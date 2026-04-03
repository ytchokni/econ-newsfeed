"""Contract tests: API response shapes must match frontend types.ts.

These tests verify key presence (not values) to catch frontend/backend
drift. Field lists are derived from app/src/lib/types.ts interfaces.
"""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import os
import pytest
from fastapi.testclient import TestClient

AUTH_HEADERS = {"X-API-Key": os.environ["SCRAPE_API_KEY"]}


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def client():
    """Create a test client with mocked database and scheduler.

    Patches both scheduler.* and api.* because test_imports.py may
    reimport api outside patch context, binding the real functions.
    """
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
        patch("api.start_scheduler"),
        patch("api.shutdown_scheduler"),
        patch("api.connection_scope", _noop_connection_scope),
    ):
        from api import app
        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# Expected key sets (derived from app/src/lib/types.ts)
# ---------------------------------------------------------------------------

PAGINATED_KEYS = {"items", "total", "page", "per_page", "pages"}

PUBLICATION_KEYS = {
    "id", "title", "authors", "year", "venue", "source_url",
    "discovered_at", "status", "abstract", "draft_url",
    "draft_url_status", "draft_available",
    "doi", "coauthors", "links",
    "event_id", "event_type", "old_status", "new_status", "event_date",
}

AUTHOR_KEYS = {"id", "first_name", "last_name"}

RESEARCHER_KEYS = {
    "id", "first_name", "last_name", "position", "affiliation",
    "description", "urls", "website_url", "publication_count", "fields",
}

RESEARCHER_URL_KEYS = {"id", "page_type", "url"}

RESEARCH_FIELD_KEYS = {"id", "name", "slug"}

RESEARCHER_DETAIL_EXTRA_KEYS = {"publications"}

SCRAPE_STATUS_KEYS = {"last_scrape", "next_scrape_at", "interval_hours"}

SCRAPE_LAST_KEYS = {
    "id", "status", "started_at", "finished_at",
    "urls_checked", "urls_changed", "pubs_extracted",
}


# ---------------------------------------------------------------------------
# Sample data (minimal, just enough to produce responses)
# ---------------------------------------------------------------------------

# Feed events row shape: fe.id, fe.event_type, fe.old_status, fe.new_status, fe.created_at,
#   p.id, p.title, p.year, p.venue, p.url, p.timestamp, p.status, p.draft_url, p.abstract, p.draft_url_status
SAMPLE_PUB = {
    "event_id": 100, "event_type": "new_paper", "old_status": None,
    "new_status": "working_paper", "old_title": None, "new_title": None,
    "created_at": datetime(2026, 3, 15, 14, 30),
    "paper_id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/p",
    "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "working_paper",
    "draft_url": "https://ssrn.com/1", "abstract": "An abstract.",
    "draft_url_status": "valid", "doi": None,
    "total_count": 1,
}
SAMPLE_AUTHORS = [{"publication_id": 1, "researcher_id": 10, "first_name": "Max", "last_name": "Steinhardt"}]
# Single publication detail (10-column papers row, used by GET /api/publications/{id} and researcher detail)
SAMPLE_PUB_DETAIL = {
    "id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/p",
    "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "working_paper",
    "draft_url": "https://ssrn.com/1", "abstract": "An abstract.",
    "draft_url_status": "valid",
}
SAMPLE_RESEARCHER = {
    "id": 10, "first_name": "Max", "last_name": "Steinhardt",
    "position": "Professor", "affiliation": "FU Berlin",
    "description": "Economist.",
    "total_count": 1,
}
SAMPLE_URLS = [{"researcher_id": 10, "id": 1, "page_type": "homepage", "url": "https://example.com"}]
SAMPLE_PUB_COUNTS = [{"researcher_id": 10, "cnt": 5}]
SAMPLE_FIELDS = [{"researcher_id": 10, "id": 1, "name": "Labour Economics", "slug": "labour-economics"}]
SAMPLE_SCRAPE = {
    "id": 1, "status": "completed",
    "started_at": datetime(2026, 3, 16, 10, 0),
    "finished_at": datetime(2026, 3, 16, 10, 5),
    "urls_checked": 10, "urls_changed": 2, "pubs_extracted": 3,
}


# ---------------------------------------------------------------------------
# Publication contract tests
# ---------------------------------------------------------------------------

class TestPublicationShape:
    """GET /api/publications response matches types.ts Publication."""

    def test_paginated_envelope_keys(self, client):
        with patch("api.Database.fetch_all") as mock_all:
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS, [], []]  # pubs, authors, coauthors, links
            body = client.get("/api/publications").json()

        assert set(body.keys()) >= PAGINATED_KEYS

    def test_publication_item_keys(self, client):
        with patch("api.Database.fetch_all") as mock_all:
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS, [], []]  # pubs, authors, coauthors, links
            body = client.get("/api/publications").json()

        item = body["items"][0]
        assert set(item.keys()) >= PUBLICATION_KEYS, (
            f"Missing keys: {PUBLICATION_KEYS - set(item.keys())}"
        )

    def test_author_sub_object_keys(self, client):
        with patch("api.Database.fetch_all") as mock_all:
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS, [], []]  # pubs, authors, coauthors, links
            body = client.get("/api/publications").json()

        author = body["items"][0]["authors"][0]
        assert set(author.keys()) >= AUTHOR_KEYS, (
            f"Missing author keys: {AUTHOR_KEYS - set(author.keys())}"
        )


# ---------------------------------------------------------------------------
# Researcher contract tests
# ---------------------------------------------------------------------------

class TestResearcherShape:
    """GET /api/researchers response matches types.ts Researcher."""

    def test_researcher_list_item_keys(self, client):
        with (
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.get_jel_codes_for_researchers", return_value={}),
        ):
            mock_all.side_effect = [
                [SAMPLE_RESEARCHER], SAMPLE_URLS,
                SAMPLE_PUB_COUNTS, SAMPLE_FIELDS,
            ]
            body = client.get("/api/researchers").json()

        assert set(body.keys()) >= PAGINATED_KEYS
        item = body["items"][0]
        assert set(item.keys()) >= RESEARCHER_KEYS, (
            f"Missing keys: {RESEARCHER_KEYS - set(item.keys())}"
        )

    def test_researcher_url_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.get_jel_codes_for_researchers", return_value={}),
        ):
            mock_all.side_effect = [
                [SAMPLE_RESEARCHER], SAMPLE_URLS,
                SAMPLE_PUB_COUNTS, SAMPLE_FIELDS,
            ]
            body = client.get("/api/researchers").json()

        url_obj = body["items"][0]["urls"][0]
        assert set(url_obj.keys()) >= RESEARCHER_URL_KEYS, (
            f"Missing url keys: {RESEARCHER_URL_KEYS - set(url_obj.keys())}"
        )

    def test_research_field_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.get_jel_codes_for_researchers", return_value={}),
        ):
            mock_all.side_effect = [
                [SAMPLE_RESEARCHER], SAMPLE_URLS,
                SAMPLE_PUB_COUNTS, SAMPLE_FIELDS,
            ]
            body = client.get("/api/researchers").json()

        field_obj = body["items"][0]["fields"][0]
        assert set(field_obj.keys()) >= RESEARCH_FIELD_KEYS, (
            f"Missing field keys: {RESEARCH_FIELD_KEYS - set(field_obj.keys())}"
        )


# ---------------------------------------------------------------------------
# Researcher detail contract tests
# ---------------------------------------------------------------------------

class TestResearcherDetailShape:
    """GET /api/researchers/{id} matches types.ts ResearcherDetail."""

    def test_detail_has_publications_key(self, client):
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.get_jel_codes_for_researcher", return_value=[]),
        ):
            mock_one.side_effect = [SAMPLE_RESEARCHER, {"cnt": 5}]
            single_urls = [{"id": 1, "page_type": "homepage", "url": "https://example.com"}]
            single_fields = [{"id": 1, "name": "Labour Economics", "slug": "labour-economics"}]
            mock_all.side_effect = [
                single_urls, single_fields,
                [SAMPLE_PUB_DETAIL], SAMPLE_AUTHORS, [], [],  # urls, fields, pubs, authors, coauthors, links
            ]
            body = client.get("/api/researchers/10").json()

        all_expected = RESEARCHER_KEYS | RESEARCHER_DETAIL_EXTRA_KEYS
        assert set(body.keys()) >= all_expected, (
            f"Missing keys: {all_expected - set(body.keys())}"
        )
        assert isinstance(body["publications"], list)


# ---------------------------------------------------------------------------
# Scrape status contract tests
# ---------------------------------------------------------------------------

class TestScrapeStatusShape:
    """GET /api/scrape/status matches expected API contract."""

    def test_scrape_status_top_level_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_SCRAPE),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            body = client.get("/api/scrape/status", headers=AUTH_HEADERS).json()

        assert set(body.keys()) >= SCRAPE_STATUS_KEYS, (
            f"Missing keys: {SCRAPE_STATUS_KEYS - set(body.keys())}"
        )

    def test_scrape_last_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_SCRAPE),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            body = client.get("/api/scrape/status", headers=AUTH_HEADERS).json()

        last = body["last_scrape"]
        assert last is not None
        assert set(last.keys()) >= SCRAPE_LAST_KEYS, (
            f"Missing last_scrape keys: {SCRAPE_LAST_KEYS - set(last.keys())}"
        )
