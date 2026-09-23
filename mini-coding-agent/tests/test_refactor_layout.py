"""Tests for the refactored mini_coding_agent package layout.

These tests lock in the public surface that used to live in ``main.py`` and
guard the thin entrypoint shims created by the refactor.
"""

import pytest

from mini_coding_agent import cli, main
from mini_coding_agent.agent import run_agent
from mini_coding_agent.config import (
    MAX_MODEL_TURN,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
    MAX_STEPS,
    WORKSPACE,
)
from mini_coding_agent.permission import Permission, classify_command
from mini_coding_agent.planing import AgentState, TodoItem, TaskStatus
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.guard import guard_tool_call
from mini_coding_agent.prompts import SYSTEM_PROMPT
from mini_coding_agent.evidence import CommandResult, EvidenceType
from mini_coding_agent.task_completion import (
    CompletionStatus, build_completion_feedback, evaluate_completion,
)
from mini_coding_agent.tool_runner import execute_tool
from mini_coding_agent.tools import resolve_path
from mini_coding_agent.tools_schema import TOOLS
from mini_coding_agent.verification import has_current_diff_verification


def test_entrypoint_reexports():
    # main.py and the package must expose the same callables.
    assert main.main is cli.main
    assert main.run_agent is run_agent
    assert callable(main.read_task)


def test_console_script_target_is_importable():
    # pyproject [project.scripts] -> mini_coding_agent.main:main
    assert callable(main.main)


def test_config_constants_are_sane():
    assert MAX_MODEL_TURN > 0
    assert MAX_STEPS > 0
    assert 0 < MAX_READ_LINES
    assert MAX_SEARCH_RESULTS > 0
    assert WORKSPACE.is_dir()


def test_tools_schema_shape():
    assert isinstance(TOOLS, list) and TOOLS
    for tool in TOOLS:
        assert tool["type"] == "function"
        # The project keeps tools in the compact OpenAI-compatible shape
        # and expands them into {"type", "function"} inside model_api.
        body = tool.get("function", tool)
        assert "name" in body
        assert "description" in body


def test_system_prompt_non_empty():
    assert isinstance(SYSTEM_PROMPT, str)
    assert SYSTEM_PROMPT.strip()


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git status", Permission.SAFE),
        ("ls -la", Permission.SAFE),
        ("pwd", Permission.SAFE),
        ("git commit -m x", Permission.CONFIRM),
        ("rm -rf /", Permission.BLOCKED),
        ("echo hi > f", Permission.BLOCKED),
    ],
)
def test_classify_command(command, expected):
    assert classify_command(command) == expected


def test_resolve_path_blocks_escape():
    assert resolve_path("pyproject.toml").name == "pyproject.toml"
    with pytest.raises(ValueError):
        resolve_path("../outside_workspace.txt")


def test_execute_tool_dispatches_unknown_tool():
    # With a plan in place the guard passes and dispatch reports the
    # unknown tool name instead of misreporting it as blocked.
    state = AgentState()
    state.todos = [TodoItem(id=1, content="inspect")]
    result = execute_tool(
        "does_not_exist", {}, state, AgentMetrics(), AgentTrace()
    )
    assert result.error == "Unknown tool: does_not_exist"


def test_guard_blocks_unknown_tool_without_plan():
    state = AgentState()
    result = guard_tool_call(
        "does_not_exist", {}, state, AgentMetrics(), AgentTrace()
    )
    assert "RUNTIME GUARD" in result


def test_guard_blocks_retrieval_without_plan():
    state = AgentState()
    # Exhaust the pre-plan inspection budget so the guard fires.
    state.pre_plan_inspection_count = 4
    result = guard_tool_call(
        "read_file", {"path": "pyproject.toml"}, state,
        AgentMetrics(), AgentTrace(),
    )
    assert "RUNTIME GUARD" in result


def test_guard_blocks_requirements_after_locking():
    state = AgentState()
    state.requirements.locked = True
    result = guard_tool_call(
        "set_requirements", {"requirements": []}, state,
        AgentMetrics(), AgentTrace(),
    )
    assert "already locked" in result


def test_guard_blocks_plan_replacement():
    state = AgentState()
    state.requirements.locked = True
    state.todos = [TodoItem(id=1, content="inspect")]
    result = guard_tool_call(
        "set_plan", {"items": []}, state, AgentMetrics(), AgentTrace()
    )
    assert "already exists" in result


@pytest.mark.parametrize(
    "requirements",
    [[], [{"kind": "soft_constraint", "description": "Keep the change small"}]],
)
def test_requirements_plan_and_get_plan_workflow(requirements):
    state = AgentState()
    metrics = AgentMetrics()
    trace = AgentTrace()

    assert guard_tool_call(
        "set_requirements", {"requirements": requirements}, state, metrics, trace
    ) is None
    result = execute_tool(
        "set_requirements", {"requirements": requirements}, state, metrics, trace
    )
    assert result.success
    assert state.requirements.locked

    items = [{"content": "Inspect source", "required_evidence": "none"}]
    assert guard_tool_call("set_plan", {"items": items}, state, metrics, trace) is None
    result = execute_tool("set_plan", {"items": items}, state, metrics, trace)
    assert result.success
    assert guard_tool_call("get_plan", {}, state, metrics, trace) is None
    assert "Inspect source" in execute_tool(
        "get_plan", {}, state, metrics, trace
    ).content


def test_successful_diff_command_records_current_revision(monkeypatch):
    from mini_coding_agent import tool_runner

    state = AgentState()
    state.workspace_revision = 1
    monkeypatch.setattr(
        tool_runner, "run_command",
        lambda **kwargs: CommandResult("git diff", "diff output", "", 0),
    )

    result = execute_tool(
        "run_command", {"command": "git diff"},
        state, AgentMetrics(), AgentTrace(),
    )

    assert result.success
    assert result.return_code == 0
    assert state.evidence[EvidenceType.DIFF_INSPECTED] == 1
    assert has_current_diff_verification(state)


def test_command_rejected_before_execution_does_not_record_verification(monkeypatch):
    from mini_coding_agent import tool_runner

    state = AgentState()
    monkeypatch.setattr(
        tool_runner, "run_command", lambda **kwargs: "Command timed out")

    result = execute_tool(
        "run_command", {"command": "git diff"},
        state, AgentMetrics(), AgentTrace(),
    )

    assert not result.success
    assert not state.verification.records
    assert not state.evidence


def test_blocked_completion_reports_missing_diff_without_claiming_success():
    state = AgentState()
    state.requirements.locked = True
    state.workspace_revision = 1
    state.changed_files.add("SUMMARY.md")
    state.todos = [
        TodoItem(id=1, content="Update summary", status=TaskStatus.COMPLETED),
        TodoItem(id=2, content="Inspect diff", status=TaskStatus.BLOCKED),
    ]

    report = evaluate_completion(state)

    assert report.complete == CompletionStatus.BLOCKED
    assert "diff_verified" in {item.name for item in report.missing}
    assert "diff_verified" in build_completion_feedback(report, state)


def test_pending_task_remains_incomplete_even_with_blocked_task():
    state = AgentState()
    state.requirements.locked = True
    state.todos = [
        TodoItem(id=1, content="Inspect diff", status=TaskStatus.BLOCKED),
        TodoItem(id=2, content="Finish work", status=TaskStatus.PENDING),
    ]

    assert evaluate_completion(state).complete == CompletionStatus.INCOMPLETE
