from .planing import (
    AgentState,
    TaskStatus,
    TodoItem,
    has_valid_evidence
)
from enum import Enum


class CompletionStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


def get_unfinished_tasks(
        state: AgentState
) -> list[TodoItem]:
    """
    获取未完成的任务
    """
    for todo in state.todos:
        if (todo.status == TaskStatus.COMPLETED
                and todo.required_evidence is not None
                and not has_valid_evidence(
                    state=state, 
                    evidence_type=todo.required_evidence)
        ):
            todo.status = TaskStatus.PENDING
    return [
        todo 
        for todo in state.todos
        if todo.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED
        }
    ]

def has_blocked_tasks(
        state: AgentState,
) -> bool:
    """
    是否有存在一个被禁止
    """
    return any(
        todo.status == TaskStatus.BLOCKED
        for todo in state.todos
    )

def is_task_complete(
        state: AgentState
) -> bool:
    """
    是否所有的任务都完成了
    """
    get_unfinished_tasks(state)
    if not state.todos:
        return True

    return all(
        todo.status == TaskStatus.COMPLETED
        for todo in state.todos
    )

def get_completion_status(
        state: AgentState,
) -> CompletionStatus:
    """
    获取完成的状态
    """

    if not state.todos:
        return CompletionStatus.INCOMPLETE

    unfinished = get_unfinished_tasks(state)

    if unfinished:
        return CompletionStatus.INCOMPLETE

    if has_blocked_tasks(state):
        return CompletionStatus.BLOCKED

    return CompletionStatus.COMPLETE


