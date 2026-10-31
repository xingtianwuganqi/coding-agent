# 第 24 课：Runtime Lifecycle / Termination Protocol（完整实践版）

> 基于 2026-10-28 提供的 `mini-coding-agent(3).zip` 实际源代码。本文包含完整实现代码、调用顺序、测试与限制。**注意：该版本不包含后续第 22、23 课可能已经在你本地合并的改动，请勿直接覆盖更新后的源文件。**

## 1. 本课要解决的三个现有问题

1. `agent.py` 只有收到无工具回复才调用 `evaluate_completion()`。模型一直调用工具时不会主动结束。
2. `stagnation.py` / `record_progress()` 把 `set_plan/update_task/replan` 等状态活动当成真实进展，可能反复刷新停滞计时。
3. 达到轮次上限和发生不可恢复阻塞的结果与普通完成结果混在字符串返回里，不利于诊断。

## 2. 设计与职责

- `task_completion.py`：判断是否真的满足用户条件，保留原文件现有实现。
- `runtime_lifecycle.py`：唯一决定继续、正常完成、或停止的地方。
- `ProgressTracker`：记录第一次获得的有效里程碑；重复调用相同工具不会被自动算作真实进展。
- `agent.py`：在模型调用**之前**和完整保存一批工具反馈**之后**调用 Controller。
- `agent_metrics.py`：记录最终 Outcome 与停止原因。

调用顺序：`check_lifecycle → call_model → (tool_calls → ToolResult/trace/history) → tracker.observe → check_lifecycle`。

## 3. 学习要点

- `CompletionStatus.INCOMPLETE` 只表示暂未满足，不应直接作为终止结果。
- `RunOutcome.COMPLETE` 表示成功，`RunOutcome.BLOCKED` 表示运行时确认不可恢复，`RunOutcome.STOPPED` 表示预算、停滞或模型故障。
- `MAX_MODEL_TURNS` 是硬预算，避免不断 Replan/修改代码重启进展计数后永远运行。
- 观察进展要区分“工具活动”与“首次建立的有效里程碑”。
- `allow_blocked_final` 被移除，因为是否结束由 Runtime 决策，不能只取决于模型是否发出第二次不带工具的答复。

## 4. `runtime_lifecycle.py` 完整实现


```python
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

```

## 5. `agent.py` 完整参考实现

这个文件保留原始 Tool Runner、Guard、Failure Recovery 和 Context Compression；变更集中在主循环前后以及统一的终止出口。为方便直接对照，以下是**完整文件**。


```python
"""Agent loop: drives the model, tools, planning and completion checks."""

import json
import time
from .config import MAX_MODEL_TURN
from .model_api import ModelCallError, call_model
from .context import (
    build_model_input,
    format_runtime_state,
    compress_history,
    print_context_debug,
    print_fixed_context_breakdown,
)
from .planning import (
    AgentState,
    create_agent_state,
    format_plan,
    print_state_debug,
    build_stagnation_feedback,
    build_runtime_instructions
)
from .task_completion import (
    CompletionStatus,
    build_completion_feedback
)
from .prompts import SYSTEM_PROMPT
from .tools_schema import TOOLS
from .tool_runner import (
    execute_tool_with_recovery
)
from .tool_result import ToolResult
from .agent_metrics import (
    AgentMetrics
)

from .agent_trace import (
    AgentTrace
)


from .guard import (
    RETRIEVAL_TOOLS,
    guard_tool_call,
    guard_recent_tool_call,
)
from .replanning import build_replan_feedback
from .runtime_lifecycle import (
    LifecycleAction, LifecycleDecision, RunOutcome, StopReason,
    RuntimeBudget, ProgressTracker, check_lifecycle,
)


def finish_agent(
    *,
    state: AgentState,
    metrics: AgentMetrics,
    trace: AgentTrace,
    decision: LifecycleDecision,
    preferred_text: str = "",
) -> str:
    """统一的单次终止出口：无需模型继续决定能否停止。"""
    metrics.run_outcome = decision.outcome.value if decision.outcome else None
    metrics.stop_reason = decision.reason.value if decision.reason else None
    trace.record(
        turn=metrics.model_turns,
        event_type="lifecycle_end",
        name=metrics.run_outcome or "unknown",
        detail=metrics.stop_reason or "requirements_satisfied",
    )
    metrics.end_runing()
    trace.print_trace()

    # 已通过 Runtime 检查，才允许返回模型此前的总结文字。
    if decision.outcome == RunOutcome.COMPLETE and preferred_text.strip():
        return preferred_text.strip()

    missing = [f"- {r.name}: {r.reason}" for r in decision.report.missing]
    changed = sorted(state.changed_files)
    lines = [
        f"Outcome: {metrics.run_outcome}",
        f"Reason: {metrics.stop_reason or 'requirements_satisfied'}",
        f"Model turns: {metrics.model_turns}",
        "Changed files: " + (", ".join(changed) if changed else "none"),
        "Unmet conditions:",
        *(missing or ["- none"]),
    ]
    return "\n".join(lines)

def run_agent(task: str) -> str:

    state = create_agent_state(
        user_prompt=task
    )

    # 指标对象
    metrics = AgentMetrics()

    # 追踪
    trace = AgentTrace()


    # 本课单独追踪“真实里程碑”，不依赖旧的 record_progress()。
    budget = RuntimeBudget(
        max_model_turns=min(MAX_MODEL_TURN, 120),
        max_no_progress_turns=30,
        warn_no_progress_turns=12,
    )
    progress = ProgressTracker()
    history_turns: list[list] = []

    progress.observe(
        state=state,
        report=check_lifecycle(state, 0, progress, budget).report,
        turn=0,
    )

    while True:
        # 模型调用之前先判定：已完成就不要继续消耗 Token。
        decision = check_lifecycle(state, metrics.model_turns, progress, budget)
        metrics.completion_checks += 1
        if decision.action != LifecycleAction.CONTINUE:
            return finish_agent(state=state, metrics=metrics, trace=trace, decision=decision)

        # 仅警告一次；达到硬阈值则由上面的 Controller 直接终止。
        if progress.should_warn(metrics.model_turns, budget):
            metrics.stagnation_warnings += 1
            state.stagnation_warnings += 1
            history_turns.append([{
                "role": "user",
                "content": build_stagnation_feedback(state),
            }])

        metrics.model_turns += 1
        turn = metrics.model_turns
        print(f"\n --- Agent step {turn} ---")
        trace.record(turn=turn, event_type="model_turn", name="call_model")

        # 先压缩旧history，会在内部进行删除
        try:
            compress_history(
                system_prompt=SYSTEM_PROMPT,
                tools=TOOLS,
                task=task,
                state=state,
                history_turns=history_turns,
                metrics=metrics,
                trace=trace
            )
        except ModelCallError as error:
            failed_report = check_lifecycle(state, metrics.model_turns, progress, budget).report
            decision = LifecycleDecision(
                action=LifecycleAction.TERMINATE,
                report=failed_report,
                outcome=RunOutcome.STOPPED,
                reason=StopReason.MODEL_ERROR,
            )
            finish_agent(state=state, metrics=metrics, trace=trace, decision=decision)
            return f"Agent stopped: {error}"

        print_fixed_context_breakdown(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            state=state
        )

        print_context_debug(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            state=state,
            history_turns=history_turns
        )

        runtime_instructions = build_runtime_instructions(
            state=state
        )

        replan_context = build_replan_feedback(
            state=state
        ) or ""

        # -------------------------
        # 1. 构建有限长度 Context
        # -------------------------
        model_input = build_model_input(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            history_turns=history_turns,
            state=state
        )

        # -------------------------
        # 2. 注入最新 Runtime State
        # -------------------------
        runtime_instructions = (
            SYSTEM_PROMPT
            + "\n\n"
            + "CURRENT RUNTIME STATE:\n"
            + format_runtime_state(state)
            + runtime_instructions
            + replan_context
            + "\n\n"
        )

        print_state_debug(state)

        try:
            message = call_model(
                model_input,
                instructions=runtime_instructions,
                tools=TOOLS,
                thinking=False,
                debug=True,
            )
        except ModelCallError as error:
            failed_report = check_lifecycle(state, metrics.model_turns, progress, budget).report
            decision = LifecycleDecision(
                action=LifecycleAction.TERMINATE,
                report=failed_report,
                outcome=RunOutcome.STOPPED,
                reason=StopReason.MODEL_ERROR,
            )
            finish_agent(state=state, metrics=metrics, trace=trace, decision=decision)
            return f"Agent stopped: {error}"
        # Preserve reasoning_content alongside tool calls for GLM's next turn.
        # input_items.append(message.model_dump(mode="json", exclude_none=True))
        turn_items = [message.model_dump(mode="json", exclude_none=True)]
        tool_calls = message.tool_calls or []

        if not tool_calls:
            # 模型停止调用工具，并不等于任务完成。
            decision = check_lifecycle(state, metrics.model_turns, progress, budget)
            metrics.completion_checks += 1
            trace.record(
                turn=metrics.model_turns,
                event_type="completion_check",
                name="no_tools",
                detail=f"status={decision.report.complete.value}",
            )
            if decision.action != LifecycleAction.CONTINUE:
                return finish_agent(
                    state=state, metrics=metrics, trace=trace,
                    decision=decision, preferred_text=message.content or "",
                )

            metrics.completion_rejections += 1
            history_turns.append(turn_items + [{
                "role": "user",
                "content": build_completion_feedback(decision.report, state),
            }])
            continue

        # -------------------------
        # 5. 执行 Tool Calls
        # -------------------------
        for call in tool_calls:
            print(f"Tool: {call.function.name}")
            print(f"Arguments: {call.function.arguments}")
            
            try:
                name = call.function.name
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")

                metrics.record_tool_call(name)
                # 记录轨迹
                trace.record(
                    turn= metrics.model_turns,
                    event_type="tool",
                    name=name,
                    detail=str(arguments)[:200]
                )
                
                guard_error = guard_tool_call(
                    name=name,
                    arguments=arguments,
                    state=state,
                    metrics=metrics,
                    trace=trace
                )
            
                if guard_error:
                    metrics.blocked_tool_calls += 1
                    trace.record(
                        turn=metrics.model_turns,
                        event_type="blocked",
                        name=name,
                        detail=guard_error,
                    )

                    result = ToolResult.fail(
                        error=guard_error
                    )

                else:
                    recent_error = guard_recent_tool_call(
                        name=name,
                        arguments=arguments,
                        state=state
                    )

                    if recent_error:
                        metrics.blocked_tool_calls += 1
                        trace.record(
                            turn=metrics.model_turns,
                            event_type="blocked_recent",
                            name=name,
                            detail=recent_error,
                        )
                        result = ToolResult.fail(
                            error=recent_error
                        )

                    else:
                        result = execute_tool_with_recovery(
                            name=call.function.name,
                            arguments=arguments,
                            state=state,
                            metrics=metrics,
                            trace=trace,
                            current_turn=metrics.model_turns,
                        )

                tool_output = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": (
                        result.content
                        if result.success
                        else result.error
                    )
                }
                
            except (json.JSONDecodeError, ValueError) as error:
                result = f"Tool arguments error: {error}"

                print(f'result: \n{result}')

                tool_output = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }

            turn_items.append(
                tool_output
            )
        # -------------------------
        # 6. 保存完整的这一轮
        # -------------------------
        history_turns.append(
            turn_items
        )

        # 一批工具全部执行、Tool Response 都保存以后，再统一检查。
        # 由实际状态和有效证据判断进展，set_plan/replan 本身不自动算进展。
        decision = check_lifecycle(state, metrics.model_turns, progress, budget)
        new_milestones = progress.observe(
            state, decision.report, metrics.model_turns
        )
        if new_milestones:
            trace.record(
                turn=metrics.model_turns,
                event_type="meaningful_progress",
                name="milestone",
                detail=", ".join(sorted(new_milestones))[:400],
            )
        decision = check_lifecycle(state, metrics.model_turns, progress, budget)
        metrics.completion_checks += 1
        if decision.action != LifecycleAction.CONTINUE:
            return finish_agent(
                state=state, metrics=metrics, trace=trace, decision=decision
            )

```

## 6. `agent_metrics.py` 修改说明

在 `AgentMetrics` Dataclass 中加入 `run_outcome` 与 `stop_reason`，并在 `print_summary()` 中输出。完整修改文件见配套 ZIP。没有必要把状态判定搬进指标对象。

## 7. 9 个针对性测试（完整代码）


```python
"""第24课：不调用真实 LLM 的生命周期回归测试。"""

from types import SimpleNamespace

from mini_coding_agent import agent
from mini_coding_agent.planning import create_agent_state, TodoItem, TaskStatus
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.evidence import EvidenceType
from mini_coding_agent.runtime_lifecycle import (
    LifecycleAction, ProgressTracker, RunOutcome,
    RuntimeBudget, StopReason, check_lifecycle,
)
from mini_coding_agent.task_completion import evaluate_completion
from mini_coding_agent.verification import VerificationKind, VerificationRecord
from mini_coding_agent.planning import update_task


def completed_state():
    state = create_agent_state("done")
    state.goal.locked = True
    state.requirements.locked = True
    state.plan.revision = 1
    state.plan.items = [TodoItem(id=1, content="implement", status=TaskStatus.COMPLETED)]
    return state


def test_complete_has_priority_over_budget():
    state = completed_state()
    decision = check_lifecycle(state, 120, ProgressTracker(), RuntimeBudget())
    assert decision.action == LifecycleAction.FINALIZE
    assert decision.outcome == RunOutcome.COMPLETE


def test_no_progress_ends_in_stop_not_success():
    state = create_agent_state("incomplete")
    decision = check_lifecycle(
        state, 41, ProgressTracker(last_meaningful_turn=10),
        RuntimeBudget(max_model_turns=120, max_no_progress_turns=30),
    )
    assert decision.action == LifecycleAction.TERMINATE
    assert decision.outcome == RunOutcome.STOPPED
    assert decision.reason == StopReason.NO_PROGRESS


def test_model_budget_ends_in_stop():
    decision = check_lifecycle(
        create_agent_state("incomplete"),
        120, ProgressTracker(last_meaningful_turn=119), RuntimeBudget(),
    )
    assert decision.reason == StopReason.MAX_MODEL_TURNS


def test_runtime_confirmed_unrecoverable_blocker():
    decision = check_lifecycle(
        create_agent_state("blocked"), 5, ProgressTracker(),
        RuntimeBudget(), unrecoverable_blocker=True,
    )
    assert decision.outcome == RunOutcome.BLOCKED
    assert decision.reason == StopReason.UNRECOVERABLE_BLOCKER


def test_replan_alone_does_not_reset_progress():
    state = create_agent_state("refactor")
    state.plan.revision = 1
    state.plan.items = [TodoItem(id=1, content="refactor", status=TaskStatus.IN_PROGRESS)]
    tracker = ProgressTracker()
    tracker.observe(state, evaluate_completion(state), 5)
    assert tracker.last_meaningful_turn == 5

    # 仅更新 plan revision 和 Todo 的编号、原任务含义不变。
    state.plan.revision += 1
    state.plan.items = [TodoItem(id=4, content="refactor", status=TaskStatus.IN_PROGRESS)]
    assert tracker.observe(state, evaluate_completion(state), 9) == set()
    assert tracker.last_meaningful_turn == 5


def test_repeat_tests_same_code_revision_are_not_new_progress():
    state = create_agent_state("fix")
    state.requirements.locked = True
    state.changed_files.add("a.py")
    state.workspace_revision = 1
    state.code_revision = 1
    state.plan.items = [TodoItem(id=1, content="fix", status=TaskStatus.PENDING)]
    state.verification.records.append(VerificationRecord(
        VerificationKind.TEST, True, 1, 1, "pytest", "pass"
    ))
    tracker = ProgressTracker()
    tracker.observe(state, evaluate_completion(state), 5)
    state.verification.records.append(VerificationRecord(
        VerificationKind.TEST, True, 1, 1, "pytest", "pass again"
    ))
    assert tracker.observe(state, evaluate_completion(state), 10) == set()
    assert tracker.last_meaningful_turn == 5


def test_stale_verification_prevents_completion():
    state = completed_state()
    state.changed_files.add("x.py")
    state.code_revision = 2
    state.workspace_revision = 2
    state.verification.records.extend([
        VerificationRecord(VerificationKind.TEST, True, 1, 1, "pytest", "pass"),
        VerificationRecord(VerificationKind.DIFF, True, 1, 1, "git diff", "ok"),
    ])
    assert check_lifecycle(state, 2, ProgressTracker(), RuntimeBudget()).action == LifecycleAction.CONTINUE


def _fake_assistant(tool_calls=(), content=""):
    return SimpleNamespace(
        content=content,
        tool_calls=list(tool_calls),
        model_dump=lambda **_: {"role": "assistant", "content": content},
    )


def test_agent_stops_right_after_completed_tool_batch(monkeypatch):
    state = create_agent_state("task")
    state.goal.locked = True
    state.requirements.locked = True
    state.plan.revision = 1
    state.plan.items = [TodoItem(id=1, content="do refactor", status=TaskStatus.IN_PROGRESS)]
    monkeypatch.setattr(agent, "create_agent_state", lambda user_prompt: state)
    monkeypatch.setattr(agent, "compress_history", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_context_debug", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_fixed_context_breakdown", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_state_debug", lambda *args: None)
    monkeypatch.setattr(agent, "guard_tool_call", lambda **kwargs: None)
    monkeypatch.setattr(agent, "guard_recent_tool_call", lambda **kwargs: None)

    called = []
    tc = SimpleNamespace(
        id="call-one", function=SimpleNamespace(
            name="update_task", arguments='{"task_id": 1, "status": "completed"}'
        )
    )
    def fake_model(*args, **kwargs):
        called.append(1)
        return _fake_assistant([tc])
    monkeypatch.setattr(agent, "call_model", fake_model)

    def fake_tool(*, name, arguments, state, metrics, **kwargs):
        return update_task(state=state, metrics=metrics, **arguments)
    monkeypatch.setattr(agent, "execute_tool_with_recovery", fake_tool)
    result = agent.run_agent("task")
    assert result.startswith("Outcome: complete")
    assert len(called) == 1


def test_agent_stops_no_tool_loop_when_budget_exceeded(monkeypatch):
    monkeypatch.setattr(agent, "compress_history", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_context_debug", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_fixed_context_breakdown", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_state_debug", lambda *args: None)
    orig = agent.RuntimeBudget
    monkeypatch.setattr(agent, "RuntimeBudget", lambda **kwargs: orig(120, 3, 1))
    calls = []
    def fake_model(*args, **kwargs):
        calls.append(1)
        return _fake_assistant(content="I am done")
    monkeypatch.setattr(agent, "call_model", fake_model)
    result = agent.run_agent("task")
    assert "Outcome: stopped" in result
    assert "no_progress" in result
    assert len(calls) == 3

```

## 8. 运行与验收

1. 先阅读 `lesson24.patch`，确认只改本课涉及文件；新建实验分支与 Runner。
2. 在项目根目录运行 `git apply --check lesson24.patch`，没有报错才执行 `git apply lesson24.patch`。
3. 运行 `uv run pytest -q tests/test_runtime_lifecycle.py`，再运行 `uv run pytest -q`。
4. 用原来能完成的重构任务测试：完成以后即使模型还想继续请求 `git diff`，下一轮前的 Lifecycle Controller 应停止。
5. 用故意不能满足的任务验证：无实质进展达到阈值时，必须返回 `STOPPED / NO_PROGRESS`；不能虚报 COMPLETE。

在受限环境中，我们用仅用于测试导入的 OpenAI stub（不请求 API）得到 **原有测试 72 passed，新版含 9 个测试共 81 passed**。真实模型调用、不同 Runner 的环境、提示词行为未验证。

## 9. 重要限制与下一阶段

- 当前只会在传入可靠的 `unrecoverable_blocker=True` 时主动返回 BLOCKED；并未实现完整的可恢复性裁决器。不能把任意一个 BLOCKED 的 Todo 当成整体终止。
- 相同文件反复改写并生成新 code_revision 仍可能创建新的验证里程碑，所以必须保留硬模型调用上限。第 27 课再完善轨迹指纹和评估。
- `run_command` 所调用的脚本可能间接改写文件，但旧代码未可靠追踪所有外部副作用；验证证据仍可能不可靠。第 26 课完善 Tool Safety。
- 此版本用确定性最终总结，不再调用模型；如需自然语言总结，可增加一次无工具的总结模型调用，并有异常回退。
- 新旧 `state.last_progress_turn` 在过渡期可能被旧 `tool_runner.py` 记录刷新，**但 Runtime Lifecycle 只使用 `ProgressTracker.last_meaningful_turn`**，避免旧实现直接影响结束判断。

### 本课思考题

1. 为什么正常完成必须先于预算耗尽进行判断？
2. 为什么不能仅靠成功的 `replan` 来更新 `last_meaningful_turn`？
3. 为什么必须等整批工具响应加入历史后再进行终止判断？
4. 何时应返回 BLOCKED 而非 STOPPED？
