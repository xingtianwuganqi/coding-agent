"""Focused regression tests for SUMMARY.md section 21 (Task State Machine).

Section 21 documents one known issue:

    can_transition_task queries the migration table with
    ``TaskStatus.get(current, set())`` instead of
    ``TASK_TRANSITIONS.get(current, set())``, so the migration table
    never takes effect.

These tests pin the documented contract so that regression cannot
return silently.
"""

from mini_coding_agent.planing import (
    TASK_TRANSITIONS,
    TaskStatus,
    TodoItem,
    TodoKind,
    can_transition_task,
    create_agent_state,
    update_task,
)
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.evidence import EvidenceType


def test_migration_table_matches_documented_edges():
    assert TASK_TRANSITIONS[TaskStatus.PENDING] == {TaskStatus.IN_PROGRESS}
    assert TASK_TRANSITIONS[TaskStatus.IN_PROGRESS] == {
        TaskStatus.COMPLETED,
        TaskStatus.BLOCKED,
    }
    assert TASK_TRANSITIONS[TaskStatus.BLOCKED] == {TaskStatus.IN_PROGRESS}
    assert TASK_TRANSITIONS[TaskStatus.COMPLETED] == set()


def test_can_transition_task_reads_the_migration_table():
    # Legal documented edges.
    assert can_transition_task(TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
    assert can_transition_task(TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED)
    assert can_transition_task(TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)
    assert can_transition_task(TaskStatus.BLOCKED, TaskStatus.IN_PROGRESS)

    # Illegal documented edges.
    assert not can_transition_task(TaskStatus.PENDING, TaskStatus.COMPLETED)
    assert not can_transition_task(TaskStatus.BLOCKED, TaskStatus.COMPLETED)
    assert not can_transition_task(TaskStatus.COMPLETED, TaskStatus.IN_PROGRESS)
    assert not can_transition_task(TaskStatus.COMPLETED, TaskStatus.BLOCKED)


def test_enum_has_no_get_attribute():
    # The buggy variant called ``TaskStatus.get(...)``; an Enum has no
    # usable ``get``, which is why the migration table silently failed.
    assert not hasattr(TaskStatus, "get")


def test_task_completion_uses_current_workspace_evidence():
    state = create_agent_state("Verify a documentation change")
    state.workspace_revision = 1
    state.code_revision = 0
    todo = TodoItem(
        id=1,
        content="Run tests",
        status=TaskStatus.IN_PROGRESS,
        required_evidence=EvidenceType.TESTS_PASSED,
        kind=TodoKind.VERIFICATION,
    )
    state.plan.items = [todo]
    state.evidence[EvidenceType.TESTS_PASSED] = 1

    result = update_task(state, AgentMetrics(), 1, "completed")

    assert result.success
    assert todo.status == TaskStatus.COMPLETED


def test_task_completion_rejects_stale_workspace_evidence():
    state = create_agent_state("Verify a documentation change")
    state.workspace_revision = 2
    state.code_revision = 0
    todo = TodoItem(
        id=1,
        content="Run tests",
        status=TaskStatus.IN_PROGRESS,
        required_evidence=EvidenceType.TESTS_PASSED,
        kind=TodoKind.VERIFICATION,
    )
    state.plan.items = [todo]
    state.evidence[EvidenceType.TESTS_PASSED] = 1

    result = update_task(state, AgentMetrics(), 1, "completed")

    assert not result.success
    assert todo.status == TaskStatus.IN_PROGRESS
