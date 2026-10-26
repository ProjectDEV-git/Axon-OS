"""Inline calculator for the Intent Bar (type ``12*7``, see ``= 84``).

A small recursive-descent parser, never ``eval``: numbers, ``+ - * /``,
``^`` (or ``**``) for powers (right-associative, binding tighter than a sign,
so ``-2^2`` is -4), brackets and unary minus. ``×`` and ``÷`` are
accepted. Limits keep it instant: 80 characters, exponents up to 100 and
results under 10^15. The Shell keeps a JavaScript twin in
shell/axon-shell/ui-logic.js (``calculate``); a test checks they agree.
"""

from __future__ import annotations

import math
import re

_MAX_LENGTH = 80
_MAX_EXPONENT = 100
_MAX_RESULT = 1e15
_ALLOWED = re.compile(r"^[\d.\s+\-*/^()]+$")
_NUMBER = re.compile(r"\d+(?:\.\d*)?|\.\d+")


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens: list[str] = re.findall(r"\d+(?:\.\d*)?|\.\d+|[+\-*/^()]", text)
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expr(self) -> float:
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            right = self.term()
            value = value + right if op == "+" else value - right
        return value

    def term(self) -> float:
        value = self.unary()
        while self.peek() in ("*", "/"):
            op = self.take()
            right = self.unary()
            if op == "*":
                value *= right
            elif right == 0:
                raise ValueError("division by zero")
            else:
                value /= right
        return value

    def power(self) -> float:
        base = self.primary()
        if self.peek() == "^":
            self.take()
            # Right-associative (2^3^2 is 2^9), and the exponent may be signed
            exponent = self.unary()
            if abs(exponent) > _MAX_EXPONENT:
                raise ValueError("exponent too large")
            if base == 0 and exponent < 0:
                raise ValueError("division by zero")
            if base < 0 and not float(exponent).is_integer():
                raise ValueError("complex result")
            result: float = float(base) ** exponent
            return result
        return base

    def unary(self) -> float:
        # Powers bind tighter than a sign: -2^2 is -4
        if self.peek() in ("+", "-"):
            sign = -1.0 if self.take() == "-" else 1.0
            return sign * self.unary()
        return self.power()

    def primary(self) -> float:
        token = self.peek()
        if token == "(":
            self.take()
            value = self.expr()
            if self.peek() != ")":
                raise ValueError("unbalanced brackets")
            self.take()
            return value
        if token is not None and _NUMBER.fullmatch(token):
            return float(self.take())
        raise ValueError("expected a number")


def _normalise(query: str) -> str:
    return query.replace("×", "*").replace("÷", "/").replace("**", "^")


def looks_like_math(query: str) -> bool:
    """True for typed arithmetic: allowed characters and at least one operator."""
    text = _normalise(query.strip())
    if not text or len(text) > _MAX_LENGTH or not _ALLOWED.match(text):
        return False
    # Dates and phone-style numbers (2024-10-26, 555-12-34) are not sums
    if re.fullmatch(r"\d+(?:-\d+){2,}", text):
        return False
    # A plain number or a signed number is not a calculation
    return bool(re.search(r"\d\s*[+\-*/^]|\)|[*/^]", text)) and bool(re.search(r"\d", text))


def format_number(value: float) -> str | None:
    """Ten significant digits, no exponent notation; None when it can't be shown simply."""
    if not math.isfinite(value) or abs(value) >= _MAX_RESULT:
        return None
    rounded = float(f"{value:.10g}")
    if rounded == 0:
        return "0"
    if abs(rounded) < 1e-6:
        return None
    if rounded.is_integer():
        return str(int(rounded))
    return repr(rounded)


def calculate(query: str) -> str | None:
    """The result of *query* as text, or None when it isn't a calculation."""
    if not looks_like_math(query):
        return None
    parser = _Parser(_normalise(query.strip()))
    try:
        value = parser.expr()
    except (ValueError, IndexError, OverflowError):
        return None
    if parser.peek() is not None:
        return None
    return format_number(value)
