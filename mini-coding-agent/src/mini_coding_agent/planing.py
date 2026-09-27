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

if TYPE_CHECKING:
    from .verification import VerificationRecord

# 最大停滞转数
MAX_STAGNANT_TURNS = 5

#最大重新计划数
MAX_REPLANS = 5

MIN_PLAN_ITEMS = 2
MAX_PLAN_ITEMS = 7


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
class PlanValidationIssue:
    '''
    单个 plan验证问题
    '''
    code: str
    message: str


@dataclass
class PlanValidationResult:
    '''
    Plan 验证结果
    '''

    # 有效的
    valid: bool
    issues: list[PlanValidationIssue] = field(
        default_factory=list
    )


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
    required_evidence: EvidenceType | None = None

    # plan validation v2
    requirement_ids: list[int] = field(
        default_factory=list
    )
    kind: TodoKind = field(
        default_factory=TodoKind
    )

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
    goal: GoalState
    plan: PlanState
    # todos: list[TodoItem] = field(default_factory=list)
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

        # kind
        kind_value = item.get('kind')
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

        # TodoItem

        todos.append(
            TodoItem(
                id=index,
                content=content,
                kind=kind,
                requirement_ids=requirement_ids,
                required_evidence=required_evidence
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
        return ToolResult(
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
    old_contents = [
        item.content
        for item in state.plan.items
    ]

    if old_contents == [item.content for item in todos]:
        return ToolResult.fail(
            error=(
                "Replacement plan is identical to the current plan."
            )
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
    record_progress(state=state, turn=current_turn)

    return ToolResult.ok(
        content=(
            f"Plan revision "
            f"{old_revision} was superseded."
            f"Created plan revision "
            f"{new_revision}."
        )
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

    for todo in state.plan.items:
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
    if not state.plan.items:
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
            for todo in state.plan.items
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


def build_goal_context(
        state: AgentState
) -> str:

    if not state.goal.locked:
        return (
            "GOAL:\n"
            "Not established yet. "
        )

    return (
        "LOCKED GOAL: \n"
        f"{state.goal.objective}\n\n"
        "The goal cannot be changed "
        "during replanning"
    )


def build_plan_context(
        state: AgentState,
) -> str:

    if state.plan.revision == 0:
        return (
            "ACTIVE PLAN: \n"
            "No plan exists yet."
        )

    lines = [
        (
            "ACTIVE PLAN "
            f"[revision "
            f"{state.plan.revision}]:"
        )
    ]

    for todo in state.plan.items:
        lines.append(
            f"- [{todo.status.value}]"
            f"{todo.id}"
            f"{todo.content}"
        )

    if state.plan.replan_count > 0:
        lines.append("")
        lines.append(
            "Previous plan revisions: "
            f"{state.plan.replan_count}"
        )

    return "\n".join(lines)


def build_runtime_instructions(
        state: AgentState,
) -> str:

    parts = [
        build_goal_context(
            state
        ),
        build_requirement_context(
            state,
        ),
        build_plan_context(
            state,
        )
    ]

    return "\n\n".join(
        part 
        for part in parts
        if part
    )


def validate_plan(
        state: AgentState,
        todos: list[TodoItem]
) -> PlanValidationResult:
    '''
    对当前计划(todos)进行整体结构校验，是 Plan 层校验的统一入口。

    校验内容（按顺序执行）：
    1. 计划长度：条目数不得少于 MIN_PLAN_ITEMS，也不得多于 MAX_PLAN_ITEMS；
    2. 重复条目：按 normalize_plan_content 规范化后的内容去重，
       出现重复则报告 duplicate_plan_item；
    3. 委派给各专项校验子函数，把发现的问题累加到同一个 issues 列表：
       - validate_plan_verification：must_verify 需求是否有对应的验证证据；
       - validate_requirement_references：todo 引用的 requirement id 是否存在；
       - validate_requirement_mapping：requirement.kind 与 todo.kind 是否匹配；
       - validate_requirement_coverage：must_change / must_verify 需求是否被覆盖。

    Args:
        state: 当前 Agent 状态，提供 state.requirements 用于需求相关校验。
        todos: 待校验的计划条目列表。

    Returns:
        PlanValidationResult：valid 表示是否存在违规，
        issues 为所有校验问题的汇总（无问题时为 valid=True 且空列表）。
    '''

    issues: list[PlanValidationIssue] = []
    # 1. plan 长度：过短（少于 MIN_PLAN_ITEMS）
    if len(todos) < MIN_PLAN_ITEMS:
        issues.append(
            PlanValidationIssue(
                code="plan_too_short",
                message=(
                    "A multi-step plan should contain "
                    f"at least {MIN_PLAN_ITEMS} times."
                )
            )
        )

    # 1. plan 长度：过长（多于 MAX_PLAN_ITEMS）
    if len(todos) > MAX_PLAN_ITEMS:
        issues.append(
            PlanValidationIssue(
                code="plan_too_long",
                message=(
                    f"Plan contains {len(todos)} items."
                    f"Maximum allowed is "
                    f"{MAX_PLAN_ITEMS}."
                )

            )
        )

    # 2.重复 todo
    seen: set[str] = set()

    for todo in todos:
        normalized = normalize_plan_content(
            todo.content
        )
        if normalized in seen:
            issues.append(
                PlanValidationIssue(
                    code="duplicate_plan_item",
                    message=(
                        "plan contains duplicate item: "
                        f"{todo.content}"
                    )
                )
            )
        seen.add(normalized)


    validate_plan_verification(
        state=state,
        todos=todos,
        issues=issues
    )

    validate_requirement_references(
        state=state,
        todos=todos,
        issues=issues
    )

    validate_requirement_mapping(
        state=state,
        todos=todos,
        issues=issues
    )

    validate_requirement_coverage(
        state=state,
        todos=todos,
        issues=issues
    )

    return PlanValidationResult(
        valid= not issues,
        issues=issues
    )

def normalize_plan_content(
        content: str,
) -> str:
    '''
    标准化函数，将plan格式化
    '''
    return " ".join(
        content.strip()
        .lower()
        .split()
    )


def format_plan_validation_errors(
        result: PlanValidationResult
) -> str:

    '''
    将Plan_validation格式化
    '''
    lines = [
        "Plan validation failed: "
    ]

    for issue in result.issues:
        lines.append(
            f"-[{issue.code}] "
            f"{issue.message}"
        )

    return "\n".join(lines)


def validate_plan_verification(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue]
) -> None:
    '''
    校验计划是否覆盖了所有 must_verify 需求的验证证据。

    逻辑：
    - 收集计划中所有 todo 声明的 required_evidence，得到计划实际提供的证据集合；
    - 通过 verification_evidence_map 将需求的 verifier 映射为对应的 EvidenceType：
      "test" -> TESTS_PASSED，"diff" -> DIFF_INSPECTED；
    - 对每个 kind 为 MUST_VERIFY 的 requirement：
      - 若其 verifier 暂无对应的 EvidenceType（如 build / syntax），
        当前不支持 Plan 层校验，直接跳过；
      - 若所需证据不在计划提供的证据集合中，报告 missing_verification_step。

    Args:
        state: 当前 Agent 状态，提供 state.requirements 需求列表。
        todos: 待校验的计划条目列表，读取其 required_evidence 字段。
        issues: 输出参数，发现的问题会 append 到此列表（原地修改，无返回值）。

    Returns:
        None：校验结果通过 issues 列表返回。
    '''
    
    # 3. Verification coverage 验证范围，Requirement -> plan coverage 计划的覆盖范围是否能覆盖需求
    plan_evidence = {
        todo.required_evidence
        for todo in todos
        if todo.required_evidence is not None
    }

    verification_evidence_map = {
        "test": EvidenceType.TESTS_PASSED,
        "diff": EvidenceType.DIFF_INSPECTED
    }

    for requirement in state.requirements.items:
        if (requirement.kind != RequirementKind.MUST_VERIFY):
            continue

        expected_evidence = (
            verification_evidence_map.get(requirement.verifier)
        )
        # 当前 EvidenceType暂时不支持
        # build /syntax 的 Plan-level 验证
        if expected_evidence is None:
            continue
        
        if expected_evidence not in plan_evidence:
            issues.append(
                PlanValidationIssue(
                    code="missing_verification_step",
                    message=(
                        f"Requirement "
                        f"{requirement.id} requires"
                        f"'{requirement.verifier}' "
                        "verification, but the plan "
                        "does not include the required "
                        "verification evidence."
                    )
                )
            )

def validate_requirement_references(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue]
) -> None:
    '''
    校验 todos 中引用的 requirement id 是否真实存在。

    逻辑：
    - 先从 state.requirements.items 收集所有合法的 requirement id；
    - 遍历每个 todo 的 requirement_ids，
      若某个 id 不在合法集合中，则报告 unknown_requirement。

    注意：本函数只负责“引用是否有效”，
    引用类型是否匹配由 validate_requirement_mapping 负责。

    Args:
        state: 当前 Agent 状态，提供 state.requirements 需求列表。
        todos: 待校验的计划条目列表，读取其 requirement_ids 字段。
        issues: 输出参数，发现的问题会 append 到此列表（原地修改，无返回值）。

    Returns:
        None：校验结果通过 issues 列表返回。
    '''
    valid_requirement_ids = {
        requirement.id
        for requirement in state.requirements.items
    }

    for todo in todos:
        for requirement_id in todo.requirement_ids:
            if requirement_id not in valid_requirement_ids:
                issues.append(
                    PlanValidationIssue(
                        code="unknown_requirement",
                        message=(
                            f"Todo {todo.id} "
                            "references unknown requirement "
                            f"{requirement_id}." 
                        )
                    )
                )

def validate_requirement_mapping(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue]
) -> None:
    '''
    校验 requirement.kind 与引用它的 todo.kind 是否一一对应。

    对应关系：
    MUST_CHANGE   -> IMPLEMENTATION
    MUST_VERIFY   -> VERIFICATION
    MUST_NOT_MODIFY -> Runtime Guard（不应被指派给任何 todo）
    SOFT_CONSTRAINT -> Context / Model Responsibility（不在此校验）

    逻辑：
    - 建立 requirement id -> requirement 的索引；
    - 遍历每个 todo 引用的 requirement：
      - 引用不存在的 id 时跳过（已由 validate_requirement_references 处理）；
      - MUST_CHANGE 却对应非 implementation 的 todo，报告 requirement_kind_mismatch；
      - MUST_VERIFY 却对应非 verification 的 todo，报告 requirement_kind_mismatch；
      - MUST_NOT_MODIFY 被引用时，报告 constraint_requirement_reference；
      - SOFT_CONSTRAINT 不做类型校验。

    Args:
        state: 当前 Agent 状态，提供 state.requirements 需求列表。
        todos: 待校验的计划条目列表，读取其 kind 与 requirement_ids 字段。
        issues: 输出参数，发现的问题会 append 到此列表（原地修改，无返回值）。

    Returns:
        None：校验结果通过 issues 列表返回。
    '''

    requirement_by_id = {
        requirement.id: requirement
        for requirement in state.requirements.items
    }

    for todo in todos:
        for requirement_id in todo.requirement_ids:
            requirement = requirement_by_id.get(requirement_id)
            # unknown requirement
            # 前面已处理
            if requirement is None:
                continue

            # MUST_CHANGE
            if (
                requirement.kind == RequirementKind.MUST_CHANGE 
                and todo.kind != TodoKind.IMPLEMENTATION
            ):
                issues.append(
                    PlanValidationIssue(
                        code="requirement_kind_mismatch",
                        message=(
                            f"Requirement {requirement.id} "
                            "is must_change and must be covered by "
                            "an implementation todo."
                        )
                    )
                )

            # MUST_VERIFY
            if (
                requirement.kind == RequirementKind.MUST_VERIFY
                and todo.kind != TodoKind.VERIFICATION
            ):
                issues.append(
                    PlanValidationIssue(
                        code="requirement_kind_mismatch",
                        message=(
                            f"Requirement {requirement.id} is must_verify and "
                            "must be covered by a verification todo."

                        )
                    )
                )

            if (
                requirement.kind == RequirementKind.MUST_NOT_MODIFY
            ):
                issues.append(
                    PlanValidationIssue(
                        code="constraint_requirement_reference",
                        message=(
                            f"Requirement {requirement.id} is a runtime constraint "
                            "and should not be assigned to a todo item."
                        )
                    )
                )



def validate_requirement_coverage(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue],
) -> None:
    """
    校验计划是否覆盖了所有需要被计划覆盖的 requirement。

    逻辑：
    - 汇总所有 todo 引用的 requirement id，得到已被覆盖的集合；
    - 只检查 kind 为 MUST_CHANGE 或 MUST_VERIFY 的 requirement
      （MUST_NOT_MODIFY 由运行时强制执行，
      SOFT_CONSTRAINT 交由模型/上下文自行遵循，均无需计划覆盖）；
    - 若某个 requirement id 不在已覆盖集合中，报告 uncovered_requirement。

    Args:
        state: 当前 Agent 状态，提供 state.requirements 需求列表。
        todos: 待校验的计划条目列表，读取其 requirement_ids 字段。
        issues: 输出参数，发现的问题会 append 到此列表（原地修改，无返回值）。

    Returns:
        None：校验结果通过 issues 列表返回。
    """
    covered_requirement_ids = set()
    for todo in todos:
        covered_requirement_ids.update(
            todo.requirement_ids
        )

    for requirement in state.requirements.items:
        if requirement.kind not in {
            RequirementKind.MUST_CHANGE,
            RequirementKind.MUST_VERIFY
        }:
            continue

        if requirement.id in covered_requirement_ids:
            continue

        issues.append(
            PlanValidationIssue(
                code="uncovered_requirement",
                message=(
                    f"Requirement {requirement.id} ({requirement.kind.value}) "
                    "is not covered by any plan item"
                )
            )
        )

