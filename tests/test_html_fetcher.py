"""Tests for HTMLFetcher: robots.txt caching."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "4000")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")

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

        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch.object(HTMLFetcher.session, 'get', return_value=mock_resp):
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

        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch('time.sleep'), \
             patch.object(HTMLFetcher.session, 'get', side_effect=[error_resp, ok_resp]):
            result = HTMLFetcher.fetch_html("https://example.com")

        assert result == "ok"

    def test_returns_none_after_max_retries(self):
        error_resp = MagicMock()
        error_resp.status_code = 500

        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch('time.sleep'), \
             patch.object(HTMLFetcher.session, 'get', return_value=error_resp):
            result = HTMLFetcher.fetch_html("https://example.com", max_retries=2)

        assert result is None

    def test_rejects_oversized_response(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"x" * 1_000_001  # Just over CONTENT_MAX_BYTES (1MB)

        with patch.object(HTMLFetcher, '_rate_limit'), \
             patch.object(HTMLFetcher.session, 'get', return_value=mock_resp):
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
