from mini_coding_agent import planning
from mini_coding_agent import planing


def test_planning_alias_reexports_validate_methods():
    names = [n for n in planning.__all__ if n.startswith("validate_")]
    assert len(names) == 5
    for name in names:
        assert callable(getattr(planning, name))
        assert getattr(planning, name).__name__ == getattr(planing, name).__name__
        assert getattr(planning, name).__doc__
