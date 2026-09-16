"""Context building, history summarization and token budgeting."""

import json
from .model_api import call_model
from .config import (
    KEEP_RECENT_TURNS,
    MAX_CONTEXT_TOKENS,
    MAX_CONTEXT_TURNS,
    MAX_TOOL_OUTPUT_CHARS,
    MAX_SUMMARY_CHARS,
    RESERVED_OUTPUT_TOKENS,
    SAFETY_MARGIN_TOKENS,
    SYSTEM_PROMPT,
)
from .planning import format_plan
from .task_state import AgentState
from .tools.schemas import TOOLS

def build_model_input(
        task: str,
        history_turns: list[list],
        state: AgentState
) -> list:
    """
    构建当前模型的上下文
    """
    input_items = [
        {
            "role": "user",
            "content": task,
        }
    ]

    if state.task_summary:
        input_items.append(
            {
                "role": "user",
                "content": (
                    "TASK MEMORY SUMMARY: \n"
                    f"{state.task_summary}"
                )
            }
        )

    # 最近内容的预算
    # recent_budget = get_recent_context_budget(
    #     task,
    #     state
    # )

    # selected_turns = select_recent_turns(
    #     history_turns,
    #     recent_budget
    # )


    for turn in history_turns:
        input_items.extend(turn)

    return input_items


# 格式化runtime的state
def format_runtime_state(
        state: AgentState
) -> str:
    lines = []
    lines.append(
        f"Workspace revision:"
        f"{state.workspace_revision}"
    )
    lines.append("")
    lines.append("Current plan:")
    lines.append(
        format_plan(state)
    )
    lines.append("")
    lines.append("Evidence:")

    if not state.evidence:
        lines.append("(none)")
    else:
        for evidence_type, revision in state.evidence.items():
            valid = (
                revision == state.workspace_revision
            )
            lines.append(
                f"- {evidence_type.value}: "
                f"revision={revision}, "
                f"valid={valid}"
            )

    return "\n".join(lines)


# 将turn转成str
def turn_to_text(
        turn: list
) -> str:
    lines = []
    for item in turn:
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "function_call_output":
                lines.append(
                    "TOOL RESULT:\n"
                    f"{item.get('output','')}"
                )
            elif item.get("role") == "tool":
                lines.append(f"TOOL RESULT:\n{item.get('content', '')}")
            elif item.get("role") == "assistant":
                if item.get("content"):
                    lines.append(f"ASSISTANT:\n{item['content']}")
                for call in item.get("tool_calls", []):
                    function = call["function"]
                    lines.append(f"TOOL CALL:\n{function['name']}({function['arguments']})")
            elif item.get("role") == "user":
                lines.append(
                    "RUNTIME FEEDBACK:\n"
                    f"{item.get('content','')}"
                )
            continue

        item_type = getattr(
            item, 
            "type",
            None,
        )

        if item_type == "function_call":
            lines.append(
                "TOOL CALL:\n"
                f"{item.name}"
                f"({item.arguments})"
            )
        elif item_type == "message":
            text_parts = []
            for content in item.content:
                text = getattr(
                    content,
                    "text",
                    None,
                )

                if text:
                    text_parts.append(text)

            if text_parts:
                lines.append(
                    "ASSISTANT:\n"
                    + "\n".join(text_parts)
                )

    return "\n\n".join(lines)


# 把多个turn拼起来
def history_to_text(
        turns: list[list]
) -> str:
    parts = []
    for index, turn in enumerate(turns, start=1):
        text = turn_to_text(turn)
        parts.append(
            f"---Old Turn {index} ---\n"
            f"{text}"
        )

    return "\n\n".join(parts)


# 总结历史总结
def summarize_history(
        old_summary: str,
        history_text: str,
) -> str:
    prompt = f"""
    You maintain compact working memory for a coding agent.

    Create an UPDATED task summary using:

    1. the existing summary
    2. the new old-history segment

    Keep ONLY information that is likely to matter later:

    - user requirements and constraints
    - confirmed architectural decisions
    - confirmed bug causes
    - important files/components already changed
    - unresolved blockers
    - important facts needed for future implementation

    Do NOT preserve:

    - source code verbatim
    - command output
    - git diff contents
    - routine tool calls
    - repeated facts
    - old investigation details that can be re-read from the workspace

    The summary MUST be concise.
    Prefer bullet points.
    Do not exceed about 2000 characters.

    Existing summary:

    {old_summary or "(none)"}

    History to compress:

    {history_text}

    Return only the updated summary.
    """

    message = call_model(
        [{"role": "user", "content": prompt}],
        thinking=False,
    )
    summary = (message.content or "").strip()
    return summary[:MAX_SUMMARY_CHARS]



# 压缩历史数据
def compress_history(
    task: str,
    state: AgentState,
    history_turns: list[list],
) -> None:
    # input_budget = get_input_token_budget()
    # fixed_tokens = estimate_fixed_context_tokens(task, state)
    # recent_budget = max(input_budget - fixed_tokens, 0)

    # selected_turns = select_recent_turns(
    #     history_turns,
    #     recent_budget,
    # )
    if len(history_turns) < MAX_CONTEXT_TURNS:
        return 

    compress_count = len(history_turns) - KEEP_RECENT_TURNS
    if compress_count <= 0:
        return

    old_turns = history_turns[:compress_count]
    history_text = history_to_text(old_turns)
    new_summary = summarize_history(
        state.task_summary,
        history_text,
    )

    state.task_summary = new_summary
    del history_turns[:compress_count]


# 给llm前 12000字符
def truncate_text(
        text: str,
        max_chars: int = MAX_TOOL_OUTPUT_CHARS
) -> str:
    if len(text) <= max_chars:
        return text

    return (
        text[:max_chars]
        + "\n\n"
        + "[OUTPUT TRUNCATED]"
    )


def truncate_tail(
        text: str,
        max_chars: int = MAX_TOOL_OUTPUT_CHARS
) -> str:
    if len(text) <= max_chars:
        return text

    return (
        "[OUTPUT TRUNCATED - showing tail]\n\n"
        + text[-max_chars:]
    )


#获取输入token
def get_input_token_budget() -> int:
    return (
        MAX_CONTEXT_TOKENS 
        - RESERVED_OUTPUT_TOKENS
        - SAFETY_MARGIN_TOKENS
    )


# token估算方法(Runtime 的保守预算估算器)
def estimate_tokens(
        text: str
) -> int:
    if not text:
        return 0

    return len(text)


# 估算tools的token
def estimate_tools_tokens() -> int:
    tools_text = json.dumps(
        TOOLS,
        ensure_ascii=False,
    )
    return estimate_tokens(
        tools_text
    )


# 估算固定Context成本
def estimate_fixed_context_tokens(
        task: str,
        state: AgentState
) -> int:
    runtime_state = format_runtime_state(
        state=state
    )

    summary = (
        state.task_summary
        or ""
    )

    total = 0
    total += estimate_tokens(
        SYSTEM_PROMPT
    )

    total += estimate_tokens(
        task
    )

    total += estimate_tokens(
        runtime_state
    )

    total += estimate_tokens(
        summary
    )

    total += estimate_tools_tokens()

    return total


# 开始计算每个Turn的成本
def estimate_turn_tokens(
        turn: list
) -> int:
    text = turn_to_text(
        turn
    )
    return estimate_tokens(
        text
    )


# 按从最新到最后的turn计算token
def select_recent_turns(
        history_turns: list[list],
        token_budget: int
) -> list[list]:
    selected_turns = []
    used_tokens = 0
    for turn in reversed(
        history_turns
    ):
        turn_tokens = (
            estimate_turn_tokens(
                turn
            )
        )

        if used_tokens + turn_tokens > token_budget:
            break

        selected_turns.append(
            turn
        )

        used_tokens += (
            turn_tokens
        )

    selected_turns.reverse()
    return selected_turns


# 计算Recent Context budget
def get_recent_context_budget(
        task: str,
        state: AgentState
) -> int:
    input_budget = get_input_token_budget()
    fixed_tokens = estimate_fixed_context_tokens(
        task,
        state,
    )

    remaining = (
        input_budget
        - fixed_tokens
    )

    return max(remaining, 0)


def print_fixed_context_breakdown(
    task: str,
    state: AgentState,
) -> None:
    runtime_state = format_runtime_state(state)
    summary = state.task_summary or ""

    values = {
        "system_prompt": estimate_tokens(SYSTEM_PROMPT),
        "tools": estimate_tools_tokens(),
        "task": estimate_tokens(task),
        "runtime_state": estimate_tokens(runtime_state),
        "summary": estimate_tokens(summary),
    }

    print("\n--- Fixed Context Breakdown ---")

    for name, tokens in values.items():
        print(f"{name}: {tokens}")

    print(
        "total:",
        sum(values.values()),
    )


def print_context_debug(
    task: str,
    state: AgentState,
    history_turns: list[list],
) -> None:
    input_budget = (
        get_input_token_budget()
    )

    fixed_tokens = (
        estimate_fixed_context_tokens(
            task,
            state,
        )
    )

    recent_budget = max(
        input_budget - fixed_tokens,
        0,
    )

    selected_turns = (
        select_recent_turns(
            history_turns,
            recent_budget,
        )
    )

    selected_tokens = sum(
        estimate_turn_tokens(turn)
        for turn in selected_turns
    )

    print(
        "\n--- Context Budget ---"
    )

    print(
        f"Max context: "
        f"{MAX_CONTEXT_TOKENS}"
    )

    print(
        f"Reserved output: "
        f"{RESERVED_OUTPUT_TOKENS}"
    )

    print(
        f"Safety margin: "
        f"{SAFETY_MARGIN_TOKENS}"
    )

    print(
        f"Input budget: "
        f"{input_budget}"
    )

    print(
        f"Fixed context: "
        f"{fixed_tokens}"
    )

    print(
        f"Recent budget: "
        f"{recent_budget}"
    )

    print(
        f"History turns stored: "
        f"{len(history_turns)}"
    )

    print(
        f"History turns selected: "
        f"{len(selected_turns)}"
    )

    print(
        f"Selected turn tokens: "
        f"{selected_tokens}"
    )


# 是否是有效的预算
def validate_context_budget(
    task: str,
    state: AgentState,
) -> str | None:
    input_budget = (
        get_input_token_budget()
    )

    fixed_tokens = (
        estimate_fixed_context_tokens(
            task,
            state,
        )
    )

    if fixed_tokens > input_budget:
        return (
            "Fixed context exceeds "
            "the available input budget."
        )

    return None
