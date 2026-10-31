"""Trusted hidden tests. Keep OUTSIDE the Agent's editable workspace."""
import pytest

from calculator import divide


def test_normal_division():
    assert divide(10, 2) == 5


def test_division_by_zero_raises_value_error():
    with pytest.raises(ValueError):
        divide(10, 0)
