"""Lesson 25: safe, versioned JSON checkpoints for one coding-agent run.

A checkpoint is an execution cursor, NOT a filesystem backup.
An uncertain in-flight tool call must never be replayed automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .agent_metrics import AgentMetrics
from .agent_trace import AgentTrace, TraceEvent
from .evidence import EvidenceType
from .goal import GoalState
from .planning import (
    AgentState, FailureState, PlanSnapshot, PlanState, PlanStatus,
    TaskStatus, TodoItem, TodoKind, VerificationState, ReplanReason,
)
from .requirements import RequirementState, RequirementKind, TaskRequirement
from .runtime_lifecycle import ProgressTracker
from .verification import VerificationKind, VerificationRecord

SCHEMA_VERSION = 1
SKIP_DIRS = {
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'node_modules', '.mini_agent',
    'dist', 'build',
}
SKIP_FILES = {'.DS_Store'}
MAX_FINGERPRINT_FILES = 10000

_TYPES = {t.__name__: t for t in (
    AgentState, GoalState, PlanState, PlanSnapshot, FailureState,
    VerificationState, TodoItem, TaskRequirement, RequirementState,
    VerificationRecord, AgentMetrics, AgentTrace, TraceEvent, ProgressTracker,
)}
_ENUMS = {t.__name__: t for t in (
    EvidenceType, PlanStatus, TaskStatus, TodoKind, ReplanReason,
    RequirementKind, VerificationKind,
)}


class CheckpointError(RuntimeError):
    """Unusable or incompatible checkpoint; do not silently start over."""


class RecoveryRequired(CheckpointError):
    """A tool may have executed without its result being committed."""


@dataclass
class RunSession:
    state: AgentState
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    trace: AgentTrace = field(default_factory=AgentTrace)
    progress: ProgressTracker = field(default_factory=ProgressTracker)
    history_turns: list[list[dict[str, Any]]] = field(default_factory=list)
    # Assistant message plus tool responses not yet moved to history_turns.
    inflight_turn: list[dict[str, Any]] | None = None
    # A tool is marked pending BEFORE it is invoked, then cleared AFTER its
    # ToolResult and AgentState have both been saved.
    pending_tool: dict[str, Any] | None = None


_TYPES['RunSession'] = RunSession


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {'$enum': type(value).__name__, '$value': value.value}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            '$dataclass': type(value).__name__,
            '$fields': {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, set):
        return {'$set': [_encode(v) for v in sorted(value, key=str)]}
    if isinstance(value, tuple):
        return {'$tuple': [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value):
            return {k: _encode(v) for k, v in value.items()}
        return {'$pairs': [[_encode(k), _encode(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f'Cannot checkpoint {type(value).__name__}')


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {'$enum', '$value'}:
        enum_cls = _ENUMS.get(value['$enum'])
        if enum_cls is None:
            raise CheckpointError(f'Unknown enum: {value["$enum"]}')
        return enum_cls(value['$value'])
    if set(value) == {'$dataclass', '$fields'}:
        cls = _TYPES.get(value['$dataclass'])
        if cls is None:
            raise CheckpointError(f'Unknown state type: {value["$dataclass"]}')
        return cls(**{k: _decode(v) for k, v in value['$fields'].items()})
    if set(value) == {'$set'}:
        return set(_decode(v) for v in value['$set'])
    if set(value) == {'$tuple'}:
        return tuple(_decode(v) for v in value['$tuple'])
    if set(value) == {'$pairs'}:
        return {_decode(k): _decode(v) for k, v in value['$pairs']}
    return {k: _decode(v) for k, v in value.items()}


def workspace_fingerprint(root: Path) -> str:
    """Hash current non-ignored workspace files, excluding checkpoints.

    Large binary files are read in chunks; this is intentionally conservative
    for the learning project, not optimized for giant monorepos.
    """
    root = root.resolve()
    digest = hashlib.sha256()
    count = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and not (Path(directory) / d).is_symlink())
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if filename in SKIP_FILES or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith('.mini_agent/'):
                continue
            count += 1
            if count > MAX_FINGERPRINT_FILES:
                raise CheckpointError('Too many workspace files for fingerprint')
            digest.update(relative.encode('utf-8'))
            digest.update(b'\0')
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


class CheckpointStore:
    def __init__(self, path: Path, workspace: Path):
        self.path = Path(path).resolve()
        self.workspace = Path(workspace).resolve()

    def save(self, session: RunSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            'schema': SCHEMA_VERSION,
            'workspace': str(self.workspace),
            'workspace_fingerprint': workspace_fingerprint(self.workspace),
            'session': _encode(session),
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
        envelope = {'sha256': hashlib.sha256(body).hexdigest(), 'payload': payload}
        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                             indent=2, allow_nan=False).encode('utf-8')
        tmp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            # Sync the renamed directory entry too (works on POSIX).
            if os.name == 'posix':
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if tmp.exists():
                tmp.unlink()

    def load(self) -> RunSession:
        if not self.path.is_file():
            raise CheckpointError(f'Checkpoint not found: {self.path}')
        try:
            envelope = json.loads(self.path.read_text(encoding='utf-8'))
            payload = envelope['payload']
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False).encode('utf-8')
            if hashlib.sha256(body).hexdigest() != envelope['sha256']:
                raise CheckpointError('Checkpoint checksum mismatch')
            if payload['schema'] != SCHEMA_VERSION:
                raise CheckpointError('Unsupported checkpoint schema')
            if payload['workspace'] != str(self.workspace):
                raise CheckpointError('Checkpoint belongs to another workspace')
            session = _decode(payload['session'])
            if not isinstance(session, RunSession):
                raise CheckpointError('Checkpoint has invalid session data')
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CheckpointError(f'Invalid checkpoint: {exc}') from exc
        if session.pending_tool is not None:
            tool = session.pending_tool
            raise RecoveryRequired(
                f'UNCERTAIN tool {tool.get("name")} '
                f'call_id={tool.get("call_id")}; do not auto-replay. '
                'Inspect workspace and reconcile manually.'
            )
        if session.inflight_turn is not None:
            raise RecoveryRequired(
                'Partial model tool-call turn exists. '
                'Do not auto-replay missing tool calls.'
            )
        current = workspace_fingerprint(self.workspace)
        if current != payload['workspace_fingerprint']:
            raise RecoveryRequired(
                'Workspace changed since checkpoint. '
                'Inspect changes before restoring state or evidence.'
            )
        return session

    def begin_turn(self, session: RunSession, assistant: dict[str, Any]) -> None:
        if session.inflight_turn is not None:
            raise CheckpointError('Cannot begin turn with unfinished tool messages')
        session.inflight_turn = [assistant]
        self.save(session)

    def before_tool(self, session: RunSession, call_id: str,
                    name: str, arguments: dict[str, Any]) -> None:
        if session.inflight_turn is None or session.pending_tool is not None:
            raise CheckpointError('Invalid pending tool transition')
        # Do not store arguments twice; assistant message already contains them.
        session.pending_tool = {'call_id': call_id, 'name': name}
        self.save(session)

    def after_tool(self, session: RunSession, tool_output: dict[str, Any]) -> None:
        if session.pending_tool is None or session.inflight_turn is None:
            raise CheckpointError('No pending tool to commit')
        if tool_output.get('tool_call_id') != session.pending_tool['call_id']:
            raise CheckpointError('Tool response ID does not match pending call')
        session.inflight_turn.append(tool_output)
        session.pending_tool = None
        self.save(session)

    def finish_turn(self, session: RunSession) -> None:
        if session.pending_tool is not None or session.inflight_turn is None:
            raise CheckpointError('Cannot commit incomplete turn')
        session.history_turns.append(session.inflight_turn)
        session.inflight_turn = None
        self.save(session)
