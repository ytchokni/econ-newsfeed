"""Tests for researcher deduplication: initial matching and merge logic."""
import pytest
from database.researchers import first_name_is_initial_match


class TestFirstNameIsInitialMatch:
    """first_name_is_initial_match returns True when one name is a single-char
    initial (with or without period) matching the other's first character."""

    def test_initial_with_period_matches_full_name(self):
        assert first_name_is_initial_match("L.", "Liam") is True

    def test_initial_without_period_matches_full_name(self):
        assert first_name_is_initial_match("L", "Liam") is True

    def test_case_insensitive(self):
        assert first_name_is_initial_match("l.", "Liam") is True

    def test_reversed_order(self):
        assert first_name_is_initial_match("Liam", "L.") is True

    def test_exact_match_returns_false(self):
        assert first_name_is_initial_match("Liam", "Liam") is False

    def test_two_char_prefix_returns_false(self):
        assert first_name_is_initial_match("Li", "Liam") is False

    def test_different_initial_returns_false(self):
        assert first_name_is_initial_match("J.", "Liam") is False

    def test_both_initials_same_letter(self):
        assert first_name_is_initial_match("L.", "L") is True

    def test_both_initials_different_letter(self):
        assert first_name_is_initial_match("L.", "J.") is False

    def test_empty_string_returns_false(self):
        assert first_name_is_initial_match("", "Liam") is False

    def test_both_empty_returns_false(self):
        assert first_name_is_initial_match("", "") is False
