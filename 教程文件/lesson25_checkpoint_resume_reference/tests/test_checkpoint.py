"""Lesson 25 checkpoint tests: no live model calls."""
from pathlib import Path

import pytest

from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.checkpoint import (
    CheckpointError, CheckpointStore, RecoveryRequired, RunSession,
)
from mini_coding_agent.evidence import EvidenceType
from mini_coding_agent.planning import (
    PlanSnapshot, PlanStatus, TaskStatus, TodoItem, TodoKind,
    create_agent_state,
)
from mini_coding_agent.requirements import RequirementKind, TaskRequirement
from mini_coding_agent.runtime_lifecycle import ProgressTracker
from mini_coding_agent.verification import VerificationKind, VerificationRecord


def setup_session(tmp_path: Path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'module.py').write_text('print(1)\n', encoding='utf-8')
    store = CheckpointStore(root / '.mini_agent' / 'checkpoint.json', root)
    session = RunSession(state=create_agent_state('refactor module'))
    return root, store, session


def test_full_state_roundtrip(tmp_path):
    root, store, session = setup_session(tmp_path)
    state = session.state
    state.goal.locked = True
    state.goal.objective = 'refactor module'
    state.requirements.locked = True
    state.requirements.items = [
        TaskRequirement(id=1, kind=RequirementKind.MUST_CHANGE,
                        description='update module', target='module.py')
    ]
    state.plan.revision = 2
    state.plan.replan_count = 1
    state.plan.items = [
        TodoItem(id=1, content='Change', status=TaskStatus.COMPLETED,
                 kind=TodoKind.IMPLEMENTATION, requirement_ids=[1]),
        TodoItem(id=2, content='Test', depends_on=[1],
                 required_evidence=EvidenceType.TESTS_PASSED,
                 reuse_from_task_id=3),
    ]
    state.plan.history = [
        PlanSnapshot(revision=1, items=[TodoItem(id=3, content='Old')],
                     status=PlanStatus.SUPERSEDED, reason='blocked',
                     created_turn=4, superseded_turn=7)
    ]
    state.workspace_revision = 5
    state.code_revision = 3
    state.changed_files = {'module.py'}
    state.evidence = {EvidenceType.TESTS_PASSED: 5}
    state.verification.records = [
        VerificationRecord(VerificationKind.TEST, True, 5, 3,
                           'pytest', 'all passed')
    ]
    session.progress = ProgressTracker(last_meaningful_turn=7,
                                       seen_milestones={'plan_created'})
    session.metrics = AgentMetrics(model_turns=8, tool_calls=10)
    session.history_turns = [[{'role': 'user', 'content': 'earlier check'}]]
    session.trace.record(7, 'tool', 'pytest', 'passed')
    store.save(session)
    loaded = store.load()
    assert loaded.state.goal.objective == 'refactor module'
    assert loaded.state.plan.history[0].items[0].id == 3
    assert loaded.state.plan.items[1].depends_on == [1]
    assert loaded.state.plan.items[1].reuse_from_task_id == 3
    assert loaded.state.changed_files == {'module.py'}
    assert loaded.state.evidence[EvidenceType.TESTS_PASSED] == 5
    assert loaded.state.verification.records[0].kind == VerificationKind.TEST
    assert loaded.progress.seen_milestones == {'plan_created'}
    assert loaded.metrics.model_turns == 8
    assert loaded.trace.events[0].event_type == 'tool'
    assert loaded.history_turns == session.history_turns


def test_safe_resume_after_completed_tool_batch(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.save(session)
    assistant = {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'type': 'function',
        'function': {'name': 'write_file', 'arguments': '{}'}}]}
    store.begin_turn(session, assistant)
    store.before_tool(session, 'c1', 'write_file', {})
    (root / 'module.py').write_text('print(2)\n', encoding='utf-8')
    session.state.workspace_revision += 1
    session.state.changed_files.add('module.py')
    store.after_tool(session, {'role': 'tool', 'tool_call_id': 'c1', 'content': 'ok'})
    store.finish_turn(session)
    loaded = store.load()
    assert loaded.inflight_turn is None
    assert loaded.pending_tool is None
    assert loaded.state.workspace_revision == 1
    assert len(loaded.history_turns) == 1
    assert loaded.history_turns[0][1]['tool_call_id'] == 'c1'


def test_crash_after_tool_change_is_not_replayed(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    store.before_tool(session, 'c2', 'delete_file', {})
    (root / 'module.py').unlink()  # side effect happened, no ToolResult saved
    with pytest.raises(RecoveryRequired, match='UNCERTAIN tool delete_file'):
        store.load()


def test_crash_with_partial_turn_requires_review(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    with pytest.raises(RecoveryRequired, match='Partial model'):
        store.load()


def test_external_change_rejected(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.save(session)
    (root / 'module.py').write_text('external edit\n', encoding='utf-8')
    with pytest.raises(RecoveryRequired, match='Workspace changed'):
        store.load()


def test_wrong_workspace_rejected(tmp_path):
    root, store, session = setup_session(tmp_path)
    store.save(session)
    other = tmp_path / 'other'; other.mkdir()
    wrong = CheckpointStore(store.path, other)
    with pytest.raises(CheckpointError, match='another workspace'):
        wrong.load()


def test_corrupt_checkpoint_rejected(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.save(session)
    data = store.path.read_text(encoding='utf-8')
    store.path.write_text(data.replace('refactor module', 'refactor others'), encoding='utf-8')
    with pytest.raises(CheckpointError, match='checksum mismatch'):
        store.load()


def test_cannot_finish_with_pending_tool(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    store.before_tool(session, 'c1', 'write_file', {})
    with pytest.raises(CheckpointError, match='incomplete turn'):
        store.finish_turn(session)


def test_unsupported_schema_rejected(tmp_path):
    _, store, session = setup_session(tmp_path)
    store.save(session)
    import json
    import hashlib
    envelope = json.loads(store.path.read_text(encoding='utf-8'))
    envelope['payload']['schema'] = 999
    payload = envelope['payload']
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()
    envelope['sha256'] = hashlib.sha256(body).hexdigest()
    store.path.write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(CheckpointError, match='Unsupported checkpoint schema'):
        store.load()


def test_run_agent_resumes_state_and_tool_history(tmp_path, monkeypatch):
    """Integration test with scripted model calls; no real API required."""
    from types import SimpleNamespace
    from mini_coding_agent import agent

    monkeypatch.setattr(agent, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 1)
    monkeypatch.setattr(agent, 'compress_history', lambda **kw: None)
    monkeypatch.setattr(agent, 'print_fixed_context_breakdown', lambda **kw: None)
    monkeypatch.setattr(agent, 'print_context_debug', lambda **kw: None)
    monkeypatch.setattr(agent, 'print_state_debug', lambda state: None)
    monkeypatch.setattr(agent.AgentTrace, 'print_trace', lambda self: None)
    monkeypatch.setattr(agent.AgentMetrics, 'print_summary', lambda self: None)

    class FakeMessage:
        def __init__(self, calls=None):
            self.tool_calls = calls or []
            self.content = ''
        def model_dump(self, **kw):
            return {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': c.id, 'type': 'function', 'function': {'name': c.function.name,
                                                         'arguments': c.function.arguments}}
                for c in self.tool_calls
            ]}

    call = SimpleNamespace(id='call-1', function=SimpleNamespace(
        name='set_goal', arguments='{"objective": "make the code safer"}'))
    model_calls = []
    def fake_model(*args, **kwargs):
        model_calls.append(kwargs)
        if len(model_calls) == 1:
            return FakeMessage([call])
        return FakeMessage()
    monkeypatch.setattr(agent, 'call_model', fake_model)

    cp = tmp_path / '.mini_agent' / 'test.json'
    first = agent.run_agent('make the code safer', checkpoint_path=str(cp))
    assert 'Outcome: stopped' in first
    loaded = CheckpointStore(cp, tmp_path).load()
    assert loaded.metrics.model_turns == 1
    assert loaded.state.goal.locked
    assert len(loaded.history_turns) == 1
    assert len(loaded.history_turns[0]) == 2

    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 3)
    second = agent.run_agent('', checkpoint_path=str(cp), resume=True)
    assert 'Outcome: stopped' in second
    loaded = CheckpointStore(cp, tmp_path).load()
    assert loaded.metrics.model_turns == 3
    assert len(loaded.history_turns) == 2
    assert loaded.history_turns[0][1]['tool_call_id'] == 'call-1'
