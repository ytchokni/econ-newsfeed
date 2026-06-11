"""Tests for researcher search SQL guard conditions — catches bugs like PR #152, #147.

PR #152: zero-publication coauthors (imported via OpenAlex enrichment) appeared in directory.
PR #147: initial-only names ("A." "B.") appeared as valid researchers.

These tests verify the SQL WHERE clause structure, not the data — no DB needed.
"""
import pytest
from unittest.mock import patch


def _get_sql(**kwargs):
    """Call search_researchers with mocked fetch_all and return the SQL string + params."""
    with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
        from database.researchers import search_researchers
        search_researchers(**kwargs)
    sql, params = mock_fetch.call_args[0]
    return sql, params


class TestCoauthorGuard:
    """Coauthor-only researchers must be hidden from directory (PR #152)."""

    def test_authorship_exists_clause_present(self):
        sql, _ = _get_sql()
        assert "EXISTS (SELECT 1 FROM authorship" in sql

    def test_authorship_references_researcher_id(self):
        sql, _ = _get_sql()
        assert "a.researcher_id = r.id" in sql

    def test_authorship_guard_not_removed_by_filters(self):
        """Guard must persist even when other filters are active."""
        for kwargs in [
            {"institution": "MIT"},
            {"search": "Smith"},
            {"field_slug": "labor"},
            {"preset": "top20"},
            {"position": "Professor"},
        ]:
            sql, _ = _get_sql(**kwargs)
            assert "EXISTS (SELECT 1 FROM authorship" in sql, (
                f"authorship guard missing when filter={kwargs}"
            )


class TestInitialOnlyNameGuard:
    """Initial-only names (e.g., "A.", "B. C.") must be hidden."""

    def test_first_name_char_length_guard(self):
        sql, _ = _get_sql()
        assert "CHAR_LENGTH(r.first_name) > 2" in sql

    def test_last_name_char_length_guard(self):
        sql, _ = _get_sql()
        assert "CHAR_LENGTH(r.last_name) > 2" in sql

    def test_first_name_regexp_guard(self):
        sql, _ = _get_sql()
        assert "r.first_name NOT REGEXP" in sql

    def test_last_name_regexp_guard(self):
        sql, _ = _get_sql()
        assert "r.last_name NOT REGEXP" in sql

    def test_null_names_guarded(self):
        sql, _ = _get_sql()
        assert "r.first_name IS NOT NULL" in sql
        assert "r.last_name IS NOT NULL" in sql


class TestValidationGuard:
    """Only researchers with openalex_author_id OR researcher_urls are shown."""

    def test_openalex_or_urls_guard_present(self):
        sql, _ = _get_sql()
        assert "openalex_author_id IS NOT NULL" in sql
        assert "researcher_urls" in sql

    def test_guard_is_or_condition(self):
        """Must be OR (either openalex_author_id OR urls), not AND."""
        sql, _ = _get_sql()
        openalex_pos = sql.find("openalex_author_id IS NOT NULL")
        urls_pos = sql.find("SELECT 1 FROM researcher_urls")
        between = sql[openalex_pos:urls_pos]
        assert "OR" in between


class TestGuardsNotRemovableByPagination:
    """Guards must be present regardless of pagination params."""

    def test_guards_with_offset_and_limit(self):
        sql, _ = _get_sql(offset=100, limit=50)
        assert "EXISTS (SELECT 1 FROM authorship" in sql
        assert "CHAR_LENGTH(r.first_name) > 2" in sql
        assert "openalex_author_id IS NOT NULL" in sql


class TestGuardsCombinedWithUserFilters:
    """All three guards must coexist with user-supplied filters."""

    def test_all_guards_present_with_all_filters(self):
        sql, _ = _get_sql(
            institution="MIT",
            search="Smith",
            field_slug="labor",
        )
        assert "EXISTS (SELECT 1 FROM authorship" in sql
        assert "CHAR_LENGTH(r.first_name) > 2" in sql
        assert "openalex_author_id IS NOT NULL" in sql
        assert "r.affiliation LIKE %s" in sql
