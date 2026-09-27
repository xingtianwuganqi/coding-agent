from dataclasses import dataclass
from .tool_result import ToolResult

@dataclass
class GoalState: 
    original_request: str
    objective: str = ""
    locked: bool = False


def set_goal(
        state,
        objective: str,
) -> ToolResult:

    objective = objective.strip()

    # Goal 不能重复覆盖
    if state.goal.locked:
        return ToolResult.fail(
            error=(
                "Goal is already locked "
                "and cannot be replaced." 
            )
        )

    # Goal不能为空

    if not objective:
        return ToolResult.fail(
            error="Goal cannot be empty."
        )

    # 保存

    state.goal.objective = objective

    state.goal.locked = True

    return ToolResult.ok(
        content=f"Goal locked: {objective}",
    )