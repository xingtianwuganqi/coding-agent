"""Lesson 27: evaluation correctness and the two discovered regression paths."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.agent import finish_agent
from mini_coding_agent.checkpoint import CheckpointStore, RunSession
from mini_coding_agent.evaluation import (
    EvalCase, EvaluationError, compare_results, evaluate_case,
    load_results, summarize_results, write_result,
)
from mini_coding_agent.planning import create_agent_state
from mini_coding_agent.runtime_lifecycle import (
    LifecycleAction, LifecycleDecision, ProgressTracker,
    RunOutcome, RuntimeBudget, StopReason, evaluate_runtime,
)
from mini_coding_agent.task_completion import evaluate_completion


def _session(outcome: str = "complete", turns: int = 8) -> RunSession:
    session = RunSession(state=create_agent_state("fix a calculator"))
    session.metrics.run_outcome = outcome
    session.metrics.model_turns = turns
    session.metrics.tool_calls = 12
    session.metrics.replans = 1
    session.metrics.end_time = session.metrics.start_time + 10.0
    return session


def _case(**kwargs):
    return EvalCase(case_id="calc", task="fix a calculator", **kwargs)


def test_independent_oracle_passes_successful_artifact(tmp_path):
    (tmp_path / "calc.py").write_text("raise ValueError('zero')", encoding="utf-8")
    case = _case(must_exist=["calc.py"], file_contains={"calc.py": "raise ValueError"},
                 max_model_turns=10)
    result = evaluate_case(case, tmp_path, _session())
    assert result.passed
    assert result.external_success
    assert result.runtime_matches_expected
    assert result.efficiency_warnings == []
    assert result.duration_seconds == pytest.approx(10.0)


def test_runtime_complete_but_external_failure_is_false_complete(tmp_path):
    (tmp_path / "calc.py").write_text("return a / b", encoding="utf-8")
    result = evaluate_case(_case(file_contains={"calc.py": "raise ValueError"}),
                           tmp_path, _session("complete"))
    assert not result.passed
    assert result.false_complete
    assert not result.external_success


def test_external_success_but_runtime_stopped_is_not_pass(tmp_path):
    (tmp_path / "calc.py").write_text("raise ValueError()", encoding="utf-8")
    result = evaluate_case(_case(file_contains={"calc.py": "raise ValueError"}),
                           tmp_path, _session("stopped"))
    assert not result.passed
    assert result.external_success
    assert not result.false_complete


def test_absence_oracle(tmp_path):
    result = evaluate_case(_case(must_not_exist=["old.py"]), tmp_path, _session())
    assert result.passed
    (tmp_path / "old.py").write_text("old", encoding="utf-8")
    assert not evaluate_case(_case(must_not_exist=["old.py"]), tmp_path, _session()).passed


def test_file_not_contains_requires_existing_file(tmp_path):
    result = evaluate_case(_case(file_not_contains={"missing.py": "bad"}),
                           tmp_path, _session())
    assert not result.external_success
    (tmp_path / "missing.py").write_text("good", encoding="utf-8")
    assert evaluate_case(_case(file_not_contains={"missing.py": "bad"}),
                         tmp_path, _session()).passed


def test_missing_opt_in_tests_never_silently_pass(tmp_path):
    oracle = tmp_path.parent / (tmp_path.name + "_oracle")
    oracle.mkdir()
    (oracle / "test_math.py").write_text("def test_one(): assert 1 == 1", encoding="utf-8")
    case = _case(pytest_paths=["test_math.py"])
    result = evaluate_case(case, tmp_path, _session())
    assert not result.passed
    assert any(check.name == "pytest" and not check.passed for check in result.checks)
    result = evaluate_case(case, tmp_path, _session(), run_tests=True, oracle_root=oracle)
    assert result.passed


def test_bad_pytest_fails(tmp_path):
    oracle = tmp_path.parent / (tmp_path.name + "_oracle")
    oracle.mkdir()
    (oracle / "test_bad.py").write_text("def test_bad(): assert False", encoding="utf-8")
    result = evaluate_case(_case(pytest_paths=["test_bad.py"]),
                           tmp_path, _session(), run_tests=True, oracle_root=oracle)
    assert not result.external_success


@pytest.mark.parametrize("unsafe", ["../escape.py", "/etc/passwd"])
def test_refuse_paths_outside_workspace(tmp_path, unsafe):
    with pytest.raises(EvaluationError):
        evaluate_case(_case(must_exist=[unsafe]), tmp_path, _session())


def test_refuse_symlink_outside_workspace(tmp_path):
    outside = tmp_path.parent / "private.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    with pytest.raises(EvaluationError):
        evaluate_case(_case(must_exist=["link.txt"]), tmp_path, _session())


def test_case_requires_external_oracle(tmp_path):
    with pytest.raises(EvaluationError):
        evaluate_case(_case(), tmp_path, _session())


def test_case_json_rejects_unknown_keys(tmp_path):
    file = tmp_path / "case.json"
    file.write_text(json.dumps({"case_id": "x", "task": "x", "must_exist": ["x"],
                                "prompt_injection": "anything"}))
    with pytest.raises(EvaluationError):
        EvalCase.from_file(file)


def test_efficiency_budget_is_warning_not_correctness_failure(tmp_path):
    (tmp_path / "ok.py").write_text("ok", encoding="utf-8")
    result = evaluate_case(_case(must_exist=["ok.py"], max_model_turns=5,
                                 max_tool_calls=10), tmp_path, _session())
    assert result.passed
    assert len(result.efficiency_warnings) == 2


def test_result_roundtrip_and_suite_comparison(tmp_path):
    (tmp_path / "ok.py").write_text("yes", encoding="utf-8")
    good = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session())
    bad = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session("stopped"))
    result_path = tmp_path / "one.json"
    write_result(result_path, good)
    assert json.loads(result_path.read_text())["case_id"] == "calc"
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(json.dumps([bad.to_json()]), encoding="utf-8")
    new.write_text(json.dumps([good.to_json()]), encoding="utf-8")
    diff = compare_results(load_results(new), load_results(old))
    assert diff["improvements"] == ["calc"]
    assert diff["regressions"] == []
    assert summarize_results(load_results(new)).pass_rate == 1
    diff_reversed = compare_results(load_results(old), load_results(new))
    assert diff_reversed["regressions"] == ["calc"]


def test_suite_different_cases_not_comparable(tmp_path):
    (tmp_path / "ok.py").write_text("yes", encoding="utf-8")
    old = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session())
    new = evaluate_case(EvalCase("other", "task", must_exist=["ok.py"]),
                        tmp_path, _session())
    with pytest.raises(EvaluationError):
        compare_results([old], [new])


def test_evaluate_runtime_wrapper_uses_tracker_not_old_kwarg():
    session = _session("stopped")
    decision = evaluate_runtime(session.state, session.metrics,
                                ProgressTracker(), RuntimeBudget())
    assert decision.action == LifecycleAction.CONTINUE


def test_finish_agent_persists_final_outcome(tmp_path):
    session = _session("stopped")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CheckpointStore(tmp_path / "checkpoint.json", workspace)
    decision = LifecycleDecision(
        action=LifecycleAction.TERMINATE,
        report=evaluate_completion(session.state),
        outcome=RunOutcome.STOPPED,
        reason=StopReason.NO_PROGRESS,
    )
    result = finish_agent(
        state=session.state, metrics=session.metrics, trace=session.trace,
        decision=decision, session=session, checkpoint_store=store,
    )
    assert "no_progress" in result
    assert store.load().metrics.run_outcome == "stopped"
    assert store.load().metrics.stop_reaseon == "no_progress"

class FakeMessage:
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []
        self.content = "model summary"

    def model_dump(self, **_kwargs):
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [{"id": x.id, "type": "function",
                                      "function": {"name": x.function.name,
                                                   "arguments": x.function.arguments}}
                                     for x in self.tool_calls]
        return message


@pytest.mark.parametrize("with_tool", [False, True])
def test_agent_end_paths_persist_final_checkpoint(tmp_path, monkeypatch, with_tool):
    """Regression: both no-tool and tool-batch termination must save outcome."""
    from types import SimpleNamespace
    from mini_coding_agent import agent

    def fake_check(state, model_turns, progress, budget):
        final = model_turns >= 1
        return LifecycleDecision(
            action=LifecycleAction.FINALIZE if final else LifecycleAction.CONTINUE,
            report=evaluate_completion(state),
            outcome=RunOutcome.COMPLETE if final else None,
        )

    calls = ([SimpleNamespace(id="call_1", function=SimpleNamespace(
        name="read_file", arguments='{"path":"missing"}'))] if with_tool else [])
    monkeypatch.setattr(agent, "check_lifecycle", fake_check)
    monkeypatch.setattr(agent, "call_model", lambda *args, **kwargs: FakeMessage(calls))
    monkeypatch.setattr(agent, "compress_history", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_fixed_context_breakdown", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_context_debug", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_state_debug", lambda state: None)
    # Running task must not touch actual workspace files. A guard rejection
    # is sufficient to complete one full, persisted tool-response turn.
    checkpoint = tmp_path / "run.json"
    agent.run_agent("test safe completion", checkpoint_path=str(checkpoint))
    stored = CheckpointStore(checkpoint, agent.WORKSPACE).load()
    assert stored.metrics.run_outcome == "complete"
    assert stored.metrics.model_turns == 1
    if with_tool:
        assert len(stored.history_turns) == 1
        assert stored.history_turns[0][-1]["role"] == "tool"


def test_compare_multiple_trials_by_run_id(tmp_path):
    (tmp_path / "ok.py").write_text("pass", encoding="utf-8")
    result_1 = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session())
    result_2 = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session("stopped"))
    result_1.run_id = "1"
    result_2.run_id = "2"
    assert len({(r.case_id, r.run_id) for r in (result_1, result_2)}) == 2
    assert compare_results([result_1, result_2], [result_1, result_2])["regressions"] == []
    with pytest.raises(EvaluationError):
        compare_results([result_1, result_1], [result_1, result_2])
