"""Todo plan, evidence tracking and completion status."""

import shlex
from .task_state import (
    AgentState,
    CommandResult,
    CompletionStatus,
    EvidenceType,
    TaskStatus,
    TodoItem,
)

def format_plan(state: AgentState) -> str:
    """
    格式化plan
    """
    if not state.todos:
        return "No plan."

    symbols = {
        TaskStatus.PENDING: "[]",
        TaskStatus.IN_PROGRESS: "[>]",
        TaskStatus.COMPLETED: "[v]",
        TaskStatus.BLOCKED: "[!]",
    }

    lines = []

    for todo in state.todos:
        line = (
            f"{symbols[todo.status]}"
            f"{todo.id}.{todo.content}"
        )

        if (todo.required_evidence is not None):
            line += (
                " "
                "[requires:"
                f"{todo.required_evidence.value}"
                "]"
            )

        if todo.note:
            line += f" - {todo.note}"

        lines.append(line)

    return "\n".join(lines)


def update_task(
        state: AgentState,
        task_id: int,
        status: str,
        note: str = ""
) -> str:
    """
    更新任务状态
    """
    try:
        new_status = TaskStatus(status)
    except ValueError:
        return f"Invalid status: {status}"

    for todo in state.todos:
        if todo.id != task_id:
            continue

        if (new_status == TaskStatus.COMPLETED):
            required = (todo.required_evidence)
            if required is not None and not has_valid_evidence(state, required):
                return (
                    f"Cannot complete task "
                    f"{task_id}.\n"
                    f"Required evidence "
                    f"is missing: "
                    f"{required.value}"
                )

        todo.status = new_status

        if note:
            todo.note = note

        result = format_plan(state)
        print("\n--- Current Plan ---")
        print(result)
        return result
        
    return f"Task not found:{task_id}"


# 给Agent一个 set_plan工具
def set_plan(
        state: AgentState,
        items: list[dict]
) -> str:
    todos = []
    for index, item in enumerate(items, start=1):
        content = item["content"]
        evidence_value = item.get("required_evidence","none")
        required_evicence = None
        if evidence_value != "none":
            try:
                required_evicence = EvidenceType(
                    evidence_value
                )
            except ValueError:
                return (
                    f"Invalid evidence type: "
                    f"{evidence_value}"
                )
        todos.append(
            TodoItem(
                id=index,
                content=content,
                required_evidence=required_evicence
            )
        )
    state.todos = todos
    
    return format_plan(state)


def get_plan(state: AgentState) -> str:
    return format_plan(state)


# 记录命令完成的证据
def record_command_evidence(
        state: AgentState,
        result: CommandResult,
) -> None:
    try:
        args = shlex.split(result.command)
    except ValueError:
        return

    evidence_type = None
    if args[:1] == ["pytest"] or args[:3] == ["uv", "run", "pytest"]:
        evidence_type = EvidenceType.TESTS_PASSED
    elif args[:2] == ["git", "diff"]:
        evidence_type = EvidenceType.DIFF_INSPECTED

    if evidence_type is not None:
        if result.returncode == 0:
            state.evidence[evidence_type] = state.workspace_revision
        else:
            state.evidence.pop(evidence_type, None)


# 让evidence失效
def invalidate_evidence(
        state: AgentState,
) -> None:
    state.evidence.clear()


# 改变workspace_revision
def mark_workspace_changed(
        state: AgentState
) -> None:
    state.workspace_revision += 1
    invalidate_evidence(state)
    get_unfinished_tasks(state)


# 判断Evidence是否有效
def has_valid_evidence(
        state: AgentState,
        evidence_type: EvidenceType
) -> bool:
    evidence_revision = state.evidence.get(
        evidence_type
    )

    if evidence_revision is None:
        return False
    return (
        evidence_revision == state.workspace_revision
    )


# tasks 相关
def get_unfinished_tasks(
        state: AgentState,
) -> list[TodoItem]:
    for todo in state.todos:
        if (todo.status == TaskStatus.COMPLETED
                and todo.required_evidence is not None
                and not has_valid_evidence(state, todo.required_evidence)):
            todo.status = TaskStatus.PENDING
    return [
        todo 
        for todo in state.todos
        if todo.status 
        not in {
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED
        }
    ]


def has_blocked_tasks(
        state: AgentState
) -> bool:
    return any(
        todo.status == TaskStatus.BLOCKED
        for todo in state.todos
    )


def is_task_complete(state: AgentState) -> bool:
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
    if not state.todos:
        return CompletionStatus.COMPLETE

    unfinished = get_unfinished_tasks(state)
    if unfinished:
        return CompletionStatus.INCOMPLETE

    if has_blocked_tasks(state):
        return CompletionStatus.BLOCKED

    return CompletionStatus.COMPLETE
