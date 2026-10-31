"""第24课：Runtime 统一完成、预算、停滞与终止决策。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .evidence import has_valid_evidence
from .planning import AgentState, TaskStatus
from .task_completion import CompletionReport, CompletionStatus, evaluate_completion


class LifecycleAction(str, Enum):
    CONTINUE = "continue"
    FINALIZE = "finalize"
    TERMINATE = "terminate"


class RunOutcome(str, Enum):
    COMPLETE = "complete"
    BLOCKED = "blocked"
    STOPPED = "stopped"


class StopReason(str, Enum):
    UNRECOVERABLE_BLOCKER = "unrecoverable_blocker"
    NO_PROGRESS = "no_progress"
    MAX_MODEL_TURNS = "max_model_turns"
    MODEL_ERROR = "model_error"


@dataclass(frozen=True)
class RuntimeBudget:
    max_model_turns: int = 120
    max_no_progress_turns: int = 30
    warn_no_progress_turns: int = 12

    def __post_init__(self) -> None:
        if not (0 < self.warn_no_progress_turns < self.max_no_progress_turns):
            raise ValueError("warn threshold must be below stop threshold")
        if self.max_model_turns <= 0:
            raise ValueError("max_model_turns must be positive")


@dataclass(frozen=True)
class LifecycleDecision:
    action: LifecycleAction
    report: CompletionReport
    outcome: RunOutcome | None = None
    reason: StopReason | None = None


@dataclass
class ProgressTracker:
    """只记录首次获得的里程碑，不把重试、重复读取、Replan 直接当进展。"""

    last_meaningful_turn: int = 0
    seen_milestones: set[str] = field(default_factory=set)
    last_warning_progress_turn: int | None = None

    def observe(
        self,
        state: AgentState,
        report: CompletionReport,
        turn: int,
    ) -> set[str]:
        milestones: set[str] = set()

        if state.goal.locked:
            milestones.add("goal_locked")
        if state.requirements.locked:
            milestones.add("requirements_locked")
        if state.plan.items:
            milestones.add("plan_created")
        if state.changed_files:
            milestones.add("first_workspace_change")

        for todo in state.plan.items:
            if todo.status != TaskStatus.COMPLETED:
                continue
            if todo.required_evidence is not None and not has_valid_evidence(
                state, todo.required_evidence
            ):
                continue
            # 使用任务定义而非 Plan Revision：Replan 重用旧任务不会重计进展。
            identity = (
                todo.content.strip().casefold(),
                todo.kind.value,
                tuple(sorted(todo.requirement_ids)),
            )
            milestones.add(f"completed_task:{identity!r}")

        for requirement in report.requirements:
            if requirement.satisfied and requirement.name.startswith("user_requirement_"):
                milestones.add(requirement.name)

        # 相同代码版本重复运行 pytest，不算新里程碑。
        if any(r.name == "code_verified" and r.satisfied for r in report.requirements):
            milestones.add(f"code_verified:{state.code_revision}")
        if any(r.name == "diff_verified" and r.satisfied for r in report.requirements):
            milestones.add(f"diff_verified:{state.workspace_revision}")

        new_milestones = milestones - self.seen_milestones
        self.seen_milestones.update(milestones)
        if new_milestones:
            self.last_meaningful_turn = turn
            self.last_warning_progress_turn = None
            # 兼容旧的进展显示；生命周期只读 Tracker，不读旧工具计数。
            state.last_progress_turn = turn
            state.stagnation_warnings = 0
        return new_milestones

    def should_warn(self, turn: int, budget: RuntimeBudget) -> bool:
        if turn - self.last_meaningful_turn < budget.warn_no_progress_turns:
            return False
        if self.last_warning_progress_turn == self.last_meaningful_turn:
            return False
        self.last_warning_progress_turn = self.last_meaningful_turn
        return True


def check_lifecycle(
    state: AgentState,
    model_turns: int,
    progress: ProgressTracker,
    budget: RuntimeBudget,
    *,
    unrecoverable_blocker: bool = False,
) -> LifecycleDecision:
    """每次模型请求之前、整批工具调用之后使用同一个决策函数。"""

    report = evaluate_completion(state)
    if report.complete == CompletionStatus.COMPLETE:
        return LifecycleDecision(LifecycleAction.FINALIZE, report, RunOutcome.COMPLETE)

    # 只有 Runtime 获得可靠的不可恢复依据才能传 True。
    if unrecoverable_blocker:
        return LifecycleDecision(
            LifecycleAction.TERMINATE,
            report,
            RunOutcome.BLOCKED,
            StopReason.UNRECOVERABLE_BLOCKER,
        )

    if model_turns >= budget.max_model_turns:
        return LifecycleDecision(
            LifecycleAction.TERMINATE,
            report,
            RunOutcome.STOPPED,
            StopReason.MAX_MODEL_TURNS,
        )

    if model_turns - progress.last_meaningful_turn >= budget.max_no_progress_turns:
        return LifecycleDecision(
            LifecycleAction.TERMINATE,
            report,
            RunOutcome.STOPPED,
            StopReason.NO_PROGRESS,
        )

    return LifecycleDecision(LifecycleAction.CONTINUE, report)
