import pytest

from .calculator import add, divide, multiply, subtract


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_subtract_negative_result():
    assert subtract(3, 5) == -2


def test_multiply():
    assert multiply(4, 3) == 12


def test_multiply_by_zero():
    assert multiply(7, 0) == 0


def test_divide():
    assert divide(6, 3) == 2


def test_divide_returns_float():
    assert divide(7, 2) == 3.5


def test_divide_by_zero_raises():
    with pytest.raises(ValueError):
        divide(1, 0)
