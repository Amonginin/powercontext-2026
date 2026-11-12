"""Helper predicates shared by the calculator.

This module is untouched by the SPEC-001 task. It exists so the regression
suite has something to guard: a change to ``Calculator.apply`` must not break
the filter semantics that are already in production.
"""

from __future__ import annotations

from typing import Any

__all__ = ["is_number", "is_integer"]


def is_number(value: Any) -> bool:
    """Return True when the value is a real number but not a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_integer(value: Any) -> bool:
    """Return True when the value is an integer but not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)
