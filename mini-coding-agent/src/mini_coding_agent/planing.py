from dataclasses import dataclass, field
from enum import Enum
from .evidence import (
    EvidenceType
)
from .evidence import (
    has_valid_evidence
)
class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class TodoItem: 
    id: int
    content: str
    status: TaskStatus = TaskStatus.PENDING
    note: str = ''
    required_evidence: EvidenceType | None = None

@dataclass
class AgentState:
    todos: list[TodoItem] = field(default_factory=list)
    # evidence: set[EvidenceType] = field(
    #     default_factory=set
    # )
    evidence: dict[EvidenceType, int] = field(
            default_factory=dict
        )
    # Versioned Evidence
    workspace_revision: int = 0
    task_summary: str = ""

def format_plan(state: AgentState) -> str:
    if not state.todos:
        return "No plan"

    symbols = {
        TaskStatus.PENDING: "[ ]",
        TaskStatus.IN_PROGRESS: "[>]",
        TaskStatus.COMPLETED: "[v]",
        TaskStatus.BLOCKED: "[!]",
    }

    lines = []
    for todo in state.todos:
        line = (
            f"{symbols[todo.status]} "
            f"{todo.id}. {todo.content}"
        )

        if (
            todo.required_evidence is not None
        ):
            line += (
                " "
                "[requires: "
                f"{todo.required_evidence.value}"
                "]"
            )

        if todo.note:
            line += f" - {todo.note}"

        lines.append(line)

    return "\n".join(lines)

# set_plan 从之前的list[str]改为list[dict]，其中携带了是否需要验证
def set_plan(
        state: AgentState,
        items: list[dict]
) -> str:
    """
    设置计划
    """
    todos = []
    for index, item in enumerate(
        items,
        start=1
    ): 
        evidence_value = item.get(
            "required_evidence", "none"
        )

        required_evidence = None
        if evidence_value != "none":
            try:
                required_evidence = EvidenceType(
                    evidence_value
                )

            except ValueError:
                return (
                    f"Invalid evidence type:"
                    f"{evidence_value}"
                )

        todos.append(
            TodoItem(
                id=index,
                content=item["content"],
                required_evidence=required_evidence
            )
        )

    state.todos = todos
    return format_plan(state)

def update_task(
        state: AgentState,
        task_id: int,
        status: str,
        note: str = "",
) -> str:
    """
    更新计划
    """
    try:
        new_status = TaskStatus(status)
    except ValueError:
        return f"Invalid status: {status}"

    for todo in state.todos:
        if todo.id != task_id:
            continue

        # 如果当前状态完成了，
        # 如果这个 Todo 有证据要求：required = todo.required_evidence
        # 但runtime中没有记录required not in state.evidence
        # 直接拒绝
        if (new_status == TaskStatus.COMPLETED 
            and todo.required_evidence is not None 
            and not has_valid_evidence(
                state,
                todo.required_evidence
            )
        ):
            return (
                f"Cannot complete task "
                f"{task_id}.\n"
                f"Required evidence is missing: "
                f"{todo.required_evidence.value}"
            )
        todo.status = new_status

        if note:
            todo.note = note

        result = format_plan(state)

        print("\n--- Current Plan ---")
        print(result)

        return result

    return f"Task not found: {task_id}"


def get_plan(
        state: AgentState
) -> str:
    """
    获取计划
    """
    return format_plan(state)

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
