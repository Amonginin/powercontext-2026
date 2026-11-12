"""TinyCalc — a deliberately small calculator used as the SDD sample project.

The point of this package is not the arithmetic. It is that it has a stable
public surface, a real test suite, and one clearly specified behaviour that is
missing on purpose: ``apply(min_value=...)``.

SPEC-001 section 2.1 asks for that filter. The implementation does not have
it yet, so the target test fails with a plain assertion error while everything
else keeps working. That is the difference between a missing feature and a
broken environment, and it is what makes the before/after comparison in the
MiniMax topic meaningful.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = ["Calculator"]


class Calculator:
    """Filters a list of numbers.

    ``max_value`` has been supported since the first release and its behaviour
    is fixed by SPEC-001 section 2.2: it keeps the elements that are less than
    or equal to the bound.
    """

    def __init__(self, values: Iterable[float]) -> None:
        self.values = list(values)

    def apply(self, **filters: Any) -> list[float]:
        """Return the values that survive the given filters.

        The order of the input is preserved and the result is a fresh list, so
        the caller can mutate it without touching ``self.values``.
        """
        result = list(self.values)
        if "max_value" in filters:
            result = [value for value in result if value <= filters["max_value"]]
        return result

    def total(self, **filters: Any) -> float:
        """Return the sum of the values that survive the given filters."""
        return sum(self.apply(**filters))
