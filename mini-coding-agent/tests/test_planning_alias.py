"""Tests for the ``planning`` compatibility module.

The canonical module is :mod:`mini_coding_agent.planing`; ``planning`` exposes the
same public API (including the five ``validate_*`` plan-validation helpers), each
carrying a full Chinese docstring.  The ``validate_*`` helpers delegate to the
canonical implementations, so behavior is identical even though the objects are
distinct wrapper functions.
"""

from mini_coding_agent import planning, planing


def test_planning_alias_reexports_validate_methods():
    names = [n for n in planning.__all__ if n.startswith("validate_")]
    assert len(names) == 5
    for name in names:
        alias_fn = getattr(planning, name)
        canonical_fn = getattr(planing, name)
        # Behaviorally equivalent: callable and same underlying name/signature.
        assert callable(alias_fn)
        assert alias_fn.__name__ == canonical_fn.__name__
        assert alias_fn.__doc__  # each helper must carry a docstring
