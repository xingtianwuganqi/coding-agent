'''Task 状态机与证据门禁（非 plan 逻辑，从旧 planning 前身模块拆出）'''

from __future__ import annotations

from dataclasses import dataclass

from .evidence import has_valid_evidence
from .planning import (
    AgentState,
    TaskStatus,
    TodoItem,
)


TASK_TRANSITIONS: dict[
    TaskStatus,
    set[TaskStatus],
] = {
    TaskStatus.PENDING: {
        TaskStatus.IN_PROGRESS
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.COMPLETED,
        TaskStatus.BLOCKED
    },
    TaskStatus.BLOCKED: {
        TaskStatus.IN_PROGRESS
    },

    TaskStatus.COMPLETED: set()
}

def can_transition_task(
        current: TaskStatus,
        target: TaskStatus,
) -> bool:
    '''
    获取current是否可以转换成target
    '''
    allowed_targets = TASK_TRANSITIONS.get(
        current,
        set()
    )

    return target in allowed_targets

def get_in_progress_task(
        state: AgentState,
) -> list[TodoItem]:

    return [
        todo 
        for todo in state.plan.items
        if (
            todo.status 
            == TaskStatus.IN_PROGRESS
        )
    ]

def has_other_in_progress_task(
        state: AgentState,
        task_id: int
) -> bool:
    '''
    是否存在一个"不是当前task，而且状态为IN_PROGRESS“的 Todo
    '''
    return any(
        todo.id != task_id
        and (
            todo.status
            == TaskStatus.IN_PROGRESS
        )
        for todo in state.plan.items
    )

def has_required_task_evidence(
        state: AgentState,
        todo: TodoItem,
) -> bool:

    '''
    判断complete是否是已经完成
    '''

    required = todo.required_evidence

    if required is None:
        return True

    return has_valid_evidence(state, required)


@dataclass
class TaskTransitionResult:
    allowed: bool
    error: str | None = None


def validate_task_transition(
        state: AgentState,
        todo: TodoItem,
        target: TaskStatus,
) -> TaskTransitionResult:
    """校验单个 todo 的状态迁移是否合法。

    按顺序检查：
    1. 禁止同状态更新（current == target）；
    2. 是否符合 TASK_TRANSITIONS 定义的状态机；
    3. 是否已有其他 in_progress 任务（单活动任务约束）；
    4. 迁移到 completed 时是否具备 required_evidence 要求的证据。

    Args:
        state: 当前 Agent 状态，提供计划中所有 todo。
        todo: 待更新的计划条目。
        target: 期望迁移到的目标状态。

    Returns:
        TaskTransitionResult：allowed 表示是否允许迁移，
        error 在拒绝时给出具体原因。
    """

    current = todo.status

    #1 禁止同状态更新

    if current == target:
        return TaskTransitionResult(
            allowed=False,
            error=(
                f"Task {todo.id} is already "
                f"{current.value}."
            )
        )

    #2 State Machine

    if not can_transition_task(
        current=current,
        target=target
    ):
        return TaskTransitionResult(
            allowed=False,
            error=(
                "Invalid task transitons: "
                f"{current.value} -> "
                f"{target.value}."
            )
        )

    #3 Single IN_PROGRESS

    if (
        target == TaskStatus.IN_PROGRESS
        and has_other_in_progress_task(
            state,
            todo.id,
        )
    ): 
        return TaskTransitionResult(
            allowed=False,
            error=(
                "Another task is already "
                "in progress."
            )
        )

    # 新增 Dependency Guard
    if target == TaskStatus.IN_PROGRESS:
        unmet_dependencies = get_unmet_dependencies(
            state,
            todo
        )

        if unmet_dependencies:
            unmet_ids = [
                str(dep_id)
                for dep_id in unmet_dependencies
            ]

            return TaskTransitionResult(
                allowed=False,
                error=(
                    f"Task {todo.id} cannot be started "
                    "because it has unmet dependencies: "
                    f"{', '.join(unmet_ids)}."
                )
            )

    #4  Evidence Guard
    if (
        target == TaskStatus.COMPLETED
        and not has_required_task_evidence(
            state,
            todo
        )
    ):
        evidence_name = (
            todo.required_evidence.value 
            if todo.required_evidence
            else 'unknown'
        )

        return TaskTransitionResult(
            allowed=False,
            error=(
                f"Task {todo.id} requires "
                f"evidence '{evidence_name}' "
                "before it can be completed."
            )
        )

    return TaskTransitionResult(
        allowed=True,
    )


def get_unmet_dependencies(
        state: AgentState,
        todo: TodoItem,
) -> list[TodoItem]:
    """判断todo 任务还有哪些依赖没有完成

    Args:
        state: 当前 Agent 状态，提供计划中所有 todo。   
        todo: 当前待检查的计划条目。
    Returns:
        list[TodoItem]: 当前 todo 的所有未完成依赖任务列表。
    """
    completed_ids = {
        t.id
        for t in state.plan.items
        if t.status == TaskStatus.COMPLETED
    }

    return [
        dep_id
        for dep_id in todo.depends_on
        if dep_id not in completed_ids
    ]


def get_ready_tasks(
        state: AgentState,
) -> list[TodoItem]:
    """获取所有可以开始执行的任务列表。

    任务可以开始执行的条件：
    1. 状态为 PENDING；
    2. 所有依赖任务已完成；
    3. 如果有 required_evidence，则必须已经具备。

    Args:
        state: 当前 Agent 状态，提供计划中所有 todo。
    Returns:
        list[TodoItem]: 所有可以开始执行的任务列表。
    """
    return [
        todo
        for todo in state.plan.items
        if (
            todo.status == TaskStatus.PENDING
            and not get_unmet_dependencies(state, todo)        )
    ]