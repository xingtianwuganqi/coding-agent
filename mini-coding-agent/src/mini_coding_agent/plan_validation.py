"""Plan validation logic extracted from planning.py.

本模块承载 Plan 层的整体结构校验逻辑（validate_plan 及其专项子校验、
结果格式化），以降低 planning.py 的逻辑复杂度。planning.py 通过
重新导出这些名称保持对外接口不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .evidence import EvidenceType
from .requirements import RequirementKind

if TYPE_CHECKING:
    from .planning import AgentState, TodoItem


MIN_PLAN_ITEMS = 2
MAX_PLAN_ITEMS = 7


@dataclass
class PlanValidationIssue:
    code: str
    message: str


@dataclass
class PlanValidationResult:
    valid: bool
    issues: list[PlanValidationIssue]


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

    # 验证plan是否覆盖了所有 must_verify 需求的验证证据
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

    # 验证计划的依赖图是否存在环
    validate_dependency_graph(
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

        # 如果计划中没有提供所需的验证证据，则报告 missing_verification_step。
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
    from .planning import TodoKind

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
                            f"Requirement "
                            f"{requirement.id} is must_change"
                            ", but todo {todo.id} is not"
                            " an implementation task."
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
                            f"Requirement "
                            f"{requirement.id} is must_verify"
                            ", but todo {todo.id} is not"
                            " a verification task."
                        )
                    )
                )

            # MUST_NOT_MODIFY 不应被指派给任何 todo
            if (
                requirement.kind
                == RequirementKind.MUST_NOT_MODIFY
            ):
                issues.append(
                    PlanValidationIssue(
                        code="constraint_requirement_reference",
                        message=(
                            f"Todo {todo.id} references "
                            f"requirement {requirement.id}, "
                            "which is must_not_modify. "
                            "must_not_modify requirements are "
                            "enforced by the Runtime Guard and "
                            "should not be assigned to a todo."
                        )
                    )
                )


def validate_requirement_coverage(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue]
) -> None:
    '''
    校验每个可机械执行的 requirement 是否至少被一个 todo 覆盖。

    逻辑：
    - 收集所有 todo 引用的 requirement id 集合；
    - 遍历 state.requirements.items：
      - MUST_NOT_MODIFY / SOFT_CONSTRAINT 由 Runtime Guard 或模型责任保证，
        不要求 plan 覆盖，直接跳过；
      - MUST_CHANGE / MUST_VERIFY 若未被任何 todo 引用，
        报告 uncovered_requirement。

    Args:
        state: 当前 Agent 状态，提供 state.requirements 需求列表。
        todos: 待校验的计划条目列表，读取其 requirement_ids 字段。
        issues: 输出参数，发现的问题会 append 到此列表（原地修改，无返回值）。

    Returns:
        None：校验结果通过 issues 列表返回。
    '''

    referenced_ids = {
        requirement_id
        for todo in todos
        for requirement_id in todo.requirement_ids
    }

    for requirement in state.requirements.items:
        if requirement.kind in (
            RequirementKind.MUST_NOT_MODIFY,
            RequirementKind.SOFT_CONSTRAINT,
        ):
            continue

        if requirement.id not in referenced_ids:
            issues.append(
                PlanValidationIssue(
                    code="uncovered_requirement",
                    message=(
                        f"Requirement "
                        f"{requirement.id} ({requirement.kind.value})"
                        " is not covered by any implementation or"
                        " verification todo."
                    )
                )
            )


def validate_dependency_graph(
        todos: list[TodoItem],
        issues: list[PlanValidationIssue]
) -> None:
    """
    校验计划的依赖图是否存在环。
    验证任务依赖：
    1.依赖 id 必须存在
    2.不能依赖自身
    3.不能重复声明依赖
    4.不能形成环
    """
    task_map = {
        todo.id: todo
        for todo in todos
    }

    invalid = False

    # 第一阶段：检查依赖引用
    for todo in todos:
        if len(todo.depends_on) != len(set(todo.depends_on)):
            issues.append(
                PlanValidationIssue(
                    code="duplicate_dependency",
                    message=(
                        f"Task {todo.id} has duplicate dependencies."
                    )
                )
            )
            invalid = True

        for dep_id in todo.depends_on:
            if dep_id not in task_map:
                issues.append(
                    PlanValidationIssue(
                        code="unknown_dependency",
                        message=(
                            f"Task {todo.id} has an unknown task {dep_id}."
                        )
                    )
                )
                invalid = True

            elif dep_id == todo.id:
                issues.append(
                    PlanValidationIssue(
                        code="self_dependency",
                        message=(
                            f"Task {todo.id} depends on itself."
                        )
                    )
                )
                invalid = True

    if invalid:
        return

    # 第二阶段：DFS 检测环
    visiting = set()
    visited = set()

    def has_cycle(task_id: int) -> bool:
        if task_id in visited:
            return False
        if task_id in visiting:
            return True

        visiting.add(task_id)
        for dep_id in task_map[task_id].depends_on:
            if has_cycle(dep_id):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    for task_id in task_map:
        if has_cycle(task_id):
            issues.append(
                PlanValidationIssue(
                    code="dependency_cycle",
                    message=(
                        f"Task {task_id} is part of a dependency cycle."
                    )
                )
            )
            break # 只需报告一个环即可
    

__all__ = [
    "MIN_PLAN_ITEMS",
    "MAX_PLAN_ITEMS",
    "PlanValidationIssue",
    "PlanValidationResult",
    "validate_plan",
    "normalize_plan_content",
    "format_plan_validation_errors",
    "validate_plan_verification",
    "validate_requirement_references",
    "validate_requirement_mapping",
    "validate_requirement_coverage",
]
