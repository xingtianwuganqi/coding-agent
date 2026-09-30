"""兼容别名模块：``mini_coding_agent.planning``。

本项目中的规范 planning 模块拼写为 ``planing``（见
``src/mini_coding_agent/planing.py``），代码库其余部分均以该拼写导入。
本模块存在的原因，是让以 ``planning`` 拼写引用的调用方也能解析到同一套对象。

本模块只负责转发与 plan/pl 相关的公共接口（计划/需求/校验），
其余逻辑已经整理到 planing 的同级模块中：

- :mod:`mini_coding_agent.task_state`：todo 状态机、证据门禁与转移校验；
- :mod:`mini_coding_agent.runtime_context`：goal/plan/需求上下文渲染；
- :mod:`mini_coding_agent.stagnation`：进展记录与停滞判定；
- :mod:`mini_coding_agent.requirements`：需求相关的数据结构与工具。

plan/pl 相关的实际实现仍位于 :mod:`mini_coding_agent.planing`，
本模块仅做同名转发/再导出，行为与 planing.py 完全一致。

下方所有 ``validate_*`` 计划校验方法都带有完整中文注释（说明用途、
校验逻辑、Args 与 Returns，并与 planing.py 中的实现保持一致），
实际实现委托给 :mod:`mini_coding_agent.planing` 中的同名函数，
行为完全一致，不做任何改动。
"""

from .planing import (  # noqa: F401
    AgentState,
    TodoItem,
    TaskStatus,
    PlanValidationIssue,
    PlanValidationResult,
    set_plan,
    replan,
    update_task,
    get_plan,
    format_plan,
    parse_plan_items,
    validate_plan as _validate_plan,
    validate_plan_verification as _validate_plan_verification,
    validate_requirement_references as _validate_requirement_references,
    validate_requirement_mapping as _validate_requirement_mapping,
    validate_requirement_coverage as _validate_requirement_coverage,
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
    return _validate_plan(state=state, todos=todos)


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
    return _validate_plan_verification(
        state=state,
        todos=todos,
        issues=issues
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
    return _validate_requirement_references(
        state=state,
        todos=todos,
        issues=issues
    )


def validate_requirement_mapping(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue]
) -> None:
    '''
    校验 requirement.kind 与引用它的 todo.kind 是否一一对应。

    对应关系：
    MUST_CHANGE     -> IMPLEMENTATION
    MUST_VERIFY     -> VERIFICATION
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
    return _validate_requirement_mapping(
        state=state,
        todos=todos,
        issues=issues
    )


def validate_requirement_coverage(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue],
) -> None:
    """
    校验计划是否覆盖了所有需要被计划覆盖的 requirement。

    用途：
    确保每条 MUST_CHANGE 或 MUST_VERIFY 的需求都至少被一个
    todo 显式引用，避免需求被遗漏而无人负责实现或验证。

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
    return _validate_requirement_coverage(
        state=state,
        todos=todos,
        issues=issues
    )


__all__ = [
    "AgentState",
    "TodoItem",
    "TaskStatus",
    "PlanValidationIssue",
    "PlanValidationResult",
    "set_plan",
    "replan",
    "update_task",
    "get_plan",
    "format_plan",
    "parse_plan_items",
    "validate_plan",
    "validate_plan_verification",
    "validate_requirement_references",
    "validate_requirement_mapping",
    "validate_requirement_coverage",
]
