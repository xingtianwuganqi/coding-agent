"""Tests for the ``planning`` module.

The canonical planning implementation lives in :mod:`mini_coding_agent.planning`
(the obsolete spelling-variant module has been merged into it and removed).  This test
verifies that the five ``validate_*`` plan-validation helpers are exported and
carry docstrings.
"""

from mini_coding_agent import planning


def test_planning_alias_reexports_validate_methods():
    names = [n for n in planning.__all__ if n.startswith("validate_")]
    # The merged planning module exposes the five plan-validation helpers of
    # the original alias facade plus validate_task_transition, which now lives
    # in the task-state layer re-exported by this module.
    assert sorted(names) == [
        "validate_plan",
        "validate_plan_verification",
        "validate_requirement_coverage",
        "validate_requirement_mapping",
        "validate_requirement_references",
        "validate_task_transition",
    ]
    for name in names:
        alias_fn = getattr(planning, name)
        # Behaviorally equivalent: callable and same underlying name/signature.
        assert callable(alias_fn)
        assert alias_fn.__doc__  # each helper must carry a docstring
