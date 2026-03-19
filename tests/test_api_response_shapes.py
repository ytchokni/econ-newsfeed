"""Contract tests: API response shapes must match frontend types.ts.

These tests verify key presence (not values) to catch frontend/backend
drift. Field lists are derived from app/src/lib/types.ts interfaces.
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked database and scheduler."""
    with (
        patch("database.Database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
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

SAMPLE_PUB = (
    1, "Trade and Wages", "2024", "JLE", "https://example.com/p",
    datetime(2026, 3, 15, 14, 30), "published", "https://ssrn.com/1",
    "An abstract.", "valid",
)
SAMPLE_AUTHORS = [(1, 10, "Max", "Steinhardt")]
SAMPLE_RESEARCHER = (
    10, "Max", "Steinhardt", "Professor", "FU Berlin", "Economist."
)
SAMPLE_URLS = [(10, 1, "homepage", "https://example.com")]
SAMPLE_PUB_COUNTS = [(10, 5)]
SAMPLE_FIELDS = [(10, 1, "Labour Economics", "labour-economics")]
SAMPLE_SCRAPE = (
    1, "completed", datetime(2026, 3, 16, 10, 0),
    datetime(2026, 3, 16, 10, 5), 10, 2, 3,
)


# ---------------------------------------------------------------------------
# Publication contract tests
# ---------------------------------------------------------------------------

class TestPublicationShape:
    """GET /api/publications response matches types.ts Publication."""

    def test_paginated_envelope_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
            body = client.get("/api/publications").json()

        assert set(body.keys()) >= PAGINATED_KEYS

    def test_publication_item_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
            body = client.get("/api/publications").json()

        item = body["items"][0]
        assert set(item.keys()) >= PUBLICATION_KEYS, (
            f"Missing keys: {PUBLICATION_KEYS - set(item.keys())}"
        )

    def test_author_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
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
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
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
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
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
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
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
        ):
            mock_one.side_effect = [SAMPLE_RESEARCHER, (5,)]
            single_urls = [(1, "homepage", "https://example.com")]
            single_fields = [(1, "Labour Economics", "labour-economics")]
            mock_all.side_effect = [
                single_urls, single_fields,
                [SAMPLE_PUB], SAMPLE_AUTHORS,
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
            body = client.get("/api/scrape/status").json()

        assert set(body.keys()) >= SCRAPE_STATUS_KEYS, (
            f"Missing keys: {SCRAPE_STATUS_KEYS - set(body.keys())}"
        )

    def test_scrape_last_sub_object_keys(self, client):
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_SCRAPE),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            body = client.get("/api/scrape/status").json()

        last = body["last_scrape"]
        assert last is not None
        assert set(last.keys()) >= SCRAPE_LAST_KEYS, (
            f"Missing last_scrape keys: {SCRAPE_LAST_KEYS - set(last.keys())}"
        )
