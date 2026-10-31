'''
计划对账

给定旧计划和新计划，哪些旧任务可以安全地保留完成状态？

我建议先采用保守的规则。
校验条件	要求
旧任务存在	reuse_from_task_id 必须指向旧计划
旧任务状态	必须为 COMPLETED
任务语义	内容、类型、Requirement 和验证要求不能改变
依赖关系	前置依赖经过 ID 映射后保持一致
依赖状态	新计划中的前置任务也必须保留完成
验证证据	如任务要求 Evidence，证据必须仍然有效
'''

from __future__ import annotations
from typing import TYPE_CHECKING

from .evidence import has_valid_evidence
from .plan_validation import normalize_plan_content

if TYPE_CHECKING:
    from .planning import AgentState, TodoItem


def reconcile_completed_tasks(
        state: AgentState,
        new_todos: list[TodoItem],
) -> tuple[list[int], str | None]:
    '''
    在Replan 时安全的集成旧任务的COMPLETED 状态。

    约定：
    - new_todos 已完成 DAG Validation
    - reuse_from_task_id 指向当前旧计划
    - 不复制 IN_PROGRESS 和BLOCKED
    - 验证失败时调用方不提交新计划

    返回：
    （保留完整状态的新任务 ID 列表， None）
    或
    （[], 错误原因）
    '''

    # 延迟导入，避免planning.py 初始化阶段循环导入
    from .planning import TaskStatus

    # 字典
    old_by_id = {
        todo.id: todo 
        for todo in state.plan.items
    }

    # 旧任务 ID -> 新任务 ID
    old_to_new: dict[int, int] = {}

    # 第一阶段：建立显式任务映射
    for new in new_todos:
        old_id = new.reuse_from_task_id

        if old_id is None:
            continue

        if old_id not in old_by_id:
            return [], (
                f"Unknown reused task ID: {old_id}"
            )

        if old_id in old_to_new:
            return [], (
                f"Old task {old_id} "
                "is reused more than once"
            )

        old_to_new[old_id] = new.id

    eligible: set[int] = set()

    # 第二阶段：判断哪些旧任务具备复用资格
    for new in new_todos:
        old_id = new.reuse_from_task_id

        if old_id is None:
            continue

        old = old_by_id[old_id]

        if old.status != TaskStatus.COMPLETED:
            continue

        same_definition = (
            normalize_plan_content(old.content)
            == normalize_plan_content(new.content)
            and old.kind == new.kind
            and set(old.requirement_ids) == set(new.requirement_ids)
            and old.required_evidence == new.required_evidence
        )

        if not same_definition:
            continue

        # 将旧计划中的前置任务 ID 映射到新任务
        mapped_dependencies = [
            old_to_new.get(dep_id)
            for dep_id in old.depends_on
        ]

        if any(
            dep_id is None
            for dep_id in mapped_dependencies
        ): 
            continue

        if set(mapped_dependencies) != set(new.depends_on):
            continue

        # 验证证据失效，不能继承完成状态
        if new.required_evidence is not None and not has_valid_evidence(state, new.required_evidence):
            continue

        eligible.add(new.id)

    # 第三阶段：依赖闭包
    # 只有前置任务也已成功完成，当前任务才能保留完成

    carried: set[int] = set()

    while True:
        newly_ready = {
            todo.id
            for todo in new_todos
            if todo.id in eligible
            and todo.id in eligible
            and all(
                dep_id in carried
                for dep_id in todo.depends_on
            )
        }

        if not newly_ready:
            break

        carried.update(newly_ready)

    # 第四阶段： 只对候选新计划赋予完成状态
    for todo in new_todos:
        if todo.id in carried:
            todo.status = TaskStatus.COMPLETED

    return sorted(carried), None