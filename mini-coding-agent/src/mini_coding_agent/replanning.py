from dataclasses import dataclass
from .planing import (
    ReplanReason,
    AgentState,
    TaskStatus
)

@dataclass
class ReplanSignal:
    '''
    重新计划的信号
    '''
    should_replan: bool
    reason: ReplanReason
    message: str = ""


def detect_replan_signal(
        state: AgentState,
) -> ReplanSignal:

    # 1.当前Task 被blocked
    blocked_tasks = [
        todo 
        for todo in state.plan.items
        if todo.status == TaskStatus.BLOCKED
    ]

    if blocked_tasks:
        return ReplanSignal(
            should_replan=True,
            reason=ReplanReason.BLOCKED_TASK,
            message=(
                "The current plan contains "
                "a blocked task."
            )
        )

    # 2. 连续相同失败
    if state.failure.consecutive_count >= 2:
        return ReplanSignal(
            should_replan=True,
            reason=ReplanReason.REPEATED_FAILURE,
            message=(
                "The same operation has "
                "failed repeatedly"
            )
        )

    # 3.多次Stagnation
    if state.stagnation_warnings >= 2:
        return ReplanSignal(
            should_replan=True,
            reason=ReplanReason.STAGNATION,
            message=(
                "The current plan has "
                "stalled repeatedly"
            )
        )

    return ReplanSignal(
        should_replan=False,
        reason=None,
    )


def build_replan_feedback(
        state: AgentState,
) -> str | None:
    signal = detect_replan_signal(
        state,
    )

    if not signal.should_replan:
        return None

    return (
        "RUNTIME REPLAN NOTICE:\n"
        f"{signal.message}\n\n"
        "Re-evaluate whether the current "
        "plan is still an effective strategy "
        "for achieving the locked goal while "
        "respecting all locked requirements.\n\n"
        "If the plan is no longer appropriate, "
        "call replan with a concrete reason "
        "and a replacement plan.\n"
        "Do not change the goal or requirements."
    )