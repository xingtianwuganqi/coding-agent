
MAX_TOOL_OUTPUT_CHARS = 12_000

# 最大上下文 token数
MAX_CONTEXT_TOKENS = 30_000
# 模型最大输出token
RESERVED_OUTPUT_TOKENS = 6000
# 安全空间
SAFETY_MARGIN_TOKENS = 2000
# 最大总结字符
MAX_SUMMARY_CHARS = 3000

# 保留完整的最近工具调用链，并在需要摘要时预留后续 turn 的空间。
MIN_RETAINED_TURNS = 2
COMPRESS_BATCH_TURNS = 3

import math
from .model_api import (
    call_model
)

from .planing import (
    AgentState,
    format_plan
)

from .agent_metrics import AgentMetrics
from .agent_trace import AgentTrace
import json

def build_model_input(
        system_prompt: str,
        tools: list[dict],
        task: str,
        history_turns: list[list],
        state: AgentState
) -> list:
    """
    组装任务、摘要和全部保留历史；预算筛选由 compress_history 完成。
    """
    input_items = [
        {
            "role": "user",
            "content": task
        }
    ]
    if state.task_summary:
        input_items.append(
            {
                "role": "user",
                "content": (
                    "TASK MEMORY SUMMARY:\n"
                    f"{state.task_summary}"
                ),
            }
        )

    for turn in history_turns:
        input_items.extend(turn)

    return input_items

def format_runtime_state(
        state: AgentState
) -> str:
    """
    格式化runtime state, 用来传给llm
    
    Workspace revision: 4

    Current plan:

    [x] 1. Inspect code
    [x] 2. Fix bug
    [>] 3. Run tests [requires: tests_passed]
    [ ] 4. Inspect diff [requires: diff_inspected]

    Evidence:

    - tests_passed: revision=3, valid=False
    """
    lines = []
    lines.append(
        f"Workspace revision"
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
        lines.append("none")

    else:
        for evidence_type, revision in state.evidence.items():
            valid = revision == state.workspace_revision

            lines.append(
                f"- {evidence_type.value}"
                f"revision={revision}"
                f"valid={valid}"
            )

    return "\n".join(lines)

def summarize_history(
    old_summary: str,
    history_text: str,
) -> str:
    """
    将之前的history_turns总结成str
    """
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
    Do not exceed about 1200 characters.

    Existing summary:

    {old_summary or "(none)"}

    History to compress:

    {history_text}

    Return only the updated summary.
    """

    # response = client.responses.create(
    #     model=MODEL,
    #     input=prompt,
    # )

    message = call_model(
        messages=[
            {"role": "user", "content": prompt},
        ],
        thinking=False,
    )

    summary = (message.content or "").strip()
    if not summary:
        raise ValueError("history is None")
    if len(summary) > MAX_SUMMARY_CHARS:
            summary = summary[:MAX_SUMMARY_CHARS]
    return summary

def turn_to_text(
    turn: list,
) -> str:
    """
    将一轮turn转成str
    """
    lines = []

    for item in turn:
        if isinstance(item, dict):
            item_type = item.get("type")

            if item_type == "function_call_output":
                lines.append(
                    "TOOL RESULT:\n"
                    f"{item.get('output', '')}"
                )

            elif item.get("role") == "assistant":
                if item.get("reasoning_content"):
                    lines.append(f"REASONING:\n{item['reasoning_content']}")
                if item.get("content"):
                    lines.append(f"ASSISTANT:\n{item['content']}")
                for call in item.get("tool_calls") or []:
                    function = call["function"]
                    lines.append(
                        f"TOOL CALL [{call.get('id', '')}]:\n"
                        f"{function['name']}({function['arguments']})"
                    )
            elif item.get("role") == "tool":
                lines.append(
                    f"TOOL RESULT [{item.get('tool_call_id', '')}]:\n"
                    f"{item.get('content') or ''}"
                )
            elif item.get("role") == "user":
                lines.append(
                    "RUNTIME FEEDBACK:\n"
                    f"{item.get('content', '')}"
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


def history_to_text(
    turns: list[list],
) -> str:
    """
    将history转成text
    """
    parts = []

    for index, turn in enumerate(
        turns,
        start=1,
    ):
        text = turn_to_text(turn)

        parts.append(
            f"--- Old Turn {index} ---\n"
            f"{text}"
        )

    return "\n\n".join(parts)


def compress_history(
    system_prompt: str,
    tools: list[dict],
    task: str,
    state: AgentState,
    history_turns: list[list],
    metrics: AgentMetrics,
    trace: AgentTrace
) -> None:
    """Compress old turns, allowing the latest whole turn to exceed the estimate."""
    metrics.compression_checks += 1
    compression_pass = 0
    while True:
        compression_pass += 1
        fixed_tokens = estimate_fixed_context_tokens(
            system_prompt, tools, task, state
        )
        input_budget = get_input_token_budget()
        recent_budget = max(input_budget - fixed_tokens, 0)
        turn_sizes = [estimate_turn_tokens(turn) for turn in history_turns]
        print(f"\n--- Compression check {compression_pass} (estimated tokens) ---")
        print(f"Input budget: {input_budget}")
        print(f"Fixed context before selection: {fixed_tokens}")
        print(f"Recent budget before selection: {recent_budget}")
        print(f"Summary before selection: {estimate_tokens(state.task_summary or '')}")
        print(f"History turns before selection: {len(history_turns)}")
        print(f"Turn sizes (oldest -> newest): {turn_sizes}")
        print(f"Total history tokens: {sum(turn_sizes)}", flush=True)
        if not history_turns:
            print("Selection is empty: no raw history remains (or no history yet).", flush=True)
            if fixed_tokens > input_budget:
                raise ValueError("固定上下文超出预算，请调整预算或缩短提示词、工具定义和摘要")
            return

        selected_turns = select_recent_turns(
            history_turns, recent_budget
        )
        required_compress_count = len(history_turns) - len(selected_turns)
        max_compress_count = max(
            0,
            len(history_turns) - min(MIN_RETAINED_TURNS, len(history_turns)),
        )
        compress_count = required_compress_count
        if compress_count:
            compress_count = min(
                max_compress_count,
                max(compress_count, COMPRESS_BATCH_TURNS),
            )
        retained_turns = history_turns[compress_count:]
        print(f"Turns selected by budget: {len(selected_turns)}")
        print(f"Turns retained after compression: {len(retained_turns)}")
        print(f"Selected turn tokens: {sum(turn_sizes[compress_count:])}")
        print(f"Turns to compress: {compress_count}")
        if required_compress_count:
            print(
                f"Budget requires compressing {required_compress_count} turn(s); "
                f"compressing {compress_count} turn(s) to create a batch buffer."
            )
        if compress_count == 0:
            if fixed_tokens > input_budget:
                raise ValueError("固定上下文超出预算")
            if sum(turn_sizes) > recent_budget:
                print("Compression stopped: keeping the latest turn despite estimated budget overflow.", flush=True)
            else:
                print("No compression needed: all raw history fits.", flush=True)
            return

        history_text = history_to_text(history_turns[:compress_count])
        print(f"Summarizing {compress_count} oldest turns...", flush=True)
        new_summary = summarize_history(state.task_summary, history_text)

        if not new_summary.strip():
            raise ValueError("摘要为空，保留原历史")

        # 记录一次压缩
        metrics.context_compressions += 1

        #记录
        trace.record(
            turn=metrics.model_turns,
            event_type="compression",
            name="compress_history",
            detail=(
                f"before_selected_turns = {selected_turns}, "
                f"summarized_budget={estimate_tokens(new_summary)}"
            ),
        )

        # Delete only after summarization succeeds, then recheck the new budget.
        state.task_summary = new_summary
        del history_turns[:compress_count]
        print(f"Summary after compression: {estimate_tokens(new_summary)} estimated tokens")
        print(f"History turns after compression: {len(history_turns)}")
        print("Rechecking budget with the new summary.", flush=True)


def truncate_text(
        text: str,
        max_chars: int = MAX_TOOL_OUTPUT_CHARS
) -> str:
    '''
    截断文字前段部分
    比如截断 Tool Result
    '''
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
    '''
    截取后段部分

    '''
    if len(text) <= max_chars:
        return text

    return (
        "[OUTPUT TRUNCATED - showing tail]\n\n"
        + text[-max_chars:]
    )
    

def estimate_tokens(text: str) -> int:
    if not text:
        return 0

    cjk_chars = sum(
        1
        for char in text
        if (
            "\u3400" <= char <= "\u4dbf"
            or "\u4e00" <= char <= "\u9fff"
            or "\uf900" <= char <= "\ufaff"
        )
    )

    non_cjk_chars = (
        len(text) - cjk_chars
    )

    return (
        cjk_chars
        + math.ceil(non_cjk_chars / 3)
    )


def get_input_token_budget() -> int:
    '''
    获取输入token数
    '''
    return (
        MAX_CONTEXT_TOKENS
        - RESERVED_OUTPUT_TOKENS
        - SAFETY_MARGIN_TOKENS
    )

def estimate_tools_tokens(
        tools: list[dict]
) -> int:
    """
    计算TOOLS的TOKEN
    """
    tools_text = json.dumps(
        tools,
        ensure_ascii=False,
    )

    return estimate_tokens(
        tools_text
    )


def estimate_fixed_context_tokens(
        system_prompt: str,
        tools: list[dict],
        task: str,
        state: AgentState
) -> int:

    """
    计算所有静态的token，包括
    TASK
    SYSTEM_PROMPT
    TOOLS
    SUMMARY
    RUNTIME_STATE
    """
    
    runtime_state = format_runtime_state(
        state=state
    )

    summary = state.task_summary or ''

    total = 0
    total += estimate_tokens(
        system_prompt
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

    total += estimate_tools_tokens(tools=tools)

    return total

def estimate_turn_tokens(
        turn: list,
) -> int:
    '''
    计算每个turn占用的token
    '''
    text = turn_to_text(
        turn
    )
    return estimate_tokens(
        text=text
    )


def select_recent_turns(
        history_turns: list[list],
        token_budget: int,
) -> list[list]:

    '''
    按token余量计算可以从中获取到多少个turn items
    '''

    selected_turns = []
    used_tokens = 0

    for turn in reversed(
        history_turns
    ):
        turn_tokens = estimate_turn_tokens(
            turn=turn
        )

        if used_tokens + turn_tokens > token_budget:
            break

        selected_turns.append(
            turn
        )

        used_tokens += turn_tokens

    selected_turns.reverse()
    minimum_turns = min(MIN_RETAINED_TURNS, len(history_turns))
    # 保留完整的最近工具调用链，避免拆散 assistant tool_calls 与 tool 结果。
    if len(selected_turns) < minimum_turns:
        retained_turns = history_turns[-minimum_turns:]
        retained_tokens = sum(
            estimate_turn_tokens(turn)
            for turn in retained_turns
        )
        print(
            f"WARNING: keeping the latest {minimum_turns} turn(s) "
            f"({retained_tokens} estimated tokens); recent budget is {token_budget}, "
            f"overflow is {retained_tokens - token_budget}. "
            "The API may reject this request.",
            flush=True,
        )
        return retained_turns
    return selected_turns

def get_recent_context_budget(
        system_prompt: str,
        tools: list[dict],
        task: str,
        state: AgentState
) -> int:
    '''
    获取recent_context的token用量
    输入总量减去静态的量，就是可以给recent_context分配的量
    '''

    input_budget = get_input_token_budget()

    fixed_tokens = estimate_fixed_context_tokens(
        system_prompt=system_prompt,
        tools=tools,
        task=task,
        state=state
    )

    remaining = input_budget - fixed_tokens
    return max(
        remaining,
        0
    )


def print_context_debug(
    system_prompt: str,
    tools: list[dict],
    task: str,
    state: AgentState,
    history_turns: list[list],
) -> None:
    '''
    打印token的占用量
    '''
    input_budget = (
        get_input_token_budget()
    )

    fixed_tokens = (
        estimate_fixed_context_tokens(
            system_prompt,
            tools,
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

def get_fixed_context_breakdown(
    system_prompt: str,
    tools: list[dict],
    task: str,
    state: AgentState,
) -> dict[str, int]:
    runtime_state = format_runtime_state(state)

    summary = state.task_summary or ""

    return {
        "system_prompt": estimate_tokens(
            system_prompt
        ),
        "tools": estimate_tools_tokens(tools=tools),
        "task": estimate_tokens(task),
        "runtime_state": estimate_tokens(
            runtime_state
        ),
        "summary": estimate_tokens(
            summary
        ),
    }

def print_fixed_context_breakdown(
    system_prompt: str,
    tools: list[dict],
    task: str,
    state: AgentState,
) -> None:
    breakdown = (
        get_fixed_context_breakdown(
            system_prompt,
            tools,
            task,
            state,
        )
    )

    print(
        "\n--- Fixed Context Breakdown ---"
    )

    for name, tokens in (
        breakdown.items()
    ):
        print(
            f"{name}: {tokens}"
        )

    print(
        "total:",
        sum(breakdown.values()),
    )
