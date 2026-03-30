"""Tests for HTMLFetcher: robots.txt caching, fetch, change detection, thread safety."""
import hashlib
import threading
import zlib
import pytest
from unittest.mock import patch, MagicMock

from html_fetcher import HTMLFetcher


class TestRobotsTxtCaching:
    def test_robots_txt_cached_per_domain(self):
        """robots.txt should be fetched once per domain, not per URL."""
        with patch("html_fetcher.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "User-agent: *\nAllow: /"
            mock_get.return_value = mock_resp

            HTMLFetcher._robots_cache.clear()
            HTMLFetcher.is_allowed_by_robots("https://example.com/page1")
            HTMLFetcher.is_allowed_by_robots("https://example.com/page2")

        assert mock_get.call_count == 1

    def test_different_domains_fetched_separately(self):
        """Different domains should each get their own robots.txt fetch."""
        with patch("html_fetcher.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "User-agent: *\nAllow: /"
            mock_get.return_value = mock_resp

            HTMLFetcher._robots_cache.clear()
            HTMLFetcher.is_allowed_by_robots("https://example.com/page1")
            HTMLFetcher.is_allowed_by_robots("https://other.com/page2")

        assert mock_get.call_count == 2

    def test_robots_txt_404_allows_access(self):
        """If robots.txt returns 404, all URLs should be allowed."""
        with patch("html_fetcher.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp

            HTMLFetcher._robots_cache.clear()
            assert HTMLFetcher.is_allowed_by_robots("https://example.com/page1") is True


class TestFetchHtml:
    def test_successful_fetch_returns_content(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"<html>Hello</html>"
        mock_resp.text = "<html>Hello</html>"
        mock_resp.apparent_encoding = "utf-8"

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch.object(HTMLFetcher, '_get_session', return_value=mock_session):
            result = HTMLFetcher.fetch_html("https://example.com")

        assert result == "<html>Hello</html>"

    def test_retry_on_server_error(self):
        error_resp = MagicMock()
        error_resp.status_code = 500
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.content = b"ok"
        ok_resp.text = "ok"
        ok_resp.apparent_encoding = "utf-8"

        mock_session = MagicMock()
        mock_session.get.side_effect = [error_resp, ok_resp]
        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch('time.sleep'), \
             patch.object(HTMLFetcher, '_get_session', return_value=mock_session):
            result = HTMLFetcher.fetch_html("https://example.com")

        assert result == "ok"

    def test_returns_none_after_max_retries(self):
        error_resp = MagicMock()
        error_resp.status_code = 500

        mock_session = MagicMock()
        mock_session.get.return_value = error_resp
        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch('time.sleep'), \
             patch.object(HTMLFetcher, '_get_session', return_value=mock_session):
            result = HTMLFetcher.fetch_html("https://example.com", max_retries=2)

        assert result is None

    def test_rejects_oversized_response(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"x" * 1_000_001  # Just over CONTENT_MAX_BYTES (1MB)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch.object(HTMLFetcher, '_get_session', return_value=mock_session):
            result = HTMLFetcher.fetch_html("https://example.com")

        assert result is None


class TestChangeDetection:
    def test_has_text_changed_returns_true_for_new_content(self):
        with patch("html_fetcher.Database.fetch_one", return_value=None):
            assert HTMLFetcher.has_text_changed(1, "abc") is True

    def test_has_text_changed_returns_false_for_same_hash(self):
        with patch("html_fetcher.Database.fetch_one", return_value={"content_hash": "abc"}):
            assert HTMLFetcher.has_text_changed(1, "abc") is False

    def test_has_text_changed_returns_true_for_different_hash(self):
        with patch("html_fetcher.Database.fetch_one", return_value={"content_hash": "old"}):
            assert HTMLFetcher.has_text_changed(1, "new") is True


class TestThreadSafety:
    def test_sessions_are_thread_local(self):
        """Each thread should get its own Session instance."""
        sessions = {}

        def capture_session():
            sessions[threading.current_thread().name] = HTMLFetcher._get_session()

        t1 = threading.Thread(target=capture_session, name="t1")
        t2 = threading.Thread(target=capture_session, name="t2")
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert sessions["t1"] is not sessions["t2"]


class TestIsFirstExtraction:
    """Tests for HTMLFetcher.is_first_extraction()."""

    @patch("html_fetcher.Database.fetch_one")
    def test_returns_true_when_never_extracted(self, mock_fetch):
        """extracted_at IS NULL means first extraction."""
        mock_fetch.return_value = {"extracted_at": None}
        assert HTMLFetcher.is_first_extraction(1) is True

    @patch("html_fetcher.Database.fetch_one")
    def test_returns_false_when_previously_extracted(self, mock_fetch):
        """extracted_at is set means already extracted before."""
        mock_fetch.return_value = {"extracted_at": "2026-03-19 12:00:00"}
        assert HTMLFetcher.is_first_extraction(1) is False

    @patch("html_fetcher.Database.fetch_one")
    def test_returns_false_when_no_html_content(self, mock_fetch):
        """No html_content row at all — nothing to extract."""
        mock_fetch.return_value = None
        assert HTMLFetcher.is_first_extraction(1) is False


class TestArchiveSnapshot:
    """Tests for HTMLFetcher.archive_snapshot()."""

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_archives_when_prior_row_exists(self, mock_execute, mock_fetch):
        """Should compress and store old raw_html when a prior row exists."""
        old_html = "<html>old content</html>"
        mock_fetch.return_value = {
            "raw_html": old_html,
            "content_hash": "old_text_hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        HTMLFetcher.archive_snapshot(url_id=1)

        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "INSERT IGNORE INTO html_snapshots" in call_args[0]
        params = call_args[1]
        assert params[0] == 1  # url_id
        assert params[1] == "old_text_hash"  # text_content_hash
        expected_html_hash = hashlib.sha256(old_html.encode("utf-8")).hexdigest()
        assert params[2] == expected_html_hash  # raw_html_hash
        assert zlib.decompress(params[3]).decode("utf-8") == old_html  # compressed blob
        assert params[4] == "2026-03-01 12:00:00"  # snapshot_at

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_no_archive_on_first_fetch(self, mock_execute, mock_fetch):
        """No prior row means no snapshot to archive."""
        mock_fetch.return_value = None

        HTMLFetcher.archive_snapshot(url_id=1)

        mock_execute.assert_not_called()

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_no_archive_when_raw_html_null(self, mock_execute, mock_fetch):
        """Legacy rows with raw_html=NULL should be skipped with a warning."""
        mock_fetch.return_value = {
            "raw_html": None,
            "content_hash": "some_hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        with patch("html_fetcher.logging.warning") as mock_warn:
            HTMLFetcher.archive_snapshot(url_id=1)

        mock_execute.assert_not_called()
        mock_warn.assert_called_once()
        assert "NULL" in mock_warn.call_args[0][0] or "null" in str(mock_warn.call_args).lower()

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query", side_effect=Exception("DB error"))
    def test_archive_failure_doesnt_raise(self, mock_execute, mock_fetch):
        """Archive errors should be logged, not raised."""
        mock_fetch.return_value = {
            "raw_html": "<html>old</html>",
            "content_hash": "hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        # Should not raise
        HTMLFetcher.archive_snapshot(url_id=1)

    @patch("html_fetcher.Database.fetch_one")
    @patch("html_fetcher.Database.execute_query")
    def test_duplicate_archive_ignored(self, mock_execute, mock_fetch):
        """Calling archive twice with same content should not error (INSERT IGNORE)."""
        mock_fetch.return_value = {
            "raw_html": "<html>old</html>",
            "content_hash": "same_hash",
            "timestamp": "2026-03-01 12:00:00",
        }

        HTMLFetcher.archive_snapshot(url_id=1)
        HTMLFetcher.archive_snapshot(url_id=1)

        # Both calls execute INSERT IGNORE — no errors
        assert mock_execute.call_count == 2
        for call in mock_execute.call_args_list:
            assert "INSERT IGNORE" in call[0][0]

    @patch("html_fetcher.Database.execute_query")
    def test_save_text_calls_archive_before_upsert(self, mock_execute):
        """save_text() should call archive_snapshot() before the upsert."""
        call_order = []

        def track_archive(url_id):
            call_order.append("archive")

        def track_execute(query, params=None):
            if "html_content" in query:
                call_order.append("upsert")
            return 0

        mock_execute.side_effect = track_execute

        with patch.object(HTMLFetcher, "archive_snapshot", side_effect=track_archive) as mock_archive:
            HTMLFetcher.save_text(url_id=1, text_content="new text", text_hash="new_hash", researcher_id=10, raw_html="<html>new</html>")

        mock_archive.assert_called_once_with(1)
        assert call_order == ["archive", "upsert"]

    @patch("html_fetcher.Database.execute_query")
    def test_save_text_still_saves_when_archive_fails(self, mock_execute):
        """save_text() should still upsert html_content even if archive_snapshot() fails internally."""
        with patch.object(HTMLFetcher, "archive_snapshot", side_effect=Exception("archive boom")):
            HTMLFetcher.save_text(url_id=1, text_content="new text", text_hash="new_hash", researcher_id=10, raw_html="<html>new</html>")

        # The upsert into html_content should still have executed
        assert mock_execute.called
        assert "html_content" in mock_execute.call_args[0][0]


class TestSnapshotRetrieval:
    """Tests for HTMLFetcher.get_snapshot() and list_snapshots()."""

    @patch("html_fetcher.Database.fetch_one")
    def test_get_snapshot_decompresses_and_verifies(self, mock_fetch):
        """Should decompress and return raw HTML after integrity check."""
        original_html = "<html><body>Test page</body></html>"
        compressed = zlib.compress(original_html.encode("utf-8"))
        html_hash = hashlib.sha256(original_html.encode("utf-8")).hexdigest()

        mock_fetch.return_value = {
            "raw_html_compressed": compressed,
            "raw_html_hash": html_hash,
        }

        result = HTMLFetcher.get_snapshot(url_id=1, snapshot_id=42)
        assert result == original_html

    @patch("html_fetcher.Database.fetch_one")
    def test_get_snapshot_integrity_failure(self, mock_fetch):
        """Should raise ValueError when decompressed HTML doesn't match hash."""
        original_html = "<html>original</html>"
        compressed = zlib.compress(original_html.encode("utf-8"))

        mock_fetch.return_value = {
            "raw_html_compressed": compressed,
            "raw_html_hash": "wrong_hash_value",
        }

        with pytest.raises(ValueError, match="[Ii]ntegrity"):
            HTMLFetcher.get_snapshot(url_id=1, snapshot_id=42)

    @patch("html_fetcher.Database.fetch_one")
    def test_get_snapshot_not_found(self, mock_fetch):
        """Should return None when snapshot doesn't exist."""
        mock_fetch.return_value = None
        result = HTMLFetcher.get_snapshot(url_id=1, snapshot_id=999)
        assert result is None

    @patch("html_fetcher.Database.fetch_all")
    def test_list_snapshots(self, mock_fetch):
        """Should return snapshots ordered by snapshot_at DESC."""
        mock_fetch.return_value = [
            {"id": 2, "text_content_hash": "hash2", "raw_html_hash": "rhash2", "snapshot_at": "2026-03-15"},
            {"id": 1, "text_content_hash": "hash1", "raw_html_hash": "rhash1", "snapshot_at": "2026-03-01"},
        ]

        result = HTMLFetcher.list_snapshots(url_id=1)
        assert len(result) == 2
        assert result[0]["id"] == 2

    @patch("html_fetcher.Database.fetch_all")
    def test_list_snapshots_empty(self, mock_fetch):
        """Should return empty list when no snapshots exist."""
        mock_fetch.return_value = []
        result = HTMLFetcher.list_snapshots(url_id=1)
        assert result == []


class TestNormalizeText:
    """Tests for normalize_text() using real false-positive fixtures."""

    def test_collapses_whitespace(self):
        """Trailing spaces before parens — Hye Young You case."""
        old = "Pamela Ban and Ju Yeon Park )"
        new = "Pamela Ban and Ju Yeon Park)"
        assert HTMLFetcher.normalize_text(old) == HTMLFetcher.normalize_text(new)

    def test_normalizes_curly_quotes(self):
        """Curly quotes to straight — Lars Svensson case."""
        old = '\u201cSwedish household debt\u201d and \u2018solvency\u2019'
        expected = '"Swedish household debt" and \'solvency\''
        result = HTMLFetcher.normalize_text(old)
        assert result == expected

    def test_collapses_google_sites_word_splitting(self):
        """Google Sites rendering splits digits — Laurence van Lent case."""
        old = "Zhang, 202 6, The Accounting Review (conditionally accepted)"
        new = "Zhang, 2026, The Accounting Review (conditionally accepted)"
        assert HTMLFetcher.normalize_text(old) == HTMLFetcher.normalize_text(new)

    def test_strips_google_sites_boilerplate(self):
        """Google Sites nav/chrome should be stripped."""
        text = "Search this site Embedded Files Skip to main content Skip to navigation Home Research CV"
        result = HTMLFetcher.normalize_text(text)
        assert "Search this site" not in result
        assert "Skip to main content" not in result
        assert "Skip to navigation" not in result
        assert "Embedded Files" not in result
        # Real content preserved
        assert "Home" in result
        assert "Research" in result

    def test_strips_cookie_consent(self):
        """Cookie consent boilerplate should be stripped."""
        text = "Research papers This site uses cookies from Google to deliver its services and to analyze traffic Learn more Got it"
        result = HTMLFetcher.normalize_text(text)
        assert "cookies from Google" not in result
        assert "Learn more Got it" not in result
        assert "Research papers" in result

    def test_preserves_real_content_change(self):
        """Maria Silfa case — R&R status update must survive normalization."""
        old = 'How Crisis Reshapes Government Talent with Jacob R. Brown'
        new = 'How Crisis Reshapes Government Talent with Jacob R. Brown (R&R, American Political Science Review)'
        assert HTMLFetcher.normalize_text(old) != HTMLFetcher.normalize_text(new)

    def test_preserves_year_changes(self):
        """Year updates must not be normalized away."""
        old = "Working Paper, 2025"
        new = "Published, 2026"
        assert HTMLFetcher.normalize_text(old) != HTMLFetcher.normalize_text(new)

    def test_preserves_new_paper_title(self):
        """A new paper appearing must produce a different normalized result."""
        old = "Paper A, Paper B"
        new = "Paper A, Paper B, Paper C: New Findings"
        assert HTMLFetcher.normalize_text(old) != HTMLFetcher.normalize_text(new)

    def test_handles_empty_string(self):
        assert HTMLFetcher.normalize_text("") == ""

    def test_handles_whitespace_only(self):
        assert HTMLFetcher.normalize_text("   \n\t  ") == ""

    def test_non_breaking_space_collapsed(self):
        """Non-breaking spaces (\\u00a0) should be treated as whitespace."""
        text = "hello\u00a0\u00a0world"
        assert HTMLFetcher.normalize_text(text) == "hello world"


class TestNormalizationIntegration:
    """Integration: normalize_text is applied before hashing in fetch_and_save_if_changed."""

    def test_whitespace_only_change_not_detected(self):
        """Content differing only in whitespace should hash identically after normalization."""
        old_hash = HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text("Paper A (with Author )"))
        new_hash = HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text("Paper A (with Author)"))
        assert old_hash == new_hash

    def test_quote_only_change_not_detected(self):
        """Content differing only in quote style should hash identically after normalization."""
        old_text = '\u201cA Paper Title\u201d by Smith'
        new_text = '"A Paper Title" by Smith'
        assert HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(old_text)) == \
               HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(new_text))

    def test_real_change_still_detected(self):
        """Substantive changes must still produce different hashes."""
        old_text = "Paper A, working_paper"
        new_text = "Paper A, accepted at AER"
        assert HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(old_text)) != \
               HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(new_text))
