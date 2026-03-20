"""Tests for link extraction and matching.

Env vars are set by conftest.py (auto-loaded by pytest for tests/).
"""
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


from link_extractor import extract_trusted_links, match_link_to_paper, discover_untrusted_domains, match_and_save_paper_links


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


class TestMatchLinkToPaper:
    def test_exact_match(self):
        title, _ = match_link_to_paper("Trade and Wages", ["Trade and Wages", "Other"])
        assert title == "Trade and Wages"

    def test_no_match_generic(self):
        title, _ = match_link_to_paper("PDF", ["Trade and Wages"])
        assert title is None

    def test_css_concatenation(self):
        title, _ = match_link_to_paper(
            "Outforgood: Transitory andpersistent labor",
            ["Out for good: Transitory and persistent labor"])
        assert title is not None

    def test_no_false_positive_short_substring(self):
        title, _ = match_link_to_paper("Green Waste", ["The Green Waste Tax Problem"])
        assert title is None

    def test_accented_characters(self):
        title, _ = match_link_to_paper(
            "Économétrie: Résumé des Résultats",
            ["Econometrie: Resume des Resultats"])
        assert title is not None


class TestMatchAndSavePaperLinks:
    @patch("link_extractor.Database.execute_query")
    @patch("link_extractor.Database.fetch_all")
    @patch("link_extractor.Database.compute_title_hash", return_value="abc123")
    @patch("link_extractor.HTMLFetcher.get_raw_html")
    def test_matches_and_saves(self, mock_get_raw, mock_hash, mock_fetch_all, mock_execute):
        html = '<div><a href="https://ssrn.com/1">Trade and Wages</a></div>'
        mock_get_raw.return_value = html
        # Batch query returns matching paper
        mock_fetch_all.return_value = [{'id': 10, 'title_hash': 'abc123'}]

        match_and_save_paper_links(url_id=1, publications=[{'title': 'Trade and Wages'}])

        link_calls = [c for c in mock_execute.call_args_list if 'paper_links' in c[0][0]]
        assert len(link_calls) == 1
        assert link_calls[0][0][1][0] == 10  # paper_id

    @patch("link_extractor.Database.execute_query")
    @patch("link_extractor.HTMLFetcher.get_raw_html")
    def test_skips_no_raw_html(self, mock_get_raw, mock_execute):
        mock_get_raw.return_value = None
        match_and_save_paper_links(url_id=1, publications=[{'title': 'X'}])
        assert not any('paper_links' in str(c) for c in mock_execute.call_args_list)


class TestDiscoverUntrustedDomains:
    def test_finds_untrusted_domain_with_title_anchor(self):
        html = '<div><a href="https://ssrn.com/1">Known Link</a><a href="https://newjournal.org/article/123">Some Long Paper Title Here</a><a href="https://twitter.com/x">Short</a></div>'
        domains = discover_untrusted_domains(html)
        assert 'newjournal.org' in domains
        assert 'twitter.com' not in domains
        assert 'ssrn.com' not in domains

    def test_returns_empty_for_all_trusted(self):
        html = '<div><a href="https://ssrn.com/1">Paper Title</a></div>'
        assert discover_untrusted_domains(html) == {}


class TestApiPaperLinks:
    @patch("api.Database.fetch_all")
    @patch("api.Database.fetch_one")
    def test_publication_detail_includes_links(self, mock_fetch_one, mock_fetch_all, client):
        mock_fetch_one.return_value = {
            "id": 1, "title": "Test", "year": "2024", "venue": "AER",
            "source_url": "https://x.com", "discovered_at": "2024-01-01T00:00:00",
            "status": "working_paper", "draft_url": None,
            "draft_url_status": "unchecked", "abstract": None,
        }
        mock_fetch_all.side_effect = [
            [{"id": 1, "first_name": "J", "last_name": "S"}],  # authors
            [{"url": "https://ssrn.com/1", "link_type": "ssrn"}],  # links
        ]
        resp = client.get("/api/publications/1")
        assert resp.status_code == 200
        assert "links" in resp.json()
        assert resp.json()["links"][0]["link_type"] == "ssrn"
