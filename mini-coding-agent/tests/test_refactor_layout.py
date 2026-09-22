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
from mini_coding_agent.planing import AgentState, TodoItem
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.guard import guard_tool_call
from mini_coding_agent.prompts import SYSTEM_PROMPT
from mini_coding_agent.tool_runner import execute_tool
from mini_coding_agent.tools import resolve_path
from mini_coding_agent.tools_schema import TOOLS


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
    result = guard_tool_call("does_not_exist", {}, state)
    assert "RUNTIME GUARD" in result


def test_guard_blocks_retrieval_without_plan():
    state = AgentState()
    # Exhaust the pre-plan inspection budget so the guard fires.
    state.pre_plan_inspection_count = 4
    result = guard_tool_call("read_file", {"path": "pyproject.toml"}, state)
    assert "RUNTIME GUARD" in result
