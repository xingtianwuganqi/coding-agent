'''
runtime 生命周期
'''

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .planning import AgentState, AgentMetrics, TaskStatus
from .evidence import has_valid_evidence

from .task_completion import (
    CompletionStatus,
    CompletionReport,
    evaluate_completion
)

class LifecycleAction(str, Enum):
    '''
    Agent 当前处于哪个执行阶段
    '''
    CONTINUE = 'continue'
    FINALIZE = 'finalize'
    TERMINATE = 'terminate'


class RunOutcome(str, Enum):
    '''
    这一次 Agent 运行最终得到什么结果
    '''
    COMPLETE = 'complete'
    BLOCKED = 'blocked'
    STOPPED = 'stopped'


class StopReason(str, Enum):
    NO_PROGRESS = 'no_progress'
    MAX_MODEL_TURNS = 'max_model_turns'
    # 不可恢复的拦截器
    UNRECOVERABLE_BLOCKER = 'unrecoverable_blocker'
    MODEL_ERROR = 'model_error'


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

        


@dataclass 
class LifecycleDecision:
    action: LifecycleAction
    report: CompletionReport
    outcome: RunOutcome | None = None
    reason: StopReason | None = None

'''
Milestone 可以理解为一个明确的进度里程碑。
goal_locked
requirements_locked
plan_created
first_workspace_change
completed_task:修改导入
code_verified:revision_3
diff_verified:revision_5
user_requirement_1
最关键的是：同一个里程碑，只能首次出现时计入进展。
'''

@dataclass
class ProgressTracker:
    """只记录首次获得的里程碑，不把重试、重复读取、Replan 直接当进展。"""

    # 最近一次获得新里程碑的模型轮次
    last_meaningful_turn: int = 0
    # 已经出现过的里程碑集合
    seen_milestones: set[str] = field(
        default_factory=set
    )
    # 避免同一次停滞连续发送相同警告
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

        # 相同代码版本重复运行pytest，不算新里程碑
        if any(r.name == "code_verified" and r.satisfied for r in report.requirements):
            milestones.add(f"code_verified:{state.code_revision}")
        if any(r.name == "diff_verified" and r.satisfied for r in report.requirements):
            milestones.add(f"diff_verified:{state.workspace_revision}")

        new_milestones = milestones - self.seen_milestones
        self.seen_milestones.update(milestones)
        if new_milestones:
            self.last_meaningful_turn = turn
            self.last_warning_progress_turn = None
            # 兼容旧的进展显示；生命周期只读 Tracker，不读旧工具计数
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
    '''
    每次模型请求之前、整批工具调用之后使用同一个决策函数。

    根据 Runtime 状态决定：
    - 继续运行
    - 进入最终总结阶段
    - 终止并报告原因

    unrecoverable_blocker 必须由 Runtime 的可靠恢复判定提供.
    不能直接使用模型自行声称的结果
    '''

    report = evaluate_completion(state=state)

    # 1. 优先检查是否真正完成
    if report.complete == CompletionStatus.COMPLETE:
        return LifecycleDecision(
            action=LifecycleAction.FINALIZE,
            report=report,
            outcome=RunOutcome.COMPLETE,
        )

    # 2. 已确认不可恢复
    if unrecoverable_blocker:
        return LifecycleDecision(
            action=LifecycleAction.TERMINATE,
            report=report,
            outcome=RunOutcome.BLOCKED,
            reason=StopReason.UNRECOVERABLE_BLOCKER,
        )

    # 3. 检查模型调用预算
    if model_turns >= budget.max_model_turns:
        return LifecycleDecision(
            action=LifecycleAction.TERMINATE,
            report=report,
            outcome=RunOutcome.STOPPED,
            reason=StopReason.MAX_MODEL_TURNS,
        )

    # 4. 检查无进展的预算
    no_progress_turns = model_turns - progress.last_meaningful_turn
    if no_progress_turns > budget.max_no_progress_turns:
        return LifecycleDecision(
            action=LifecycleAction.TERMINATE,
            report=report,
            outcome=RunOutcome.STOPPED,
            reason=StopReason.NO_PROGRESS,
        )

    # 5. 继续执行
    return LifecycleDecision(
        action=LifecycleAction.CONTINUE,
        report=report,
    )


def evaluate_runtime(
        state: AgentState,
        metrics: AgentMetrics,
        progress: ProgressTracker,
        budget: RuntimeBudget,
) -> LifecycleDecision:
    """Convenience wrapper using the same ProgressTracker as run_agent()."""
    return check_lifecycle(
        state=state,
        model_turns=metrics.model_turns,
        progress=progress,
        budget=budget,
    )
