"""Regression tests for the helpers in ``tinycalc.filters``.

These guard the behaviour that predates SPEC-001. They must pass both before
and after the change; a failure here means the change broke something that
was already shipped.
"""

from tinycalc import filters


class TestIsNumber:
    def test_integers_are_numbers(self):
        assert filters.is_number(3) is True

    def test_floats_are_numbers(self):
        assert filters.is_number(3.5) is True

    def test_booleans_are_not_numbers(self):
        assert filters.is_number(True) is False

    def test_strings_are_not_numbers(self):
        assert filters.is_number("3") is False

    def test_none_is_not_a_number(self):
        assert filters.is_number(None) is False


class TestIsInteger:
    def test_integers_pass(self):
        assert filters.is_integer(3) is True

    def test_floats_do_not_pass(self):
        assert filters.is_integer(3.5) is False

    def test_booleans_do_not_pass(self):
        assert filters.is_integer(True) is False

    def test_strings_do_not_pass(self):
        assert filters.is_integer("3") is False
