"""
planning.py（工作区根目录入口）

本文件用于在仓库根目录直接暴露 mini_coding_agent 的「计划校验」
模块，方便以 `import planning` 或 `from planning import validate_plan`
等方式引用。

实际实现位于 :mod:`mini_coding_agent.planning`（其底层委托给
:mod:`mini_coding_agent.planing`）。该模块中的全部 ``validate_*``
计划校验方法均带有完整中文注释（用途、校验逻辑、Args 与 Returns）。

本文件仅做转发，不改变任何行为。
"""

from src.mini_coding_agent.planning import (  # noqa: F401
    AgentState,
    TodoItem,
    TaskStatus,
    PlanValidationIssue,
    PlanValidationResult,
    validate_plan,
    validate_plan_verification,
    validate_requirement_references,
    validate_requirement_mapping,
    validate_requirement_coverage,
)

__all__ = [
    "AgentState",
    "TodoItem",
    "TaskStatus",
    "PlanValidationIssue",
    "PlanValidationResult",
    "validate_plan",
    "validate_plan_verification",
    "validate_requirement_references",
    "validate_requirement_mapping",
    "validate_requirement_coverage",
]
