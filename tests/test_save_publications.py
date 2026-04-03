# tests/test_save_publications.py
"""Tests for Publication.save_publications edge cases."""
import pytest
from unittest.mock import patch, MagicMock, call
from publication import Publication, _author_id_cache


def _mock_conn():
    """Create a mock DB connection that simulates INSERT IGNORE with new row."""
    mock_cursor = MagicMock()
    mock_cursor.lastrowid = 1  # Simulate new paper inserted
    mock_cursor.fetchone.return_value = (0,)  # Default: no snapshot baseline
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


@pytest.fixture(autouse=True)
def clear_author_cache():
    """Ensure each test starts with a clean author cache."""
    _author_id_cache.clear()
    yield
    _author_id_cache.clear()


class TestAuthorNormalization:
    """Author lists with != 2 elements should not crash save_publications."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_three_element_author_joins_first_names(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['Jose', 'Luis', 'Garcia'] -> first_name='Jose Luis', last_name='Garcia'."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["Jose", "Luis", "Garcia"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("Jose Luis", "Garcia", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_single_element_author_uses_empty_first_name(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['Garcia'] -> first_name='', last_name='Garcia'."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["Garcia"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("", "Garcia", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_empty_author_list_falls_back_to_page_owner(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """[[]] (empty inner list) -> fall back to page owner, don't crash."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        # (0,) for baseline snapshot count (hoisted), then page owner lookup
        cursor.fetchone.side_effect = [(0,), ("Jane", "Doe")]

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [[]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("Jane", "Doe", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_normal_two_element_author_unchanged(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """['John', 'Doe'] -> first_name='John', last_name='Doe' (normal case)."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("http://example.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("John", "Doe", conn=conn)


class TestCursorCleanup:
    """Cursor must be closed even when an exception occurs mid-save."""

    @patch("publication.Database.get_researcher_id", side_effect=RuntimeError("db error"))
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_cursor_closed_on_author_error(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Cursor.close() called even when author processing raises."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        # Should not raise — error is caught and logged
        Publication.save_publications("http://example.com", [{
            "title": "Paper A",
            "authors": [["John", "Doe"]],
            "year": "2024",
        }])

        cursor.close.assert_called()

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_second_pub_succeeds_after_first_pub_fails(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """A failed publication must not prevent subsequent publications from saving."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        # First call raises, second succeeds
        mock_get_researcher.side_effect = [RuntimeError("fail"), 42]

        Publication.save_publications("http://example.com", [
            {"title": "Paper A", "authors": [["Bad", "Author"]], "year": "2024"},
            {"title": "Paper B", "authors": [["Good", "Author"]], "year": "2024"},
        ])

        # Paper B should still be committed
        assert conn.commit.call_count >= 1


class TestAuthorLookupCache:
    """get_researcher_id should be called once per unique author, not per occurrence."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_same_author_across_pubs_looked_up_once(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """If 'John Doe' appears in 3 publications, get_researcher_id called once."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        pubs = [
            {"title": f"Paper {i}", "authors": [["John", "Doe"], ["Jane", "Smith"]], "year": "2024"}
            for i in range(3)
        ]

        Publication.save_publications("http://example.com", pubs)

        # 2 unique authors x 1 call each = 2 calls total (not 6)
        assert mock_get_researcher.call_count == 2

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_cache_persists_across_save_publications_calls(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Cache carries over between save_publications calls (same process, different URLs)."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        pub = [{"title": "Paper A", "authors": [["John", "Doe"]], "year": "2024"}]

        Publication.save_publications("http://url1.com", pub)
        Publication.save_publications("http://url2.com", pub)

        # John Doe looked up once across both calls
        assert mock_get_researcher.call_count == 1


class TestAbstractBackfill:
    """When a duplicate paper is found, backfill NULL abstract/year/venue from new extraction."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_backfills_abstract_when_existing_is_null(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Duplicate path should UPDATE abstract when existing paper has NULL abstract."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        # Simulate INSERT IGNORE → duplicate (lastrowid=0)
        cursor.lastrowid = 0
        # fetchone for title_hash lookup → returns paper id=10
        # fetchone for existing paper fields → abstract is NULL
        cursor.fetchone.side_effect = [
            (0,),                # baseline snapshot count (hoisted before loop)
            (10,),               # SELECT id FROM papers WHERE title_hash = ...
            (None, None, None),  # SELECT abstract, year, venue FROM papers WHERE id = ...
        ]
        cursor.rowcount = 1  # new_to_this_url = True for paper_urls INSERT

        Publication.save_publications("http://new-source.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
            "venue": "AER",
            "abstract": "This paper studies trade.",
        }])

        # Verify UPDATE was called to backfill
        update_calls = [
            call for call in cursor.execute.call_args_list
            if 'UPDATE papers SET' in str(call) and 'COALESCE' in str(call)
        ]
        assert len(update_calls) == 1, f"Expected 1 backfill UPDATE, got {len(update_calls)}"

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_skips_backfill_when_existing_has_all_fields(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """No UPDATE if existing paper already has abstract, year, venue."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.lastrowid = 0
        cursor.fetchone.side_effect = [
            (0,),                                  # baseline snapshot count (hoisted)
            (10,),                                 # title_hash lookup
            ("Existing abstract", "2023", "QJE"),  # all fields populated
        ]
        cursor.rowcount = 1

        Publication.save_publications("http://new-source.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "abstract": "New abstract",
            "year": "2024",
            "venue": "AER",
        }])

        update_calls = [
            call for call in cursor.execute.call_args_list
            if 'UPDATE papers SET' in str(call) and 'COALESCE' in str(call)
        ]
        assert len(update_calls) == 0, "Should not backfill when all fields exist"


class TestFallbackToPageOwner:
    """When LLM returns no authors, the page owner should be used as default author."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_uses_page_owner_when_no_authors(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Empty authors list should trigger lookup of the page owner."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        # (0,) for baseline snapshot count (hoisted), then page owner lookup
        cursor.fetchone.side_effect = [(0,), ("Stefanie", "Stantcheva")]

        Publication.save_publications("https://www.stantcheva.com/research/", [{
            "title": "Understanding of Trade",
            "authors": [],
            "year": "2022",
        }])

        mock_get_researcher.assert_called_once_with("Stefanie", "Stantcheva", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_skips_fallback_when_authors_present(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Non-empty authors list should NOT trigger page owner lookup."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn

        Publication.save_publications("https://example.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
        }])

        mock_get_researcher.assert_called_once_with("John", "Doe", conn=conn)

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_no_crash_when_page_owner_not_found(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """If page owner lookup returns None, no author is added (no crash)."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.fetchone.return_value = None  # no owner found

        Publication.save_publications("https://unknown.com", [{
            "title": "Orphan Paper",
            "authors": [],
            "year": "2024",
        }])

        mock_get_researcher.assert_not_called()

    @patch("publication.Database.get_researcher_id")
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_page_owner_added_alongside_coauthors(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Page owner should be added as author even when LLM extracts other authors."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        # Return different IDs for coauthor vs owner
        mock_get_researcher.side_effect = [99, 7]  # 99=coauthor, 7=owner
        # (0,) for baseline snapshot count, then page owner lookup
        cursor.fetchone.side_effect = [(0,), ("Magne", "Mogstad")]

        Publication.save_publications("http://mogstad.com/research", [{
            "title": "Inequality in Current and Lifetime Income",
            "authors": [["R.", "Aaberge"]],
            "year": "2023",
        }])

        # Both the coauthor AND the page owner should be looked up
        mock_get_researcher.assert_any_call("R.", "Aaberge", conn=conn)
        mock_get_researcher.assert_any_call("Magne", "Mogstad", conn=conn)
        assert mock_get_researcher.call_count == 2

        # Two INSERT IGNORE INTO authorship calls
        authorship_inserts = [
            c for c in cursor.execute.call_args_list
            if 'INSERT IGNORE INTO authorship' in str(c)
        ]
        assert len(authorship_inserts) == 2
