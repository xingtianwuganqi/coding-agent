"""
停滞 / 进展 判定逻辑。

这些函数只关心「任务是否在推进」这一横切关注点，
并不属于 Plan/Pl 本身的结构或校验逻辑，
因此从旧 planning 前身模块拆分到这里。

planning.py 会在文件末尾重新导出这些名字，
以保持对外接口不变。
"""

from __future__ import annotations

from .task_state import (
    TaskStatus,
)


def record_progress(
        state,
        turn: int,
) -> None:
    state.last_progress_turn = turn
    state.stagnation_warnings = 0


def tool_caused_progress(
    name: str,
    result,
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
        state,
        current_turn: int,
) -> bool:
    '''
    判断是不是进入了停滞
    '''
    # 延迟导入，避免与 planning 形成循环依赖
    from .planning import MAX_STAGNANT_TURNS

    if not state.plan.items:
        return False

    stagnant_turns = (
        current_turn - state.last_progress_turn
    )

    # 判断距离上次进展（progress_tools中内容）的轮数是否超过阈值
    return stagnant_turns >= MAX_STAGNANT_TURNS


def build_stagnation_feedback(
        state,
) -> str:
    '''
    构建停滞反馈
    '''
    active_task = next(
        (
            todo
            for todo in state.plan.items
            if todo.status == TaskStatus.IN_PROGRESS
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
