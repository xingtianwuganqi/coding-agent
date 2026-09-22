import json
from .planing import (
    AgentState
)

MAX_PRE_PLAN_INSPECTIONS = 4
MAX_CONSECUTIVE_RETRIEVALS = 3


RETRIEVAL_TOOLS = {
    "list_files",
    "read_file",
    "search_text",
}

ACTION_TOOLS = {
    "write_file",
    "replace_text",
    "run_command",
}

def guard_tool_call(
        name: str,
        arguments: dict,
        state: AgentState
) -> str | None:
    '''
    工具调用守卫
    '''
    if name == "set_plan":
        state.consecutive_retrieval_count = 0
        return None

    if name in RETRIEVAL_TOOLS:
        if not state.todos:
            if state.pre_plan_inspection_count >= MAX_PRE_PLAN_INSPECTIONS:
                return (
                    "RUNTIME GUARD: enough pre-plan "
                    "inspection has been performed. "
                    "Create the todo plan with set_plan."
                )
            state.pre_plan_inspection_count += 1
            return None

        if state.consecutive_retrieval_count >= MAX_CONSECUTIVE_RETRIEVALS:
            return (
                "RUNTIME GUARD: too many consecutive inspections. "
                "Continue the current plan by modifying code, running a command, "
                "updating the task, or marking it blocked with a reason."
            )
        state.consecutive_retrieval_count += 1
        return None

    if name in ACTION_TOOLS or name == "update_task":
        state.consecutive_retrieval_count = 0

    if not state.todos:
        return (
            "RUNTIME GUARD: no plan exists yet. "
            "Create a todo plan with set_plan "
            "before executing this tool."
        )

    return None

def tool_signature(
        name: str,
        arguments: dict,
        state: AgentState,
) -> str:
    args = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
    )

    return (
        f"{state.workspace_revision}"
        f"{name}"
        f"{args}"
    )

def guard_recent_tool_call(
        name: str,
        arguments: dict,
        state: AgentState,
) -> str| None:
    if name in RETRIEVAL_TOOLS:
        signature = tool_signature(
            name=name,
            arguments=arguments,
            state=state
        )
        if signature in state.recent_tool_calls[-8:]:
            return (
                "RUNTIME GUARD: duplicate retrieval blocked. "
                "This exact content was already inspected. "
                "Use the previous result or continue the plan."
            )
        state.recent_tool_calls.append(signature)
    return None
    