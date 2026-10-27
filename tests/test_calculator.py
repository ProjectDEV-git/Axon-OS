"""Tests for the Intent Bar's inline calculator (services/calculator.py)."""

from __future__ import annotations

import pytest

from services.calculator import calculate, format_number, looks_like_math
from services.suggestions import CALCULATOR, build_suggestions, default_index, describe

CASES = [
    ("12*7", "84"),
    ("2^10", "1024"),
    ("2**8", "256"),
    ("2^3^2", "512"),  # right-associative
    ("-2^2", "-4"),  # a power binds tighter than the sign
    ("2^-1", "0.5"),
    ("-2^0.5", "-1.414213562"),
    ("(1+2)*3", "9"),
    ("-(2+3)*2", "-10"),
    ("10/4", "2.5"),
    ("1/3", "0.3333333333"),
    ("0.1+0.2", "0.3"),
    ("3 × 4", "12"),
    ("8 ÷ 2", "4"),
    (".5*4", "2"),
    ("(5)", "5"),
    ("123456789*1000", "123456789000"),
]
NOT_MATH = [
    "5",
    "-5",
    "hello",
    "2024-10-26",  # a date, not 1988
    "555-12-34",
    "1/0",
    "2^1000",  # exponent limit
    "99999999*99999999",  # result limit
    "(-2)^0.5",  # complex
    "1.2.3+4",
    "((2)",
    "2-",
    "1e5+1",
    "3,4+1",
    "x" * 90,
]


@pytest.mark.parametrize(("query", "expected"), CASES)
def test_calculates(query: str, expected: str) -> None:
    assert calculate(query) == expected


@pytest.mark.parametrize("query", NOT_MATH)
def test_leaves_other_text_alone(query: str) -> None:
    assert calculate(query) is None


def test_long_input_is_never_parsed() -> None:
    assert not looks_like_math("1+" * 50 + "1")


def test_number_format() -> None:
    assert format_number(2.0) == "2"
    assert format_number(1e15) is None
    assert format_number(float("nan")) is None
    assert format_number(1e-9) is None
    assert format_number(0.0) == "0"


def test_calculator_row_comes_first_and_enter_copies() -> None:
    rows = build_suggestions("12*7")
    assert rows[0].category == CALCULATOR
    assert rows[0].title == "= 84"
    assert rows[0].action == {"type": "copy", "text": "84"}
    assert default_index(rows) == 0
    assert describe(rows[0]) == "Copy 84"
    # Plain words don't get a calculator row
    assert all(r.category != CALCULATOR for r in build_suggestions("firefox"))
