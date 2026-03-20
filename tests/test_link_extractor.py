"""Tests for link extraction and matching."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("CONTENT_MAX_CHARS", "4000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, MagicMock
import pytest
from html_fetcher import HTMLFetcher


class TestSaveTextWithRawHtml:
    @patch("html_fetcher.Database.execute_query")
    def test_save_text_stores_raw_html(self, mock_execute):
        HTMLFetcher.save_text(url_id=1, text_content="text", text_hash="abc",
                              researcher_id=10, raw_html="<html>test</html>")
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "raw_html" in sql
        assert "<html>test</html>" in params

    @patch("html_fetcher.Database.execute_query")
    def test_save_text_without_raw_html_passes_none(self, mock_execute):
        HTMLFetcher.save_text(url_id=1, text_content="text", text_hash="abc", researcher_id=10)
        assert mock_execute.call_args[0][1][-1] is None


from link_extractor import extract_trusted_links, match_link_to_paper, discover_untrusted_domains


class TestExtractTrustedLinks:
    def test_extracts_ssrn_link(self):
        html = '<div><a href="https://ssrn.com/abstract=1">Paper Title</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert links[0]['link_type'] == 'ssrn'

    def test_ignores_untrusted(self):
        html = '<div><a href="https://twitter.com/x">T</a><a href="https://ssrn.com/1">S</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1

    def test_ignores_non_article_journal_paths(self):
        html = '<div><a href="https://www.sciencedirect.com/topics/economics/x">topic</a><a href="https://www.sciencedirect.com/science/article/pii/S1">article</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1

    def test_strips_nav_footer(self):
        html = '<nav><a href="https://ssrn.com/1">X</a></nav><div><a href="https://ssrn.com/2">Y</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1

    def test_url_dedup_picks_best_anchor(self):
        html = '<div><a href="https://dropbox.com/paper.pdf">"</a><a href="https://dropbox.com/paper.pdf">My Great Paper Title</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert 'My Great Paper' in links[0]['anchor_text']

    def test_sibling_fallback_for_empty_anchor(self):
        html = '<p><a href="/local.pdf"><strong>Paper Title Here</strong></a><a href="https://nber.org/papers/w123"><br></a></p>'
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert 'Paper Title Here' in links[0]['anchor_text']

    def test_parent_text_fallback_for_generic_anchor(self):
        html = '<p>Incomplete Take-Up of Insurance Benefits (with J. Doe) [ <a href="https://ssrn.com/abstract=99">SSRN Version</a> ]</p>'
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert 'Incomplete Take-Up' in links[0]['anchor_text']
