"""Tests for must_change requirement target normalization/resolution."""

import builtins

import pytest

from mini_coding_agent import requirements as requirements_module
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.planning import create_agent_state
from mini_coding_agent.requirements import (
    evaluate_requirement,
    normalize_file_target,
    resolve_file_target,
    set_requirements,
)
from mini_coding_agent.tool_runner import execute_tool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(requirements_module, "WORKSPACE", tmp_path)
    return tmp_path


def make_file(root, relative_path, content="x"):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_unique_bare_filename_is_completed(workspace):
    make_file(workspace, "src/pkg/guard.py")

    assert resolve_file_target("guard.py") == "src/pkg/guard.py"


def test_full_relative_path_is_kept(workspace):
    make_file(workspace, "src/pkg/guard.py")

    assert (
        resolve_file_target("src/pkg/guard.py")
        == "src/pkg/guard.py"
    )


def test_leading_dot_slash_is_accepted(workspace):
    make_file(workspace, "src/pkg/guard.py")

    assert (
        resolve_file_target("./src/pkg/guard.py")
        == "src/pkg/guard.py"
    )


def test_ambiguous_bare_filename_is_rejected(workspace):
    make_file(workspace, "src/a/guard.py")
    make_file(workspace, "src/b/guard.py")

    with pytest.raises(ValueError, match="Ambiguous file target"):
        resolve_file_target("guard.py")


def test_ambiguous_target_uses_resolver(workspace):
    make_file(workspace, "src/a/guard.py")
    make_file(workspace, "src/b/guard.py")

    calls = []

    def resolver(target, matches):
        calls.append((target, list(matches)))
        return "src/b/guard.py"

    assert (
        resolve_file_target("guard.py", resolver=resolver)
        == "src/b/guard.py"
    )
    assert calls == [
        ("guard.py", ["src/a/guard.py", "src/b/guard.py"])
    ]


def test_ambiguous_target_resolver_returning_none_is_rejected(workspace):
    make_file(workspace, "src/a/guard.py")
    make_file(workspace, "src/b/guard.py")

    with pytest.raises(ValueError, match="No file was selected"):
        resolve_file_target(
            "guard.py",
            resolver=lambda _target, _matches: None,
        )


def test_set_requirements_asks_user_for_ambiguous_target(
    workspace, monkeypatch, capsys
):
    make_file(workspace, "src/a/guard.py")
    make_file(workspace, "src/b/guard.py")

    monkeypatch.setattr(
        builtins, "input", lambda _prompt="": "2"
    )

    state = create_agent_state(user_prompt="Add comments")
    trace = AgentTrace()

    result = execute_tool(
        name="set_requirements",
        arguments={
            "requirements": [
                {
                    "kind": "must_change",
                    "description": "Add comments to guard",
                    "target": "guard.py",
                }
            ]
        },
        state=state,
        metrics=AgentMetrics(),
        trace=trace,
    )

    output = capsys.readouterr().out

    assert "src/a/guard.py" in output
    assert "src/b/guard.py" in output

    assert result.success
    assert state.requirements.locked
    assert (
        state.requirements.items[0].target
        == "src/b/guard.py"
    )

    assert any(
        event.event_type == "requirement_target_choice"
        and "src/b/guard.py" in event.detail
        for event in trace.events
    )


def test_ambiguous_target_user_selecting_zero_keeps_error(
    workspace, monkeypatch
):
    make_file(workspace, "src/a/guard.py")
    make_file(workspace, "src/b/guard.py")

    monkeypatch.setattr(
        builtins, "input", lambda _prompt="": "0"
    )

    state = create_agent_state(user_prompt="Add comments")

    result = execute_tool(
        name="set_requirements",
        arguments={
            "requirements": [
                {
                    "kind": "must_change",
                    "description": "Add comments to guard",
                    "target": "guard.py",
                }
            ]
        },
        state=state,
        metrics=AgentMetrics(),
        trace=AgentTrace(),
    )

    assert not result.success
    assert "REQUIREMENT_VALIDATION_ERROR" in result.error
    assert "No file was selected" in result.error
    assert not state.requirements.locked


def test_multiple_ambiguous_targets_ask_once_per_target(
    workspace, monkeypatch
):
    make_file(workspace, "src/a/guard.py")
    make_file(workspace, "src/b/guard.py")
    make_file(workspace, "src/c/util.py")
    make_file(workspace, "src/d/util.py")

    answers = iter(["1", "2"])

    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt="": next(answers),
    )

    state = create_agent_state(user_prompt="Add comments")

    result = execute_tool(
        name="set_requirements",
        arguments={
            "requirements": [
                {
                    "kind": "must_change",
                    "description": "Change guard",
                    "target": "guard.py",
                },
                {
                    "kind": "must_change",
                    "description": "Change util",
                    "target": "util.py",
                },
            ]
        },
        state=state,
        metrics=AgentMetrics(),
        trace=AgentTrace(),
    )

    assert result.success
    assert state.requirements.items[0].target == "src/a/guard.py"
    assert state.requirements.items[1].target == "src/d/util.py"


def test_directory_target_is_rejected(workspace):
    (workspace / "src" / "pkg").mkdir(parents=True)

    with pytest.raises(ValueError, match="directory, not a file"):
        resolve_file_target("src/pkg")


def test_missing_bare_filename_is_rejected(workspace):
    with pytest.raises(ValueError, match="File not found in workspace"):
        resolve_file_target("guard.py")


def test_ignored_directories_do_not_create_ambiguity(workspace):
    make_file(workspace, "src/pkg/guard.py")
    make_file(workspace, ".venv/lib/guard.py")

    assert resolve_file_target("guard.py") == "src/pkg/guard.py"


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "~/secret.py",
        "C:/secret.py",
        "../outside.py",
        "src/../../outside.py",
        "guard_recent_tool_call",
    ],
)
def test_invalid_targets_are_rejected(workspace, target):
    with pytest.raises(ValueError):
        normalize_file_target(target)


def test_set_requirements_stores_resolved_relative_path(workspace):
    make_file(workspace, "src/pkg/guard.py")

    state = create_agent_state(user_prompt="Add comments")

    result = set_requirements(
        state,
        [
            {
                "kind": "must_change",
                "description": "Add comments to guard",
                "target": "guard.py",
            }
        ],
    )

    assert result.success
    assert state.requirements.locked
    assert state.requirements.items[0].target == "src/pkg/guard.py"

    state.changed_files.add("src/pkg/guard.py")

    check = evaluate_requirement(
        state, state.requirements.items[0]
    )

    assert check.satisfied


def test_set_requirements_rejects_ambiguous_bare_filename(workspace):
    make_file(workspace, "src/a/guard.py")
    make_file(workspace, "src/b/guard.py")

    state = create_agent_state(user_prompt="Add comments")

    result = set_requirements(
        state,
        [
            {
                "kind": "must_change",
                "description": "Add comments to guard",
                "target": "guard.py",
            }
        ],
    )

    assert not result.success
    assert "REQUIREMENT_VALIDATION_ERROR" in result.error
    assert not state.requirements.locked


def test_must_change_without_target_still_requires_any_change(workspace):
    state = create_agent_state(user_prompt="Make any change")

    result = set_requirements(
        state,
        [
            {
                "kind": "must_change",
                "description": "Some change must happen",
            }
        ],
    )

    assert result.success
    assert state.requirements.items[0].target is None

    check = evaluate_requirement(
        state, state.requirements.items[0]
    )
    assert not check.satisfied

    state.changed_files.add("src/pkg/anything.py")

    check = evaluate_requirement(
        state, state.requirements.items[0]
    )
    assert check.satisfied
