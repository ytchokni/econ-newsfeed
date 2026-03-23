"""Tests for researcher deduplication: initial matching and merge logic."""
import pytest
from unittest.mock import patch, MagicMock
from database.researchers import first_name_is_initial_match, get_researcher_id


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


class TestGetResearcherIdInitialTier:
    """Tier 1.5: initial matching in get_researcher_id()."""

    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    @patch("database.researchers.execute_query")
    def test_initial_matches_single_candidate(self, mock_exec, mock_one, mock_all):
        """'L.' + existing 'Liam' with same last name -> returns existing id."""
        # Tier 1 exact match fails
        mock_one.side_effect = [None]
        # Tier 1.5 same-last-name query returns one candidate
        mock_all.return_value = [{"id": 49, "first_name": "L.", "last_name": "Wren-Lewis"}]
        # UPDATE first_name returns None (no lastrowid needed)
        mock_exec.return_value = None

        result = get_researcher_id("Liam", "Wren-Lewis")
        assert result == 49
        # Should have updated first_name to the longer name
        update_call = mock_exec.call_args
        assert "UPDATE researchers SET first_name" in update_call[0][0]
        assert "Liam" in update_call[0][1]

    @patch("database.researchers._disambiguate_researcher")
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    def test_multiple_initial_matches_falls_through(self, mock_one, mock_all, mock_disamb):
        """Multiple initial matches -> skip Tier 1.5, fall through to LLM."""
        mock_one.side_effect = [None, None]  # Tier 1 + Tier 2 fail
        mock_all.return_value = [
            {"id": 10, "first_name": "L.", "last_name": "Smith"},
            {"id": 20, "first_name": "Liam", "last_name": "Smith"},
        ]
        mock_disamb.return_value = 10

        result = get_researcher_id("L", "Smith")
        assert result == 10
        mock_disamb.assert_called_once()

    @patch("database.researchers._disambiguate_researcher", return_value=None)
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    @patch("database.researchers.execute_query")
    def test_no_initial_match_falls_through_to_insert(self, mock_exec, mock_one, mock_all, mock_disamb):
        """No initial match and no LLM match -> inserts new researcher."""
        mock_one.side_effect = [None, None]  # Tier 1 + Tier 2 fail
        mock_all.return_value = [
            {"id": 10, "first_name": "John", "last_name": "Smith"},
        ]
        mock_exec.return_value = 99  # new id from INSERT

        result = get_researcher_id("Robert", "Smith")
        assert result == 99
