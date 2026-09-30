'''Task 状态机与证据门禁（非 plan/pl 逻辑，从 planing.py 拆出）'''

from __future__ import annotations

from dataclasses import dataclass

from .evidence import has_valid_evidence
from .planing import (
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
