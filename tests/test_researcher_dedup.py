"""Tests for researcher deduplication: initial matching and merge logic."""
import pytest
from unittest.mock import patch, MagicMock
from database.researchers import first_name_is_initial_match, get_researcher_id, merge_researchers


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


class TestMergeResearchers:
    """merge_researchers transfers authorship, JEL codes, metadata, then deletes duplicate."""

    def _make_mock_conn(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_transfers_authorship_and_deletes_duplicate(self):
        conn, cursor = self._make_mock_conn()
        cursor.fetchone.side_effect = [
            {"first_name": "L.", "last_name": "Smith", "affiliation": None,
             "description": None, "position": None, "openalex_author_id": None},
            {"first_name": "Liam", "last_name": "Smith", "affiliation": "Oxford",
             "description": "Economist", "position": "Prof", "openalex_author_id": "A123"},
        ]
        merge_researchers(10, 20, conn)

        executed = [call[0][0] for call in cursor.execute.call_args_list]
        # Should delete overlapping authorship
        assert any("DELETE FROM authorship" in q for q in executed)
        # Should update remaining authorship
        assert any("UPDATE authorship SET researcher_id" in q for q in executed)
        # Should transfer JEL codes
        assert any("UPDATE IGNORE researcher_jel_codes" in q for q in executed)
        # Should update first_name to longer
        assert any("UPDATE researchers SET first_name" in q for q in executed)
        # Should backfill metadata
        assert any("affiliation" in q and "UPDATE researchers" in q for q in executed)
        # Should delete duplicate
        assert any("DELETE FROM researchers WHERE id" in q for q in executed)
        # Should commit
        conn.commit.assert_called_once()

    def test_keeps_longer_first_name(self):
        conn, cursor = self._make_mock_conn()
        cursor.fetchone.side_effect = [
            {"first_name": "L.", "last_name": "Smith", "affiliation": None,
             "description": None, "position": None, "openalex_author_id": None},
            {"first_name": "Liam", "last_name": "Smith", "affiliation": None,
             "description": None, "position": None, "openalex_author_id": None},
        ]
        merge_researchers(10, 20, conn)

        # Find the UPDATE first_name call
        for call in cursor.execute.call_args_list:
            query = call[0][0]
            if "UPDATE researchers SET first_name" in query:
                params = call[0][1]
                assert params[0] == "Liam"  # longer name
                break
        else:
            pytest.fail("UPDATE researchers SET first_name was never called")

    def test_raises_if_canonical_equals_duplicate(self):
        conn, _ = self._make_mock_conn()
        with pytest.raises(ValueError, match="same"):
            merge_researchers(10, 10, conn)


class TestIsBadResearcherName:
    """is_bad_researcher_name rejects empty first names and initial-only last names."""

    def test_empty_first_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("", "Smith") is True

    def test_whitespace_first_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("  ", "Smith") is True

    def test_initial_only_last_name_with_period(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("Eric", "A.") is True

    def test_initial_only_last_name_without_period(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("David", "K") is True

    def test_empty_last_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("John", "") is True

    def test_whitespace_last_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("John", "  ") is True

    def test_valid_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("John", "Smith") is False

    def test_short_but_valid_last_name(self):
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("Yi", "Li") is False

    def test_initial_first_name_is_ok(self):
        """Single-letter first names like 'J.' are fine — it's last names we reject."""
        from database.researchers import is_bad_researcher_name
        assert is_bad_researcher_name("J.", "Smith") is False
