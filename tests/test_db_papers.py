"""Unit tests for new query functions in database/papers.py.

Mocks database.papers.fetch_all and database.papers.fetch_one so no real DB
is required.
"""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import patch, call
import pytest


# ---------------------------------------------------------------------------
# get_authors_for_papers
# ---------------------------------------------------------------------------

class TestGetAuthorsForPapers:
    def test_empty_input_returns_empty_dict(self):
        from database.papers import get_authors_for_papers
        result = get_authors_for_papers([])
        assert result == {}

    def test_groups_by_paper_id(self):
        rows = [
            {"publication_id": 1, "researcher_id": 10, "first_name": "Alice", "last_name": "Smith"},
            {"publication_id": 1, "researcher_id": 11, "first_name": "Bob", "last_name": "Jones"},
            {"publication_id": 2, "researcher_id": 12, "first_name": "Carol", "last_name": "Lee"},
        ]
        with patch("database.papers.fetch_all", return_value=rows) as mock_fetch:
            from database.papers import get_authors_for_papers
            result = get_authors_for_papers([1, 2])

        assert len(result[1]) == 2
        assert result[1][0] == {"id": 10, "first_name": "Alice", "last_name": "Smith"}
        assert result[1][1] == {"id": 11, "first_name": "Bob", "last_name": "Jones"}
        assert len(result[2]) == 1
        assert result[2][0] == {"id": 12, "first_name": "Carol", "last_name": "Lee"}

    def test_sql_uses_parameterized_in_clause(self):
        with patch("database.papers.fetch_all", return_value=[]) as mock_fetch:
            from database.papers import get_authors_for_papers
            get_authors_for_papers([3, 7, 9])

        sql, params = mock_fetch.call_args[0]
        assert "IN (%s,%s,%s)" in sql
        assert params == (3, 7, 9)

    def test_paper_with_no_authors_returns_empty_list(self):
        with patch("database.papers.fetch_all", return_value=[]):
            from database.papers import get_authors_for_papers
            result = get_authors_for_papers([42])
        assert result == {42: []}

    def test_returns_id_field_from_researcher_id(self):
        rows = [
            {"publication_id": 5, "researcher_id": 99, "first_name": "Dan", "last_name": "Brown"},
        ]
        with patch("database.papers.fetch_all", return_value=rows):
            from database.papers import get_authors_for_papers
            result = get_authors_for_papers([5])
        assert result[5][0]["id"] == 99


# ---------------------------------------------------------------------------
# get_coauthors_for_papers
# ---------------------------------------------------------------------------

class TestGetCoauthorsForPapers:
    def test_empty_input_returns_empty_dict(self):
        from database.papers import get_coauthors_for_papers
        result = get_coauthors_for_papers([])
        assert result == {}

    def test_groups_by_paper_id(self):
        rows = [
            {"paper_id": 1, "display_name": "Eve White", "openalex_author_id": "A123"},
            {"paper_id": 2, "display_name": "Frank Black", "openalex_author_id": None},
        ]
        with patch("database.papers.fetch_all", return_value=rows):
            from database.papers import get_coauthors_for_papers
            result = get_coauthors_for_papers([1, 2])

        assert result[1] == [{"display_name": "Eve White", "openalex_author_id": "A123"}]
        assert result[2] == [{"display_name": "Frank Black", "openalex_author_id": None}]

    def test_sql_uses_parameterized_in_clause(self):
        with patch("database.papers.fetch_all", return_value=[]) as mock_fetch:
            from database.papers import get_coauthors_for_papers
            get_coauthors_for_papers([1, 2])

        sql, params = mock_fetch.call_args[0]
        assert "IN (%s,%s)" in sql
        assert params == (1, 2)

    def test_paper_with_no_coauthors_returns_empty_list(self):
        with patch("database.papers.fetch_all", return_value=[]):
            from database.papers import get_coauthors_for_papers
            result = get_coauthors_for_papers([99])
        assert result == {99: []}


# ---------------------------------------------------------------------------
# get_links_for_papers
# ---------------------------------------------------------------------------

class TestGetLinksForPapers:
    def test_empty_input_returns_empty_dict(self):
        from database.papers import get_links_for_papers
        result = get_links_for_papers([])
        assert result == {}

    def test_groups_by_paper_id(self):
        rows = [
            {"paper_id": 1, "url": "https://ssrn.com/1", "link_type": "ssrn"},
            {"paper_id": 1, "url": "https://doi.org/10.1/2", "link_type": "doi"},
            {"paper_id": 3, "url": "https://nber.org/3", "link_type": "nber"},
        ]
        with patch("database.papers.fetch_all", return_value=rows):
            from database.papers import get_links_for_papers
            result = get_links_for_papers([1, 3])

        assert len(result[1]) == 2
        assert result[1][0] == {"url": "https://ssrn.com/1", "link_type": "ssrn"}
        assert result[3][0] == {"url": "https://nber.org/3", "link_type": "nber"}

    def test_sql_uses_parameterized_in_clause(self):
        with patch("database.papers.fetch_all", return_value=[]) as mock_fetch:
            from database.papers import get_links_for_papers
            get_links_for_papers([5, 6, 7])

        sql, params = mock_fetch.call_args[0]
        assert "IN (%s,%s,%s)" in sql
        assert params == (5, 6, 7)

    def test_paper_with_no_links_returns_empty_list(self):
        with patch("database.papers.fetch_all", return_value=[]):
            from database.papers import get_links_for_papers
            result = get_links_for_papers([11])
        assert result == {11: []}


# ---------------------------------------------------------------------------
# get_paper_detail
# ---------------------------------------------------------------------------

class TestGetPaperDetail:
    def test_returns_row_when_found(self):
        expected = {
            "id": 1, "title": "Test Paper", "year": "2024", "venue": "AER",
            "source_url": "http://example.com", "discovered_at": None,
            "status": "published", "draft_url": None, "abstract": None,
            "draft_url_status": None, "doi": None, "is_seed": 0,
            "title_hash": "abc123", "openalex_id": None,
        }
        with patch("database.papers.fetch_one", return_value=expected):
            from database.papers import get_paper_detail
            result = get_paper_detail(1)
        assert result == expected

    def test_returns_none_when_not_found(self):
        with patch("database.papers.fetch_one", return_value=None):
            from database.papers import get_paper_detail
            result = get_paper_detail(999)
        assert result is None

    def test_sql_selects_all_expected_columns(self):
        with patch("database.papers.fetch_one", return_value=None) as mock_fetch:
            from database.papers import get_paper_detail
            get_paper_detail(42)

        sql, params = mock_fetch.call_args[0]
        for col in ("id", "title", "year", "venue", "source_url", "discovered_at",
                    "status", "draft_url", "abstract", "draft_url_status", "doi",
                    "is_seed", "title_hash", "openalex_id"):
            assert col in sql, f"Column {col!r} missing from SQL"
        assert params == (42,)


# ---------------------------------------------------------------------------
# get_paper_history
# ---------------------------------------------------------------------------

class TestGetPaperHistory:
    def test_returns_feed_events_ordered_desc(self):
        rows = [
            {"id": 5, "event_type": "status_change", "old_status": "working_paper",
             "new_status": "published", "created_at": "2026-01-02"},
            {"id": 3, "event_type": "new_paper", "old_status": None,
             "new_status": "working_paper", "created_at": "2026-01-01"},
        ]
        with patch("database.papers.fetch_all", return_value=rows):
            from database.papers import get_paper_history
            result = get_paper_history(1)
        assert result == rows

    def test_returns_empty_list_when_no_events(self):
        with patch("database.papers.fetch_all", return_value=[]):
            from database.papers import get_paper_history
            result = get_paper_history(1)
        assert result == []

    def test_sql_filters_by_paper_id_and_orders_desc(self):
        with patch("database.papers.fetch_all", return_value=[]) as mock_fetch:
            from database.papers import get_paper_history
            get_paper_history(7)

        sql, params = mock_fetch.call_args[0]
        assert "paper_id = %s" in sql
        assert "ORDER BY created_at DESC" in sql
        assert params == (7,)

    def test_sql_selects_expected_columns(self):
        with patch("database.papers.fetch_all", return_value=[]) as mock_fetch:
            from database.papers import get_paper_history
            get_paper_history(1)

        sql, _ = mock_fetch.call_args[0]
        for col in ("id", "event_type", "old_status", "new_status", "created_at"):
            assert col in sql, f"Column {col!r} missing from SQL"


# ---------------------------------------------------------------------------
# search_feed_events
# ---------------------------------------------------------------------------

class TestSearchFeedEvents:
    """Tests for search_feed_events dynamic SQL builder."""

    def _call(self, mock_rows=None, mock_count=None, **kwargs):
        """Helper: call search_feed_events with mocked fetch_one and fetch_all."""
        if mock_rows is None:
            mock_rows = []
        if mock_count is None:
            mock_count = {"cnt": len(mock_rows)}
        with (
            patch("database.papers.fetch_one", return_value=mock_count) as mock_count_fetch,
            patch("database.papers.fetch_all", return_value=mock_rows) as mock_fetch,
        ):
            from database.papers import search_feed_events
            result = search_feed_events(**kwargs)
        return result, mock_fetch, mock_count_fetch

    def test_no_filters_returns_all(self):
        rows = [{"paper_id": 1, "event_id": 1, "event_type": "new_paper",
                 "old_status": None, "new_status": "working_paper", "created_at": None,
                 "title": "T", "year": "2024", "venue": None, "source_url": None,
                 "discovered_at": None, "status": "working_paper", "draft_url": None,
                 "abstract": None, "draft_url_status": None, "doi": None,
                 "old_title": None, "new_title": None}]
        (result_rows, total), mock_fetch, _ = self._call(mock_rows=rows)
        assert total == 1
        assert result_rows == rows
        sql, params = mock_fetch.call_args[0]
        assert "WHERE" not in sql

    def test_empty_results_returns_zero_total(self):
        (rows, total), _, _ = self._call()
        assert rows == []
        assert total == 0

    def test_year_filter_adds_condition(self):
        (_, _), mock_fetch, _ = self._call(year="2023")
        sql, params = mock_fetch.call_args[0]
        assert "p.year = %s" in sql
        assert "2023" in params

    def test_researcher_id_filter_uses_exists_subquery(self):
        (_, _), mock_fetch, _ = self._call(researcher_id=42)
        sql, params = mock_fetch.call_args[0]
        assert "EXISTS" in sql
        assert "authorship" in sql
        assert 42 in params

    def test_status_list_single_uses_equals(self):
        (_, _), mock_fetch, _ = self._call(status_list=["published"])
        sql, params = mock_fetch.call_args[0]
        assert "p.status = %s" in sql
        assert "p.status IN" not in sql
        assert "published" in params

    def test_status_list_multiple_uses_in_clause(self):
        (_, _), mock_fetch, _ = self._call(status_list=["published", "working_paper"])
        sql, params = mock_fetch.call_args[0]
        assert "p.status IN (%s,%s)" in sql
        assert "published" in params
        assert "working_paper" in params

    def test_since_filter_adds_condition(self):
        from datetime import datetime
        since_dt = datetime(2026, 1, 1)
        (_, _), mock_fetch, _ = self._call(since=since_dt)
        sql, params = mock_fetch.call_args[0]
        assert "fe.created_at >= %s" in sql
        assert since_dt in params

    def test_institution_single_uses_like(self):
        (_, _), mock_fetch, _ = self._call(institution_list=["MIT"])
        sql, params = mock_fetch.call_args[0]
        assert "r.affiliation LIKE %s" in sql
        assert any("MIT" in str(p) for p in params)

    def test_institution_multiple_uses_or_likes(self):
        (_, _), mock_fetch, _ = self._call(institution_list=["MIT", "Harvard"])
        sql, params = mock_fetch.call_args[0]
        assert sql.count("r.affiliation LIKE %s") == 2

    def test_institution_ignored_when_preset_set(self):
        (_, _), mock_fetch, _ = self._call(institution_list=["MIT"], preset="top20")
        sql, params = mock_fetch.call_args[0]
        # preset overrides institution_list — MIT keyword appears in _TOP20_DEPT_KEYWORDS
        # but the institution_list branch is skipped; only one EXISTS block for top20
        assert sql.count("EXISTS") == 1

    def test_preset_top20_adds_dept_keywords(self):
        (_, _), mock_fetch, _ = self._call(preset="top20")
        sql, params = mock_fetch.call_args[0]
        assert "EXISTS" in sql
        # The TOP20 list has 24 keywords; verify a known one appears in params
        assert any("MIT" in str(p) for p in params)

    def test_search_fulltext_for_long_terms(self):
        (_, _), mock_fetch, _ = self._call(search="inflation")
        sql, params = mock_fetch.call_args[0]
        assert "MATCH" in sql
        assert "BOOLEAN MODE" in sql

    def test_search_like_for_short_terms(self):
        (_, _), mock_fetch, _ = self._call(search="ab")  # 2 chars < default FT_MIN_TOKEN_SIZE=3
        sql, params = mock_fetch.call_args[0]
        assert "LIKE" in sql
        assert "MATCH" not in sql

    def test_event_type_filter_adds_condition(self):
        (_, _), mock_fetch, _ = self._call(event_type="new_paper")
        sql, params = mock_fetch.call_args[0]
        assert "fe.event_type = %s" in sql
        assert "new_paper" in params

    def test_jel_code_single_uppercased(self):
        (_, _), mock_fetch, _ = self._call(jel_code="d91")
        sql, params = mock_fetch.call_args[0]
        assert "researcher_jel_codes" in sql
        assert "D91" in params

    def test_jel_code_multiple_comma_split(self):
        (_, _), mock_fetch, _ = self._call(jel_code="E31, f41")
        sql, params = mock_fetch.call_args[0]
        assert "IN (%s,%s)" in sql
        assert "E31" in params
        assert "F41" in params

    def test_pagination_params_are_last_in_tuple(self):
        """LIMIT and OFFSET must be the final params."""
        (_, _), mock_fetch, _ = self._call(year="2024", limit=10, offset=20)
        _, params = mock_fetch.call_args[0]
        # params should end with (limit, offset)
        assert params[-2] == 10
        assert params[-1] == 20

    def test_separate_count_query_used(self):
        """Total comes from a separate COUNT(*) query, not a window function."""
        (_, _), mock_fetch, mock_count_fetch = self._call(mock_count={"cnt": 42})
        # count query is issued via fetch_one
        assert mock_count_fetch.called
        count_sql, _ = mock_count_fetch.call_args[0]
        assert "COUNT(*) AS cnt" in count_sql
        assert "COUNT(*) OVER()" not in count_sql
        # data query has no window function
        data_sql, _ = mock_fetch.call_args[0]
        assert "COUNT(*) OVER()" not in data_sql

    def test_order_by_created_at_desc(self):
        (_, _), mock_fetch, _ = self._call()
        sql, _ = mock_fetch.call_args[0]
        assert "ORDER BY fe.created_at DESC" in sql

    def test_escape_like_helper(self):
        from database.papers import _escape_like
        assert _escape_like("50% off") == "50\\% off"
        assert _escape_like("a_b") == "a\\_b"
        assert _escape_like("a\\b") == "a\\\\b"

    def test_escape_fulltext_helper(self):
        from database.papers import _escape_fulltext
        assert _escape_fulltext("+inflation -recession") == "inflation recession"
        assert _escape_fulltext('"monetary policy"') == "monetary policy"
