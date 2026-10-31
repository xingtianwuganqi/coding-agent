"""Tests for the delete_file tool and its confirmation prompt."""

import builtins

from mini_coding_agent import tools as tools_module
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.planning import create_agent_state
from mini_coding_agent.tool_result import ToolResult
from mini_coding_agent.tool_runner import execute_tool


def run_delete(
        path: str,
        answer: str,
        monkeypatch,
        tmp_path,
        inputs=None,
):
    monkeypatch.setattr(tools_module, "WORKSPACE", tmp_path)

    if inputs is None:
        inputs = [answer]

    values = iter(inputs)
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(values)

    monkeypatch.setattr(builtins, "input", fake_input)

    state = create_agent_state(user_prompt="test task")

    result = execute_tool(
        name="delete_file",
        arguments={"path": path},
        state=state,
        metrics=AgentMetrics(),
        trace=AgentTrace(),
    )

    return result, state, prompts


def test_confirmation_prompt_shows_target_path(monkeypatch, tmp_path, capsys):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")

    result, state, prompts = run_delete(
        "note.txt", "y", monkeypatch, tmp_path
    )

    output = capsys.readouterr().out

    assert "delete_file" in output
    assert "Target file: note.txt" in output
    assert prompts == ["\nDo you want to allow this operation? [y/N]: "]
    assert "DEBUG" not in output

    assert isinstance(result, ToolResult)
    assert result.success
    assert "Deleted file: note.txt" in result.content
    assert not target.exists()

    assert state.workspace_revision == 1
    assert "note.txt" in state.changed_files


def test_denied_delete_keeps_the_file(monkeypatch, tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("hello", encoding="utf-8")

    result, state, _ = run_delete(
        "keep.txt", "n", monkeypatch, tmp_path
    )

    assert not result.success
    assert "denied" in result.error
    assert target.exists()
    assert state.workspace_revision == 0


def test_deleting_an_absent_file_is_a_noop(monkeypatch, tmp_path):
    result, state, _ = run_delete(
        "missing.txt", "y", monkeypatch, tmp_path
    )

    assert result.success
    assert "Already absent" in result.content
    assert state.workspace_revision == 0
    assert state.changed_files == set()


def test_deleting_a_directory_fails_without_crashing(
    monkeypatch, tmp_path
):
    directory = tmp_path / "some_dir"
    directory.mkdir()

    result, state, _ = run_delete(
        "some_dir", "y", monkeypatch, tmp_path
    )

    assert not result.success
    assert "regular file" in result.error
    assert directory.is_dir()
    assert state.workspace_revision == 0
