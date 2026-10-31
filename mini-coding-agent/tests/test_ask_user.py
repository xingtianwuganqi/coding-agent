"""Tests for the interactive ask_user tool."""

import builtins

from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.guard import guard_tool_call
from mini_coding_agent.permission import TOOL_PERMISSIONS, Permission
from mini_coding_agent.planning import create_agent_state
from mini_coding_agent.tool_runner import execute_tool
from mini_coding_agent.tools import ask_user
from mini_coding_agent.tools_schema import TOOLS


def fake_input(*answers):
    values = iter(answers)

    def _input(_prompt=""):
        return next(values)

    return _input


def test_ask_user_returns_selected_option(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input("2"))

    result = ask_user(
        question="Which approach should I use?",
        options=["Refactor in place", "Create a new module"],
    )

    assert result.success
    assert "2" in result.content
    assert "Create a new module" in result.content


def test_ask_user_accepts_zero_for_no_answer(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input("0"))

    result = ask_user(
        question="Which approach should I use?",
        options=["Option A", "Option B"],
    )

    assert result.success
    assert "0" in result.content
    assert "none of the options apply" in result.content


def test_ask_user_treats_empty_answer_as_zero(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input(""))

    result = ask_user(
        question="Which approach should I use?",
        options=["Option A", "Option B"],
    )

    assert result.success
    assert "0" in result.content


def test_ask_user_reprompts_on_invalid_answer(monkeypatch):
    monkeypatch.setattr(
        builtins,
        "input",
        fake_input("7", "not-a-number", "1"),
    )

    result = ask_user(
        question="Which approach should I use?",
        options=["Option A", "Option B"],
    )

    assert result.success
    assert "Option A" in result.content


def test_ask_user_gives_up_after_repeated_invalid_answers(monkeypatch):
    monkeypatch.setattr(
        builtins,
        "input",
        fake_input("x", "y", "z"),
    )

    result = ask_user(
        question="Which approach should I use?",
        options=["Option A", "Option B"],
    )

    assert result.success
    assert "no option selected" in result.content


def test_ask_user_handles_closed_input(monkeypatch):
    def _raise(_prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", _raise)

    result = ask_user(
        question="Which approach should I use?",
        options=["Option A", "Option B"],
    )

    assert result.success
    assert "did not answer" in result.content


def test_ask_user_rejects_empty_options():
    result = ask_user(question="Anything?", options=[])

    assert not result.success
    assert "option" in result.error


def test_ask_user_is_registered_as_a_safe_tool():
    names = {
        tool.get("name")
        or tool.get("function", {}).get("name")
        for tool in TOOLS
    }

    assert "ask_user" in names
    assert TOOL_PERMISSIONS["ask_user"] == Permission.SAFE


def test_ask_user_is_allowed_before_goal_is_locked():
    state = create_agent_state(user_prompt="test task")

    assert not state.goal.locked

    error = guard_tool_call(
        name="ask_user",
        arguments={
            "question": "Which approach?",
            "options": ["A", "B"],
        },
        state=state,
        metrics=AgentMetrics(),
        trace=AgentTrace(),
    )

    assert error is None


def test_execute_tool_dispatches_ask_user(monkeypatch):
    monkeypatch.setattr(builtins, "input", fake_input("1"))

    result = execute_tool(
        name="ask_user",
        arguments={
            "question": "Which approach?",
            "options": ["Option A", "Option B"],
        },
        state=create_agent_state(user_prompt="test task"),
        metrics=AgentMetrics(),
        trace=AgentTrace(),
    )

    assert result.success
    assert "Option A" in result.content
