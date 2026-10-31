"""Planning 模块：plan / todo / requirement 校验的规范实现。

本模块原先是对旧拼写变体模块的转发包装；现在旧模块的实现已完整
合并到此处，成为唯一的 planning 实现来源。对外接口（类、函数、常量）
与合并前保持一致。
"""

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
from .requirements import (
    RequirementState,
    RequirementKind,
    build_requirement_context,
)
from .goal import GoalState
from copy import deepcopy
from .failure_recovery import clear_failure_streak
from .plan_validation import (
    MIN_PLAN_ITEMS,
    MAX_PLAN_ITEMS,
    PlanValidationIssue,
    PlanValidationResult,
    validate_plan,
    normalize_plan_content,
    format_plan_validation_errors,
    validate_plan_verification,
    validate_requirement_references,
    validate_requirement_mapping,
    validate_requirement_coverage,
)


if TYPE_CHECKING:
    from .verification import VerificationRecord

# 最大停滞转数
MAX_STAGNANT_TURNS = 5

#最大重新计划数
MAX_REPLANS = 5



class PlanStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"


@dataclass
class PlanSnapshot:
    revision: int
    items: list[TodoItem]
    status: PlanStatus
    reason: str
    created_turn: int
    superseded_turn: int | None = None


@dataclass
class PlanState:
    revision: int = 0
    # 当前的plan
    items: list[TodoItem] = field(
        default_factory=list
    )
    # history plan
    history: list[PlanSnapshot] = field(
        default_factory=list
    )

    replan_count: int = 0



@dataclass
class FailureState:
    '''
    故障状态
    '''
    # 最后的签名
    last_signature: str | None = None
    # 连续计数
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


class TodoKind(str, Enum):
    ANALYSIS = "analysis"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"

@dataclass
class TodoItem: 
    id: int
    content: str
    status: TaskStatus = TaskStatus.PENDING
    note: str = ''
    # 该任务完成时需要什么验证证据
    required_evidence: EvidenceType | None = None

    # plan validation v2 该任务负责满足哪些用户需求
    requirement_ids: list[int] = field(
        default_factory=list
    )
    kind: TodoKind = TodoKind.ANALYSIS
    # 22 task depandency 该任务必须等待哪些前置任务
    depends_on: list[int] = field(
        default_factory=list
    )
    # 23新增：次任务服用了旧计划中的哪个任务
    reuse_from_task_id: int | None = None

class ReplanReason(str, Enum):
    # 原计划建立在错误的认识上
    ASSUMPTION_INVALID = "assumption_invalid"
    # 当前Todo走不通
    BLOCKED_TASK = "blocked_task"
    # 重试多次失败
    REPEATED_FAILURE = "repeated_failure"
    # 当前plan导致不停调查，没有进展
    STAGNATION = "stagnation"
    # 原计划如果继续执行，就会违反 Requirement
    REQUIREMENT_CONFLICT = "requirement_conflict"

@dataclass
class AgentState:
    goal: GoalState = field(default_factory=GoalState)
    plan: PlanState = field(default_factory=PlanState)
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

    @property
    def todos(self) -> list[TodoItem]:
        """向后兼容别名：todos 即当前 plan 的 items。"""
        return self.plan.items

    @todos.setter
    def todos(self, value: list[TodoItem]) -> None:
        self.plan.items = value

def create_agent_state(
    user_prompt: str,
) -> AgentState:
    """
    创建AgentState
    """
    return AgentState(
        goal=GoalState(
            original_request=user_prompt
        ),
        plan=PlanState()
    )


def format_plan(
    state: AgentState,
) -> str:

    if not state.plan.items:
        return "No plan"

    symbols = {
        TaskStatus.PENDING: "[ ]",
        TaskStatus.IN_PROGRESS: "[>]",
        TaskStatus.COMPLETED: "[v]",
        TaskStatus.BLOCKED: "[!]",
    }

    lines = []

    for todo in state.plan.items:

        line = (
            f"{symbols[todo.status]} "
            f"{todo.id}. "
            f"[{todo.kind.value}] "
            f"{todo.content}"
        )

        if todo.requirement_ids:

            requirement_text = ",".join(
                str(requirement_id)
                for requirement_id
                in todo.requirement_ids
            )

            line += (
                " "
                f"[requirements: "
                f"{requirement_text}]"
            )

        if (
            todo.required_evidence
            is not None
        ):

            line += (
                " "
                "[requires evidence: "
                f"{todo.required_evidence.value}"
                "]"
            )

        if todo.note:

            line += (
                f" - {todo.note}"
            )

        if todo.depends_on:
            deps = ",".join(
                str(dep_id)
                for dep_id in todo.depends_on
            )
            line += (
                " "
                f"[depends on: {deps}]"
            )

        lines.append(line)

    return "\n".join(lines)


def parse_plan_items(
        items: list[dict],
) -> tuple[
    list[TodoItem] | None,
    str | None
]:
    '''
    将 LLM tool arguments 转换成 Runtime TodoItem。
    返回：
    (todos, None)
    或者
    (None, error)
    '''
    if not items:
        return None, "Plan cannot be empty."

    todos: list[TodoItem] = []
    for index, item in enumerate(
        items,
        start=1
    ):
        # content
        content = item.get('content', "")
        if not isinstance(
            content,
            str,
        ): 
            return (
                None,
                (
                   f"Plan item {index}"
                   "content must be a string." 
                )
            )

        content = content.strip()
        if not content:
            return (
                None,
                (
                    f"Plan item {index} "
                    "content cannot be empty."
                )
            )

        # kind（缺省时默认 ANALYSIS）
        kind_value = item.get('kind')
        if kind_value is None:
            kind_value = TodoKind.ANALYSIS.value
        try:
            kind = TodoKind(kind_value)
        except ValueError:
            return (
                None,
                (
                    f"Invalid todo kind "
                    f"for item {index}: "
                    f"{kind_value}"
                )
            )

        # requirement_ids
        requirement_ids = item.get(
            'requirement_ids', []
        )

        if not isinstance(
            requirement_ids,
            list
        ):
            return (
                None,"requirement_ids must be a list"
            )

        if not all(
            isinstance(
                requirement_id, int,
            )
            for requirement_id in requirement_ids
        ):
            return (
                None, "All requirement_ids must be intergers."
            )

        # required_evidence
        evidence_value = item.get(
            "required_evidence", ""
        )

        required_evidence = None
        if evidence_value != "none":
            try:
                required_evidence = EvidenceType(evidence_value)
            except ValueError:
                return (
                    None, f"Invalid evidence type: {evidence_value}"
                )

        # 解析依赖任务 ID
        depends_on = item.get(
            'depends_on', []
        )

        if not isinstance(depends_on, list):
            return (
                None, "depends_on must be a list"
            )

        if not all(
            type(task_id) is int and task_id > 0 for task_id in depends_on
        ):
            return (
                None, "depends_on must contain positive integer IDs."
            )

        reuse_from_task_id = item.get(
            "reuse_from_task_id"
        )

        # TodoItem
        todos.append(
            TodoItem(
                id=index,
                content=content,
                kind=kind,
                requirement_ids=requirement_ids,
                required_evidence=required_evidence,
                depends_on=depends_on,
                reuse_from_task_id=reuse_from_task_id,
            )
        )

    return todos, None


# set_plan 从之前的list[str]改为list[dict]，其中携带了是否需要验证
def set_plan(
        state: AgentState,
        items: list[dict]
) -> ToolResult:
    """
    设置计划
    """

    # Goal 必须确定
    if not state.goal.locked:
        return ToolResult.fail(
            error=(
                "Goal must be locked "
                "before creating a plan"
            )
        )

    # Requirement必须确定
    if not state.requirements.locked:
        return ToolResult.fail(
            error=(
                "Requirements must be locked "
                "before creating a plan"
            )
        )
    # 已经有plan 
    if state.plan.revision > 0:
        return ToolResult.fail(
            error=(
                "An active plan already exists. "
                "Use replan instead of set_plan."
            )
        )

    # plan不能为空
    clean_items = [
        item
        for item in items
        if item
    ]

    if not clean_items:
        return ToolResult.fail(
            error="Plan cannot be empty."
        )

    # Plan v1
    todos, error = parse_plan_items(
        items=items
    )
    if error:
        return ToolResult.fail(
            error=error
        )

    # 将validation plan 放在todos.append(...)之后，state.plan.revision = 1 之前调用
    # 判断是不是有效的计划
    validation = validate_plan(
        state=state,
        todos=todos
    )

    if not validation.valid:
        return ToolResult.fail(
            error=format_plan_validation_errors(
                validation
            )
        )

    state.plan.revision = 1
    state.plan.items = todos

    return ToolResult(
        success=True,
        content=format_plan(state),
    )

def replan(
        state: AgentState,
        items: list[dict],
        reason: ReplanReason,
        explanation: str, # 解释
        current_turn: int,
) -> ToolResult:

    from .plan_reconciliation import reconcile_completed_tasks

    if (state.plan.replan_count >= MAX_REPLANS):
        return ToolResult.fail(
            error=(
                "Maximum replanning limit "
                "has been reached. "
                "Do not keep replacing the plan. "
                "Resolve the current blocker or "
                "mark the task blocked."
            )
        )

    # 必须有plan
    if state.plan.revision == 0:
        return ToolResult.fail(
            error=(
                "No existing plan."
                "Use set_plan first."
            )
        )

    # Goal / Requirements 必须保持锁定
    if not state.goal.locked:
        return ToolResult.fail(
            error="Cannot replan without a locked goal."
        )

    if not state.requirements.locked:
        return ToolResult.fail(
            "Cannot replan without locked requirements."
        )

    explanation = explanation.strip()

    if not explanation:
        return ToolResult.fail(
            error=(
                "Replanning requires an explanation."
            )
        )

    # 先校验并构造新计划，失败时不改变当前计划。
    if not items:
        return ToolResult.fail(
            "Replacement plan cannot be empty."
        )

    todos, error = parse_plan_items(
        items=items
    )
    if error:
        return ToolResult.fail(
            error=error
        )

    # 在有todos后，立马校验todos是否有效
    # 新 Plan 验证失败，不能把旧 Plan 搞坏。
    validation = validate_plan(
        state=state,
        todos=todos
    )

    if not validation.valid:
        return ToolResult.fail(
            error=format_plan_validation_errors(
                validation
            )
        )

    # 不允许完全相同的plan
    # old_contents = [
    #     item.content
    #     for item in state.plan.items
    # ]

    # if old_contents == [item.content for item in todos]:
    #     return ToolResult.fail(
    #         error=(
    #             "Replacement plan is identical to the current plan."
    #         )
    #     )
    old_signature = plan_signature(state.plan.items)
    new_signature = plan_signature(todos)
    if old_signature == new_signature:
        return ToolResult.fail(
            error=(
                "Replacement plan is identical to the current plan."
            )
        )

     # 对照新旧plan，看那些任务可以保留，那些需要替换
    carried_ids, reconcile_error = reconcile_completed_tasks(
        state=state,
        new_todos=todos,
    )

    if reconcile_error:
        return ToolResult.fail(
            error=reconcile_error
        )

    # 保存旧plan
    old_revision = state.plan.revision
    snapshot = PlanSnapshot(
        revision=old_revision,
        items = deepcopy(
            state.plan.items
        ),
        status=PlanStatus.SUPERSEDED,
        reason=(
            f"{reason.value}"
            f"{explanation}"
        ),
        created_turn=0,
        superseded_turn=current_turn
    )
    new_revision = old_revision + 1
    state.plan.history.append(snapshot)
    state.plan.revision = new_revision
    state.plan.items = todos
    state.plan.replan_count += 1
    # 清空所有的failure streak
    clear_failure_streak(state=state)
    # record_progress(state=state, turn=current_turn)

    return ToolResult.ok(
        content=(
            f"Plan revision "
            f"{old_revision} was superseded."
            f"Created plan revision "
            f"{new_revision}."
        )
    )

def plan_signature(
        todos: list[TodoItem],
) -> tuple:
    '''
    对todos进行签名
    '''
    return tuple(
        (
            normalize_plan_content(todo.content),
            todo.kind.value,
            tuple(sorted(todo.requirement_ids)),
            (
                todo.required_evidence.value 
                if todo.required_evidence 
                else None
            ),
            tuple(sorted(todo.depends_on)),
        )
        for todo in todos
    )

def update_task(
        state: AgentState,
        metrics: AgentMetrics,
        task_id: int,
        status: str,
        note: str | None = None,
) -> ToolResult:

    # 1. Parse status

    try:
        target_status = TaskStatus(status)
    except ValueError:
        return ToolResult.fail(
            error=f"Invalid task status: {status}"
        )

    # 2.Find task
    todo = next(
        (
            item 
            for item in state.plan.items
            if item.id == task_id
        ),
        None
    )

    if todo is None:
        return ToolResult.fail(
            error=f"Task {task_id} does not exist."
        )

    # 3.Validate
    validation = validate_task_transition(
        state=state,
        todo=todo,
        target=target_status,
    )

    if not validation.allowed:

        return ToolResult.fail(
            error=validation.error
            or "Invalid task transition."
        )

    # 4. Commit
    old_status = todo.status
    todo.status = target_status
    if note is not None:
        todo.note = note.strip()

    # #5 Progress
    # record_progress(
    #     state=state,
    #     turn=metrics.model_turns,
    # )
    return ToolResult.ok(
        content=(
            f"Task {task_id} updated: "
            f"{old_status.value} "
            f"-> {target_status.value}."
        )
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

# 上下文渲染函数已拆分到 .runtime_context，
# 停滞/进展判定函数已拆分到 .stagnation，
# 在文件末尾统一重新导出，保持对外接口不变。


# Task 状态机与证据门禁：实现已拆分到 .task_state，
# 上下文渲染函数已拆分到 .runtime_context，
# 停滞/进展判定函数已拆分到 .stagnation，
# 这里统一重新导出，保持对外接口与合并前一致。
from .task_state import (  # noqa: E402
    TASK_TRANSITIONS,
    TaskTransitionResult,
    can_transition_task,
    get_in_progress_task,
    has_other_in_progress_task,
    has_required_task_evidence,
    validate_task_transition,
)

from .runtime_context import (  # noqa: E402
    build_goal_context,
    build_plan_context,
    build_runtime_instructions,
    print_state_debug,
)

from .stagnation import (  # noqa: E402
    build_stagnation_feedback,
    is_stagnating,
    # record_progress,
    tool_caused_progress,
)

# 旧拼写变体模块已作废：实现已合并至 mini_coding_agent.planning，不再被任何代码引用。

__all__ = [
    "MIN_PLAN_ITEMS",
    "MAX_PLAN_ITEMS",
    "MAX_REPLANS",
    "PlanStatus",
    "PlanSnapshot",
    "PlanState",
    "TodoItem",
    "AgentState",
    "create_agent_state",
    "format_plan",
    "parse_plan_items",
    "set_plan",
    "replan",
    "update_task",
    "get_plan",
    "validate_plan",
    "normalize_plan_content",
    "format_plan_validation_errors",
    "validate_plan_verification",
    "validate_requirement_references",
    "validate_requirement_mapping",
    "validate_requirement_coverage",
    "TASK_TRANSITIONS",
    "TaskTransitionResult",
    "can_transition_task",
    "get_in_progress_task",
    "has_other_in_progress_task",
    "has_required_task_evidence",
    "validate_task_transition",
    "build_goal_context",
    "build_plan_context",
    "build_runtime_instructions",
    "print_state_debug",
    "build_stagnation_feedback",
    "is_stagnating",
    # "record_progress",
    "tool_caused_progress",
]


