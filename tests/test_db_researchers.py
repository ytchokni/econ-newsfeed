"""Unit tests for new query functions in database/researchers.py.

Mocks database.researchers.fetch_all and database.researchers.fetch_one so no
real DB is required.
"""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import patch
import pytest


# ---------------------------------------------------------------------------
# get_urls_for_researchers
# ---------------------------------------------------------------------------

class TestGetUrlsForResearchers:
    def test_empty_input_returns_empty_dict(self):
        from database.researchers import get_urls_for_researchers
        result = get_urls_for_researchers([])
        assert result == {}

    def test_groups_by_researcher_id(self):
        rows = [
            {"researcher_id": 1, "id": 10, "page_type": "home", "url": "https://alice.com"},
            {"researcher_id": 1, "id": 11, "page_type": "cv", "url": "https://alice.com/cv"},
            {"researcher_id": 2, "id": 20, "page_type": "home", "url": "https://bob.com"},
        ]
        with patch("database.researchers.fetch_all", return_value=rows):
            from database.researchers import get_urls_for_researchers
            result = get_urls_for_researchers([1, 2])

        assert len(result[1]) == 2
        assert result[1][0] == {"id": 10, "page_type": "home", "url": "https://alice.com"}
        assert result[1][1] == {"id": 11, "page_type": "cv", "url": "https://alice.com/cv"}
        assert len(result[2]) == 1
        assert result[2][0] == {"id": 20, "page_type": "home", "url": "https://bob.com"}

    def test_sql_uses_parameterized_in_clause(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_urls_for_researchers
            get_urls_for_researchers([3, 7, 9])

        sql, params = mock_fetch.call_args[0]
        assert "IN (%s,%s,%s)" in sql
        assert params == (3, 7, 9)

    def test_researcher_with_no_urls_returns_empty_list(self):
        with patch("database.researchers.fetch_all", return_value=[]):
            from database.researchers import get_urls_for_researchers
            result = get_urls_for_researchers([42])
        assert result == {42: []}

    def test_returns_id_page_type_url_fields(self):
        rows = [
            {"researcher_id": 5, "id": 99, "page_type": "home", "url": "https://example.com"},
        ]
        with patch("database.researchers.fetch_all", return_value=rows):
            from database.researchers import get_urls_for_researchers
            result = get_urls_for_researchers([5])
        assert result[5][0] == {"id": 99, "page_type": "home", "url": "https://example.com"}


# ---------------------------------------------------------------------------
# get_pub_counts_for_researchers
# ---------------------------------------------------------------------------

class TestGetPubCountsForResearchers:
    def test_empty_input_returns_empty_dict(self):
        from database.researchers import get_pub_counts_for_researchers
        result = get_pub_counts_for_researchers([])
        assert result == {}

    def test_groups_by_researcher_id(self):
        rows = [
            {"researcher_id": 1, "cnt": 5},
            {"researcher_id": 2, "cnt": 12},
        ]
        with patch("database.researchers.fetch_all", return_value=rows):
            from database.researchers import get_pub_counts_for_researchers
            result = get_pub_counts_for_researchers([1, 2, 3])

        assert result[1] == 5
        assert result[2] == 12
        # researcher 3 has no rows — defaults to 0
        assert result[3] == 0

    def test_missing_ids_default_to_zero(self):
        with patch("database.researchers.fetch_all", return_value=[]):
            from database.researchers import get_pub_counts_for_researchers
            result = get_pub_counts_for_researchers([10, 11])
        assert result == {10: 0, 11: 0}

    def test_sql_uses_group_by(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_pub_counts_for_researchers
            get_pub_counts_for_researchers([1, 2])

        sql, params = mock_fetch.call_args[0]
        assert "GROUP BY researcher_id" in sql
        assert "authorship" in sql
        assert params == (1, 2)

    def test_sql_uses_parameterized_in_clause(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_pub_counts_for_researchers
            get_pub_counts_for_researchers([4, 5])

        sql, params = mock_fetch.call_args[0]
        assert "IN (%s,%s)" in sql


# ---------------------------------------------------------------------------
# get_fields_for_researchers
# ---------------------------------------------------------------------------

class TestGetFieldsForResearchers:
    def test_empty_input_returns_empty_dict(self):
        from database.researchers import get_fields_for_researchers
        result = get_fields_for_researchers([])
        assert result == {}

    def test_groups_by_researcher_id(self):
        rows = [
            {"researcher_id": 1, "id": 100, "name": "Labor Economics", "slug": "labor"},
            {"researcher_id": 1, "id": 101, "name": "Macro", "slug": "macro"},
            {"researcher_id": 2, "id": 102, "name": "Finance", "slug": "finance"},
        ]
        with patch("database.researchers.fetch_all", return_value=rows):
            from database.researchers import get_fields_for_researchers
            result = get_fields_for_researchers([1, 2])

        assert len(result[1]) == 2
        assert result[1][0] == {"id": 100, "name": "Labor Economics", "slug": "labor"}
        assert result[2][0] == {"id": 102, "name": "Finance", "slug": "finance"}

    def test_researcher_with_no_fields_returns_empty_list(self):
        with patch("database.researchers.fetch_all", return_value=[]):
            from database.researchers import get_fields_for_researchers
            result = get_fields_for_researchers([7])
        assert result == {7: []}

    def test_sql_joins_research_fields_and_orders_by_name(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_fields_for_researchers
            get_fields_for_researchers([1, 2, 3])

        sql, params = mock_fetch.call_args[0]
        assert "research_fields" in sql
        assert "researcher_fields" in sql
        assert "ORDER BY rf.name" in sql
        assert params == (1, 2, 3)

    def test_sql_uses_parameterized_in_clause(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_fields_for_researchers
            get_fields_for_researchers([8, 9])

        sql, params = mock_fetch.call_args[0]
        assert "IN (%s,%s)" in sql


# ---------------------------------------------------------------------------
# get_researcher_detail
# ---------------------------------------------------------------------------

class TestGetResearcherDetail:
    def test_returns_row_when_found(self):
        expected = {
            "id": 1, "first_name": "Alice", "last_name": "Smith",
            "position": "Professor", "affiliation": "MIT", "description": "Economist.",
        }
        with patch("database.researchers.fetch_one", return_value=expected):
            from database.researchers import get_researcher_detail
            result = get_researcher_detail(1)
        assert result == expected

    def test_returns_none_when_not_found(self):
        with patch("database.researchers.fetch_one", return_value=None):
            from database.researchers import get_researcher_detail
            result = get_researcher_detail(999)
        assert result is None

    def test_sql_selects_all_expected_columns(self):
        with patch("database.researchers.fetch_one", return_value=None) as mock_fetch:
            from database.researchers import get_researcher_detail
            get_researcher_detail(42)

        sql, params = mock_fetch.call_args[0]
        for col in ("id", "first_name", "last_name", "position", "affiliation", "description"):
            assert col in sql, f"Column {col!r} missing from SQL"
        assert params == (42,)

    def test_sql_filters_by_researcher_id(self):
        with patch("database.researchers.fetch_one", return_value=None) as mock_fetch:
            from database.researchers import get_researcher_detail
            get_researcher_detail(7)

        sql, params = mock_fetch.call_args[0]
        assert "WHERE id = %s" in sql
        assert params == (7,)


# ---------------------------------------------------------------------------
# get_researcher_papers
# ---------------------------------------------------------------------------

class TestGetResearcherPapers:
    def test_returns_list_of_papers(self):
        rows = [
            {"id": 1, "title": "Paper A", "year": "2024", "venue": "AER",
             "source_url": "http://x.com", "discovered_at": None, "status": "published",
             "draft_url": None, "abstract": None, "draft_url_status": None, "doi": None},
        ]
        with patch("database.researchers.fetch_all", return_value=rows):
            from database.researchers import get_researcher_papers
            result = get_researcher_papers(1)
        assert result == rows

    def test_returns_empty_list_when_no_papers(self):
        with patch("database.researchers.fetch_all", return_value=[]):
            from database.researchers import get_researcher_papers
            result = get_researcher_papers(42)
        assert result == []

    def test_sql_joins_authorship_and_filters_by_researcher(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_researcher_papers
            get_researcher_papers(5)

        sql, params = mock_fetch.call_args[0]
        assert "authorship" in sql
        assert "a.researcher_id = %s" in sql
        assert params == (5,)

    def test_sql_orders_by_discovered_at_desc(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_researcher_papers
            get_researcher_papers(5)

        sql, _ = mock_fetch.call_args[0]
        assert "ORDER BY p.discovered_at DESC" in sql

    def test_sql_selects_expected_columns(self):
        with patch("database.researchers.fetch_all", return_value=[]) as mock_fetch:
            from database.researchers import get_researcher_papers
            get_researcher_papers(1)

        sql, _ = mock_fetch.call_args[0]
        for col in ("id", "title", "year", "venue", "source_url", "discovered_at",
                    "status", "draft_url", "abstract", "draft_url_status", "doi"):
            assert col in sql, f"Column {col!r} missing from SQL"


# ---------------------------------------------------------------------------
# search_researchers
# ---------------------------------------------------------------------------

class TestSearchResearchers:
    """Tests for search_researchers dynamic SQL builder."""

    def _call(self, mock_rows=None, **kwargs):
        """Helper: call search_researchers with mocked fetch_all."""
        if mock_rows is None:
            mock_rows = []
        with patch("database.researchers.fetch_all", return_value=mock_rows) as mock_fetch:
            from database.researchers import search_researchers
            result = search_researchers(**kwargs)
        return result, mock_fetch

    def test_no_filters_returns_all(self):
        rows = [{"id": 1, "first_name": "Alice", "last_name": "Smith",
                 "position": "Prof", "affiliation": "MIT", "description": None,
                 "total_count": 1}]
        (result_rows, total), mock_fetch = self._call(mock_rows=rows)
        assert total == 1
        assert result_rows == rows

    def test_empty_results_returns_zero_total(self):
        (rows, total), _ = self._call()
        assert rows == []
        assert total == 0

    def test_base_condition_openalex_or_urls_always_present(self):
        (_, _), mock_fetch = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "openalex_author_id IS NOT NULL" in sql
        assert "researcher_urls" in sql

    def test_base_condition_requires_at_least_one_publication(self):
        (_, _), mock_fetch = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "authorship" in sql

    def test_base_condition_char_length_always_present(self):
        (_, _), mock_fetch = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "CHAR_LENGTH" in sql
        assert "first_name" in sql
        assert "last_name" in sql

    def test_base_condition_regexp_filter_always_present(self):
        (_, _), mock_fetch = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "REGEXP" in sql

    def test_institution_filter_adds_like_condition(self):
        (_, _), mock_fetch = self._call(institution="MIT")
        sql, params = mock_fetch.call_args[0]
        assert "r.affiliation LIKE %s" in sql
        assert any("MIT" in str(p) for p in params)

    def test_position_filter_adds_like_condition(self):
        (_, _), mock_fetch = self._call(position="Professor")
        sql, params = mock_fetch.call_args[0]
        assert "r.position LIKE %s" in sql
        assert any("Professor" in str(p) for p in params)

    def test_preset_top20_adds_dept_keywords(self):
        (_, _), mock_fetch = self._call(preset="top20")
        sql, params = mock_fetch.call_args[0]
        # All keywords are in affiliation LIKE conditions
        assert "r.affiliation LIKE %s" in sql
        assert any("MIT" in str(p) for p in params)

    def test_field_slug_single_uses_equals(self):
        (_, _), mock_fetch = self._call(field_slug="labor")
        sql, params = mock_fetch.call_args[0]
        assert "f.slug = %s" in sql
        assert "f.slug IN" not in sql
        assert "labor" in params

    def test_field_slug_multiple_uses_in_clause(self):
        (_, _), mock_fetch = self._call(field_slug="labor, macro")
        sql, params = mock_fetch.call_args[0]
        assert "f.slug IN (%s,%s)" in sql
        assert "labor" in params
        assert "macro" in params

    def test_field_slug_joins_research_fields(self):
        (_, _), mock_fetch = self._call(field_slug="finance")
        sql, _ = mock_fetch.call_args[0]
        assert "researcher_fields" in sql
        assert "research_fields" in sql

    def test_search_fulltext_for_long_terms(self):
        (_, _), mock_fetch = self._call(search="inflation")
        sql, params = mock_fetch.call_args[0]
        assert "MATCH" in sql
        assert "BOOLEAN MODE" in sql

    def test_search_like_for_short_terms(self):
        (_, _), mock_fetch = self._call(search="ab")  # 2 chars < default FT_MIN_TOKEN_SIZE=3
        sql, params = mock_fetch.call_args[0]
        assert "LIKE" in sql
        assert "MATCH" not in sql

    def test_search_fulltext_also_adds_concat_like(self):
        """FULLTEXT search should also add a CONCAT LIKE for full-name matching."""
        (_, _), mock_fetch = self._call(search="John Smith")
        sql, params = mock_fetch.call_args[0]
        assert "CONCAT" in sql
        assert "LIKE" in sql

    def test_pagination_params_are_last_in_tuple(self):
        """LIMIT and OFFSET must be the final params."""
        (_, _), mock_fetch = self._call(institution="Harvard", limit=10, offset=20)
        _, params = mock_fetch.call_args[0]
        assert params[-2] == 10
        assert params[-1] == 20

    def test_window_function_in_sql(self):
        (_, _), mock_fetch = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "COUNT(*) OVER()" in sql

    def test_order_by_last_name_first_name(self):
        (_, _), mock_fetch = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "ORDER BY r.last_name, r.first_name" in sql

    def test_escape_like_helper(self):
        from database.researchers import _escape_like
        assert _escape_like("50% off") == "50\\% off"
        assert _escape_like("a_b") == "a\\_b"
        assert _escape_like("a\\b") == "a\\\\b"

    def test_escape_fulltext_helper(self):
        from database.researchers import _escape_fulltext
        assert _escape_fulltext("+inflation -recession") == "inflation recession"
        assert _escape_fulltext('"monetary policy"') == "monetary policy"

    def test_where_clause_present_when_filters_active(self):
        (_, _), mock_fetch = self._call(institution="Princeton")
        sql, _ = mock_fetch.call_args[0]
        assert "WHERE" in sql

    def test_base_conditions_always_in_where(self):
        """Even with no user filters, WHERE is present for base conditions."""
        (_, _), mock_fetch = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "WHERE" in sql


# ---------------------------------------------------------------------------
# get_urls_needing_extraction
# ---------------------------------------------------------------------------

class TestGetUrlsNeedingExtraction:
    def test_queries_active_urls_with_hash_mismatch(self):
        from database.researchers import get_urls_needing_extraction
        rows = [{"id": 1, "researcher_id": 10, "url": "https://a.com", "page_type": "PUBLICATIONS"}]
        with patch("database.researchers.fetch_all", return_value=rows) as mock_fetch:
            result = get_urls_needing_extraction()
        assert result == rows
        query = mock_fetch.call_args[0][0]
        assert "is_active = TRUE" in query
        assert "content_hash IS NOT NULL" in query
        assert "extracted_hash IS NULL" in query
        assert "extracted_hash != hc.content_hash" in query

    def test_facade_exposes_it(self):
        from database import Database
        assert hasattr(Database, "get_urls_needing_extraction")
