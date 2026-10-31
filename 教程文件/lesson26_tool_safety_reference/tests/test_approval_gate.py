"""Lesson 26: approval != ask_user; denial != execution error."""
from types import SimpleNamespace
import builtins
import pytest

from mini_coding_agent.approval import (
    ApprovalStatus, authorize_tool_call, policy_for_tool,
)
from mini_coding_agent.permission import Permission, ask_for_confirmation
from mini_coding_agent.checkpoint import CheckpointStore, RunSession
from mini_coding_agent.planning import create_agent_state
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.tool_runner import execute_tool_with_recovery
from mini_coding_agent import tools as tools_module


@pytest.mark.parametrize('name,args,expected', [
    ('read_file', {'path': 'note.txt'}, Permission.SAFE),
    ('write_file', {'path': 'note.txt', 'content': 'hello'}, Permission.WRITE),
    ('delete_file', {'path': 'note.txt'}, Permission.CONFIRM),
    ('run_command', {'command': 'git diff'}, Permission.SAFE),
    ('run_command', {'command': 'git diff --output=report.txt'}, Permission.CONFIRM),
    ('run_command', {'command': 'pytest -q'}, Permission.CONFIRM),
    ('run_command', {'command': 'uv run pytest'}, Permission.CONFIRM),
    ('run_command', {'command': 'rm -rf /'}, Permission.BLOCKED),
    ('unknown_tool', {}, Permission.BLOCKED),
])
def test_policy_classification(name, args, expected):
    assert policy_for_tool(name, args) == expected


def test_explicit_approval_allows_exact_operation():
    requested = []
    def approve(command, detail):
        requested.append((command, detail))
        return True

    result = authorize_tool_call(
        'delete_file', {'path': 'note.txt'}, confirm=approve,
    )
    assert result.status == ApprovalStatus.APPROVED
    assert result.allowed
    assert requested == [('delete_file', 'Target file: note.txt')]


def test_denial_is_cached_within_one_run_not_reprompted():
    denied = set()
    asked = []
    def refuse(name, detail):
        asked.append((name, detail))
        return False

    first = authorize_tool_call(
        'delete_file', {'path': 'keep.txt'}, denied_signatures=denied, confirm=refuse,
    )
    second = authorize_tool_call(
        'delete_file', {'path': 'keep.txt'}, denied_signatures=denied, confirm=refuse,
    )
    assert first.status == second.status == ApprovalStatus.DENIED
    assert len(asked) == 1
    assert first.signature in denied


def test_new_args_require_new_approval():
    denied = set()
    called = []
    def refuse(name, detail):
        called.append(detail)
        return False
    authorize_tool_call('delete_file', {'path': 'a.txt'}, denied_signatures=denied, confirm=refuse)
    authorize_tool_call('delete_file', {'path': 'b.txt'}, denied_signatures=denied, confirm=refuse)
    assert len(called) == 2


def test_noninteractive_confirmation_defaults_to_deny(monkeypatch):
    def closed(_prompt=''):
        raise EOFError()
    monkeypatch.setattr(builtins, 'input', closed)
    assert ask_for_confirmation('delete_file') is False
    assert authorize_tool_call('delete_file', {'path': 'a.txt'}).status == ApprovalStatus.DENIED


def test_user_denial_skips_generic_failure_recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_module, 'WORKSPACE', tmp_path)
    f = tmp_path / 'keep.txt'
    f.write_text('hello')
    monkeypatch.setattr(builtins, 'input', lambda _='': 'n')
    metrics = AgentMetrics()
    state = create_agent_state('test')
    result = execute_tool_with_recovery(
        'delete_file', {'path': 'keep.txt'},
        state, metrics, AgentTrace(), current_turn=1,
    )
    assert not result.success
    assert result.failure_code == 'USER_DENIED'
    assert metrics.auto_retries == 0
    assert metrics.tool_failures == 0
    assert metrics.tool_executions == 0  # no side-effectful tool dispatched
    assert f.exists()


def test_skipped_tool_checkpoint_is_safe_to_load(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = CheckpointStore(tmp_path / 'checkpoint.json', workspace)
    session = RunSession(state=create_agent_state('test'))
    session.denied_approvals.add('a-signature')
    assistant = {'role': 'assistant', 'tool_calls': [
        {'id': 'call1', 'function': {'name': 'delete_file', 'arguments': '{}'}}
    ]}
    store.begin_turn(session, assistant)
    store.skip_tool(session, {
        'role': 'tool', 'tool_call_id': 'call1', 'content': 'USER_DENIED',
    })
    store.finish_turn(session)
    loaded = store.load()
    assert loaded.denied_approvals == {'a-signature'}
    assert loaded.pending_tool is None
    assert loaded.inflight_turn is None
    assert loaded.history_turns[0][1]['content'] == 'USER_DENIED'


def test_checkpoint_before_tool_keeps_uncertain_marker(tmp_path):
    from mini_coding_agent.checkpoint import RecoveryRequired
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = CheckpointStore(tmp_path / 'checkpoint.json', workspace)
    session = RunSession(state=create_agent_state('test'))
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    store.before_tool(session, 'call1', 'delete_file', {'path': 'keep.txt'})
    with pytest.raises(RecoveryRequired, match='UNCERTAIN tool'):
        store.load()


def test_agent_denial_saved_without_pending_tool_and_not_reprompted(monkeypatch, tmp_path):
    """Exercise the actual Agent Loop, not just the helper function."""
    import json
    from mini_coding_agent import agent
    from mini_coding_agent.planning import TodoItem

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = workspace / 'keep.txt'
    target.write_text('important')
    monkeypatch.setattr(agent, 'WORKSPACE', workspace)
    monkeypatch.setattr(tools_module, 'WORKSPACE', workspace)
    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 2)
    monkeypatch.setattr(agent, 'compress_history', lambda **kwargs: None)
    monkeypatch.setattr(agent, 'print_fixed_context_breakdown', lambda **kwargs: None)
    monkeypatch.setattr(agent, 'print_context_debug', lambda **kwargs: None)
    monkeypatch.setattr(agent, 'print_state_debug', lambda *args: None)

    original_create = agent.create_agent_state
    def setup_state(*args, **kwargs):
        state = original_create(*args, **kwargs)
        state.goal.locked = True
        state.requirements.locked = True
        state.plan.revision = 1
        state.plan.items = [TodoItem(id=1, content='Inspect deletion', status='pending')]
        # TaskStatus is a str-valued Enum in this project; use enum instance.
        from mini_coding_agent.planning import TaskStatus
        state.plan.items[0].status = TaskStatus.PENDING
        return state
    monkeypatch.setattr(agent, 'create_agent_state', setup_state)

    class Message:
        content = ''
        def __init__(self, idx):
            self.tool_calls = [SimpleNamespace(
                id=f'call_{idx}',
                function=SimpleNamespace(name='delete_file', arguments=json.dumps({'path': 'keep.txt'})),
            )]
        def model_dump(self, **kwargs):
            return {'role': 'assistant', 'tool_calls': [
                {'id': self.tool_calls[0].id, 'type': 'function', 'function': {
                    'name': 'delete_file', 'arguments': self.tool_calls[0].function.arguments,
                }}
            ]}

    turns = iter([Message(1), Message(2)])
    monkeypatch.setattr(agent, 'call_model', lambda *args, **kwargs: next(turns))
    called = []
    def deny(prompt=''):
        called.append(prompt)
        return 'n'
    monkeypatch.setattr(builtins, 'input', deny)
    checkpoint_path = tmp_path / 'checkpoint.json'
    output = agent.run_agent('test task', checkpoint_path=str(checkpoint_path))
    assert 'Outcome: stopped' in output
    assert len(called) == 1
    assert target.read_text() == 'important'
    loaded = CheckpointStore(checkpoint_path, workspace).load()
    assert len(loaded.history_turns) == 2
    assert loaded.pending_tool is None
    assert len(loaded.denied_approvals) == 1


@pytest.mark.parametrize('path', ['.env', '.git/config', '.venv/pyvenv.cfg', '.mini_agent/state.json'])
def test_sensitive_workspace_paths_cannot_be_read_or_modified(monkeypatch, tmp_path, path):
    from mini_coding_agent.tools import resolve_path
    monkeypatch.setattr(tools_module, 'WORKSPACE', tmp_path)
    with pytest.raises(ValueError, match='Protected workspace path'):
        resolve_path(path)
