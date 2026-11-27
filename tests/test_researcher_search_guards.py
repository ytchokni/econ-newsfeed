"""Tests for researcher search SQL guard conditions — catches bugs like PR #152, #147.

PR #152: zero-publication coauthors (imported via OpenAlex enrichment) appeared in directory.
PR #147: initial-only names ("A." "B.") appeared as valid researchers.

These tests verify the SQL WHERE clause structure, not the data — no DB needed.
"""
import pytest
from unittest.mock import MagicMock, patch


def _get_sql(**kwargs):
    """Call search_researchers with mocked fetch_one + fetch_all and return the SQL string + params.

    A name search is ranked by the in-memory index (stubbed to one match); the
    page query that loads its rows must still carry every guard.
    """
    from backend.database.researcher_index import Match, SearchResult
    index = MagicMock()
    index.search.return_value = SearchResult((Match(researcher_id=7, tier=95),))
    with patch("backend.database.researchers.fetch_one", return_value={'cnt': 0}), \
         patch("backend.database.researchers._name_index", return_value=index), \
         patch("backend.database.researchers.fetch_all", return_value=[]) as mock_fetch:
        from backend.database.researchers import search_researchers
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

    def test_initials_patterns_are_case_sensitive_regexps(self):
        """Pattern, not length: "A. Y." is hidden, "Li"/"Wu" are not (#263)."""
        from backend.database.researcher_index import CAPITAL_INITIALS, INITIALS_ONLY
        sql, params = _get_sql()
        assert "CHAR_LENGTH" not in sql
        assert "NOT REGEXP_LIKE(TRIM(r.first_name), %s, 'c')" in sql
        assert "NOT REGEXP_LIKE(TRIM(r.last_name), %s, 'c')" in sql
        assert list(params[:4]) == [INITIALS_ONLY, INITIALS_ONLY, CAPITAL_INITIALS, "[a-z]"]

    def test_empty_names_guarded(self):
        sql, _ = _get_sql()
        assert "TRIM(r.first_name) <> ''" in sql
        assert "TRIM(r.last_name) <> ''" in sql

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
        assert "REGEXP_LIKE(TRIM(r.first_name)" in sql
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
        assert "REGEXP_LIKE(TRIM(r.first_name)" in sql
        assert "openalex_author_id IS NOT NULL" in sql
        assert "r.affiliation LIKE %s" in sql
