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
