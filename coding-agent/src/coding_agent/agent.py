"""Agent runtime loop (model calls, tool calls, completion)."""

import json
from .model_api import call_model
from .config import (
    MAX_MODEL_TURNS,
    MAX_TOOL_CALLS,
    SYSTEM_PROMPT,
)
from .context import (
    build_model_input,
    compress_history,
    format_runtime_state,
    print_fixed_context_breakdown,
    validate_context_budget,
)
from .planning import (
    format_plan,
    get_completion_status,
)
from .task_state import (
    AgentState,
    CompletionStatus,
)
from .tools.executor import execute_tool
from .tools.schemas import TOOLS

# 打印出workspacerevision状态
def print_state_debug(
    state: AgentState,
) -> None:
    print("\n--- Runtime State ---")

    print(
        "Workspace revision:",
        state.workspace_revision,
    )

    print("Evidence:")

    if not state.evidence:
        print("  (none)")
        return

    for evidence_type, revision in (
        state.evidence.items()
    ):
        valid = (
            revision
            == state.workspace_revision
        )

        print(
            f"  {evidence_type.value}: "
            f"revision={revision}, "
            f"valid={valid}"
        )


# 运行agent
def run_agent(task: str) -> str:
    state = AgentState()
    allow_blocked_final = False
    # input_items = [
    #     {
    #         "role": "user",
    #         "content": task,
    #     }
    # ]
    history_turns: list[list] = []
    #-----记忆系统
    # 上下文Context = Recent Turns + Runtime state + Task Summary
    # Recent Turns: 最近几轮具体发生了什么
    # Runtime state: 当前做到哪、Evidence、Revision
    # Task Summary: 过去留下来的结论

    model_turns = 0
    tool_call_count = 0

    while True:
        if model_turns > MAX_MODEL_TURNS:
            return (
                "Agent Stopped:"
                "maxinum model turns reached."
            )
        model_turns += 1
        print(f"\n--- Agent turn {model_turns} ---")

        budget_error = validate_context_budget(
            task,
            state
        )

        if budget_error:
            return (
                f"Agent stopped:"
                f"{budget_error}"
            )

        print_fixed_context_breakdown(
            task=task,
            state=state
        )

        # 压缩上下文
        compress_history(
            task=task,
            state=state,
            history_turns=history_turns,
        )

        # print_context_debug(
        #     task,
        #     state,
        #     history_turns
        # )

        
        # -------------------------
        # 1. 构建有限长度 Context
        # -------------------------
        model_input = build_model_input(
            task=task, 
            history_turns=history_turns,
            state=state
        )

        # 每次调用模型时，动态生成最新的RuntimeState
        # -------------------------
        # 2. 注入最新 Runtime State
        # -------------------------
        runtime_instructions = (
            SYSTEM_PROMPT
            + "\n\n"
            + "CURRENT RUNTIME STATE:\n"
            + format_runtime_state(state)
        )

        message = call_model(
            model_input,
            instructions=runtime_instructions,
            tools=TOOLS,
        )
        # Keep reasoning_content and tool calls intact for GLM's next turn.
        turn_items = [message.model_dump(mode="json", exclude_none=True)]
        print(json.dumps(turn_items, ensure_ascii=False, indent=2))
        tool_calls = message.tool_calls or []

        # 判断任务都完成了才结束
        # -------------------------
        # 4. 模型想结束
        # -------------------------

        if not tool_calls:
            completion_status = get_completion_status(
                state=state
            )

            if completion_status == CompletionStatus.COMPLETE:
                return message.content or ""

            if completion_status == CompletionStatus.BLOCKED and allow_blocked_final:
                return message.content or ""

            if completion_status == CompletionStatus.INCOMPLETE:
                runtime_feedback = {
                    "role": "user",
                    "content": (
                        "You attempted to finish, "
                        "but the task plan is "
                        "not complete.\n\n"
                        f"{format_plan(state)}\n\n"
                        "Continue working on "
                        "the unfinished tasks."
                    )
                }

                turn_items.append(runtime_feedback)
                history_turns.append(turn_items)
                
                continue

            if completion_status == CompletionStatus.BLOCKED:
                allow_blocked_final = True
                runtime_feedback = {
                    "role": "user",
                    "content": (
                        "Some tasks are blocked.\n\n"
                        f"{format_plan(state)}\n\n"
                        "Provide a final answer "
                        "explaining completed work "
                        "and blocked tasks."
                    ),
                }

                turn_items.append(
                    runtime_feedback
                )
                history_turns.append(
                    turn_items
                )
                
                continue

        for call in tool_calls:

            # print_state_debug(state)

            tool_call_count += 1
            if tool_call_count > MAX_TOOL_CALLS:
                return (
                    "Agent stopped:"
                    "maximum tool calls reached"
                )
            arguments = json.loads(call.function.arguments)

            print(f"Tool call: {call.function.name}")
            print(f'args:{arguments}')

            result = execute_tool(
                call.function.name, 
                arguments,
                state
            )

            print(f"Tool result: {result}")

            tool_output = {
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            }
            turn_items.append(
                tool_output
            )
        # -------------------------
        # 6. 保存完整的这一轮
        # -------------------------

        history_turns.append(
            turn_items
        )
