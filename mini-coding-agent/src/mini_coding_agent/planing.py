from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from .evidence import (
    EvidenceType
)
from .evidence import (
    has_valid_evidence
)

from .agent_metrics import(
    AgentMetrics
)

from .tool_result import ToolResult
from .requirements import RequirementState

if TYPE_CHECKING:
    from .verification import VerificationRecord

# 最大停滞转数
MAX_STAGNANT_TURNS = 5

@dataclass
class FailureState:
    '''
    故障状态
    '''
    last_signature: str | None = None
    consecutive_count: int = 0

@dataclass
class VerificationState:
    '''
    验证状态
    '''
    records: list[VerificationRecord] = field(
        default_factory=list
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
    # Versioned Evidence, → 任何文件修改都增加
    workspace_revision: int = 0
    task_summary: str = ""
    # 预计划检查次数
    pre_plan_inspection_count: int = 0
    # 连续检索次数
    consecutive_retrieval_count: int = 0
    # 最近调用的工具
    recent_tool_calls: list[str] = field(
        default_factory=list
    )

    # 最近一次真正取得进展，是第几个 Model Turn。
    last_progress_turn: int = 0

    stagnation_warnings: int = 0

    # 记录同一个错误时不时连续失败
    failure: FailureState = field(
        default_factory=FailureState
    )

    # verification 只有代码文件修改才增加
    code_revision: int = 0
    
    changed_files: set[str] = field(
        default_factory=set
    )

    verification: VerificationState = field(
        default_factory=VerificationState
    )

    requirements: RequirementState = field(
        default_factory=RequirementState
    )


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
) -> ToolResult:
    """
    设置计划
    """
    old_plan = [
        {
            "content": todo.content,
            "required_evidence": (
                todo.required_evidence.value
                if todo.required_evidence
                else "none"
            )
        }
        for todo in state.todos
    ]
    if old_plan == items:
        return ToolResult.fail(
            error="Plan is unchanged",
        )
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
                return ToolResult(
                    success=False,
                    error=(
                        f"Invalid evidence type:"
                        f"{evidence_value}"
                    )
                )

        todos.append(
            TodoItem(
                id=index,
                content=item["content"],
                required_evidence=required_evidence
            )
        )

    state.todos = todos
    return ToolResult(
        success=True,
        content=format_plan(state),
    )

def update_task(
        state: AgentState,
        metrics: AgentMetrics,
        task_id: int,
        status: str,
        note: str = "",
) -> ToolResult:
    """
    更新计划
    """
    try:
        new_status = TaskStatus(status)
    except ValueError:
        return ToolResult(
            success=False,
            error=f"Invalid status: {status}"
        )

    for todo in state.todos:
        if todo.id != task_id:
            continue

        # 如果状态相同，不用改
        if todo.status == new_status:
            return ToolResult.fail(
                error=(
                    f"Task {task_id} is already "
                    f"{new_status.value}"
                ),
            )
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
            return ToolResult(
                success=False,
                error=(
                    f"Cannot complete task "
                    f"{task_id}.\n"
                    f"Required evidence is missing: "
                    f"{todo.required_evidence.value}"
                )
            )

        # 只有状态真正发生改变的时候才记录
        if todo.status != new_status:
            record_progress(
                state=state,
                turn=metrics.model_turns
            )
        todo.status = new_status

        if note:
            todo.note = note

        result = format_plan(state)

        print("\n--- Current Plan ---")
        print(result)

        return ToolResult(
            success=True,
            content=result,
        )

    return ToolResult(
        success=False,
        error=f"Task not found: {task_id}"
    )


def get_plan(
        state: AgentState
) -> ToolResult:
    """
    获取计划
    """
    return ToolResult(
        success=True,
        content=format_plan(state)
    )

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


def record_progress(
        state: AgentState,
        turn: int,
) -> None:
    state.last_progress_turn = turn
    state.stagnation_warnings = 0


def tool_caused_progress(
    name: str,
    result: ToolResult,
) -> bool:
    '''
    判断“这次工具调用成功了，但它到底算不算真正推进了任务”。
    '''
    if not result.success:
        return False

    progress_tools = {
        "write_file",
        "replace_text",
        "set_plan",
        "update_task",
    }

    return name in progress_tools


def is_stagnating(
        state: AgentState,
        current_turn: int,
) -> bool:
    '''
    判断是不是进入了停滞
    '''
    if not state.todos:
        return False

    stagnant_turns = (
        current_turn - state.last_progress_turn
    )

    return stagnant_turns >= MAX_STAGNANT_TURNS


def build_stagnation_feedback(
        state: AgentState,
) -> str:
    '''
    构建停滞反馈
    '''
    active_task = next(
        (
            todo 
            for todo in state.todos
            if todo.status
            == TaskStatus.IN_PROGRESS
        ),
        None
    )

    task_text = (
        active_task.content
        if active_task
        else "current task"
    )

    return (
        "RUNTIME NOTICE: progress has stalled. "
        "Several turns have passed without a "
        "meaningful state change. "
        f"Current task: {task_text}. "
        "Use the information already gathered "
        "to take the next concrete action. "
        "Do not continue investigating unless "
        "a specific missing fact blocks progress."
    )
