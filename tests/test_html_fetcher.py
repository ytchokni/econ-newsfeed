"""Tests for HTMLFetcher: robots.txt caching."""
import os
import sys

os.environ.setdefault("CONTENT_MAX_CHARS", "4000")

# Stub out the database module before importing html_fetcher, since the
# database package has broken imports in this worktree that are unrelated
# to HTMLFetcher functionality.
from unittest.mock import MagicMock
sys.modules.setdefault("database", MagicMock())

from html_fetcher import HTMLFetcher  # noqa: E402

from unittest.mock import patch


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
