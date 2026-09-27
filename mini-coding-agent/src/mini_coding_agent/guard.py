import json
from .planing import (
    AgentState
)

from .agent_metrics import AgentMetrics
from .agent_trace import AgentTrace

from .requirements import guard_requirement_constraints

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
        state: AgentState,
        metrics: AgentMetrics,
        trace: AgentTrace
) -> str | None:
    '''
    工具调用守卫
    '''
    # 1.goal gate
    if name == "set_goal" and state.goal.locked:
        return (
            "RUNTIME GUARD: task requirements are already locked. "
            "Do not call set_goal again; continue the next step."
        )

    if not state.goal.locked:
        if name == "set_goal":
            return None
        return (
            "RUNTIME GUARD: "
            "the task goal has not "
            "been established yet. "
            "Call set_goal first."
        )

    # 2.Requirement gate
    if name == "set_requirements" and state.requirements.locked:
        return (
            "RUNTIME GUARD: task requirements are already locked. "
            "Do not call set_requirements again; continue the current plan."
        )

    if not state.requirements.locked:
        if name == "set_requirements":
            return None
        return (
            "RUNTIME GUARD: task requirements "
            "have not been extracted yet. "
            "Call set_requirements first and "
            "record the explicit requirements "
            "from the user's request."
        )

    # 2.Requirement constraints
    requirement_error = guard_requirement_constraints(
        name=name,
        arguments=arguments,
        state=state
    )

    if requirement_error:
        metrics.requirement_violations += 1
        trace.record(
            turn=metrics.model_turns,
            event_type="requirement_violation",
            name=name,
            detail=requirement_error
        )
        return requirement_error

    # 4.plan gate

    if state.plan.revision == 0:
        allowed_before_plan = (
            "set_goal",
            "set_requirements",
            "set_plan",
            "list_files",
            "read_file",
            "search_text",
        )

        if name not in allowed_before_plan:
            return (
                "RUNTIME GUARD: "
                "create a plan before "
                "performing task actions."
            )

    if name == "set_plan":
        if state.plan.items:
            return (
                "RUNTIME GUARD: a todo plan already exists. "
                "Do not call set_plan again; use update_task to continue it."
            )
        state.consecutive_retrieval_count = 0
        return None

    # 5.Replan只能在已有计划后
    if name == "replan" and state.plan.revision == 0:
        return (
            "RUNTIME GUARD: "
            "there is no current plan "
            "to replace"
        )

    if name in RETRIEVAL_TOOLS:
        if not state.plan.items:
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

    if not state.plan:
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
    
