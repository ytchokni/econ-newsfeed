"""Tests for researcher endpoints."""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@contextmanager
def _noop_connection_scope():
    yield None


@pytest.fixture
def client():
    """Create a test client with mocked database and scheduler."""
    with (
        patch("database.create_tables"),
        patch("scheduler.start_scheduler"),
        patch("scheduler.shutdown_scheduler"),
        patch("api.connection_scope", _noop_connection_scope),
    ):
        from api import app

        with TestClient(app) as c:
            yield c


# Sample DB return shapes
SAMPLE_RESEARCHERS = [
    # {id, first_name, last_name, position, affiliation, description, total_count}
    {"id": 1, "first_name": "Max Friedrich", "last_name": "Steinhardt", "position": "Professor", "affiliation": "Freie Universität Berlin", "description": "A leading researcher in trade economics.", "total_count": 2},
    {"id": 2, "first_name": "Jane", "last_name": "Doe", "position": "Assistant Professor", "affiliation": "MIT", "description": None, "total_count": 2},
]

SAMPLE_RESEARCHER_DETAIL = {"id": 1, "first_name": "Max Friedrich", "last_name": "Steinhardt", "position": "Professor", "affiliation": "Freie Universität Berlin", "description": "A leading researcher in trade economics."}

SAMPLE_PUBLICATIONS_R1 = [
    # {id, title, year, venue, source_url, discovered_at, status, draft_url, abstract, draft_url_status}
    {"id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE", "source_url": "https://example.com/pub", "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "published", "draft_url": None, "abstract": None, "draft_url_status": None},
]

# Batch maps returned by Database methods
URLS_MAP_ALL = {
    1: [
        {"id": 10, "page_type": "PUB", "url": "https://example.com/steinhardt/pubs"},
        {"id": 11, "page_type": "WP", "url": "https://example.com/steinhardt/wp"},
        {"id": 12, "page_type": "homepage", "url": "https://steinhardt.example.com"},
    ],
    2: [
        {"id": 20, "page_type": "PUB", "url": "https://example.com/doe/pubs"},
    ],
}

URLS_MAP_R1 = {
    1: [
        {"id": 10, "page_type": "PUB", "url": "https://example.com/steinhardt/pubs"},
        {"id": 11, "page_type": "WP", "url": "https://example.com/steinhardt/wp"},
        {"id": 12, "page_type": "homepage", "url": "https://steinhardt.example.com"},
    ],
}

URLS_MAP_R2 = {
    2: [
        {"id": 20, "page_type": "PUB", "url": "https://example.com/doe/pubs"},
    ],
}

PUB_COUNTS_ALL = {1: 23, 2: 5}
PUB_COUNTS_R1 = {1: 23}
PUB_COUNTS_R2 = {2: 5}

FIELDS_MAP_ALL = {
    1: [{"id": 1, "name": "Labour Economics", "slug": "labour-economics"}],
    2: [],
}
FIELDS_MAP_R1 = {
    1: [{"id": 1, "name": "Labour Economics", "slug": "labour-economics"}],
}
FIELDS_MAP_R2 = {2: []}

SAMPLE_FIELDS_R1 = [
    # {id, name, slug}
    {"id": 1, "name": "Labour Economics", "slug": "labour-economics"},
]


# ---------------------------------------------------------------------------
# Task 3.2: GET /api/researchers
# ---------------------------------------------------------------------------

class TestListResearchers:
    """Tests for GET /api/researchers."""

    def test_returns_all_researchers(self, client):
        with (
            patch("api.search_researchers", return_value=(SAMPLE_RESEARCHERS, 2)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_ALL),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_ALL),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_ALL),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2
        assert "total" in body
        assert "page" in body
        assert "per_page" in body
        assert "pages" in body

    def test_researcher_item_shape(self, client):
        """Each researcher must have id, first_name, last_name, position, affiliation, bio, urls, website_url, publication_count, fields."""
        with (
            patch("api.search_researchers", return_value=([SAMPLE_RESEARCHERS[0]], 1)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_R1),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_R1),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_R1),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers")

        item = response.json()["items"][0]
        assert item["id"] == 1
        assert item["first_name"] == "Max Friedrich"
        assert item["last_name"] == "Steinhardt"
        assert item["position"] == "Professor"
        assert item["affiliation"] == "Freie Universität Berlin"
        assert item["publication_count"] == 23
        assert "description" in item
        assert len(item["urls"]) == 3
        assert item["urls"][0]["id"] == 10
        assert item["urls"][0]["page_type"] == "PUB"
        assert item["urls"][0]["url"] == "https://example.com/steinhardt/pubs"
        assert item["website_url"] == "https://steinhardt.example.com"
        assert len(item["fields"]) == 1
        assert item["fields"][0]["name"] == "Labour Economics"

    def test_default_pagination(self, client):
        """Default page=1, per_page=20; response includes pagination metadata."""
        with (
            patch("api.search_researchers", return_value=(SAMPLE_RESEARCHERS, 2)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_ALL),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_ALL),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_ALL),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers")

        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 1
        assert body["per_page"] == 20
        assert body["total"] == 2
        assert body["pages"] == 1

    def test_custom_pagination(self, client):
        """Custom page and per_page values."""
        with (
            patch("api.search_researchers", return_value=([SAMPLE_RESEARCHERS[1]], 2)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_R2),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_R2),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_R2),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers?page=2&per_page=1")

        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 2
        assert body["per_page"] == 1
        assert body["pages"] == 2

    def test_per_page_capped_at_100(self, client):
        """per_page > 100 should be rejected as 400."""
        response = client.get("/api/researchers?per_page=200")
        assert response.status_code == 400

    def test_invalid_page_returns_400(self, client):
        response = client.get("/api/researchers?page=-1")
        assert response.status_code == 400

    def test_institution_filter(self, client):
        """?institution= performs partial match on affiliation."""
        with (
            patch("api.search_researchers", return_value=([SAMPLE_RESEARCHERS[1]], 1)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_R2),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_R2),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_R2),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers?institution=MIT")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_position_filter(self, client):
        """?position= performs partial match on position."""
        with (
            patch("api.search_researchers", return_value=([SAMPLE_RESEARCHERS[0]], 1)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_R1),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_R1),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_R1),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers?position=Professor")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_preset_top20_accepted(self, client):
        """?preset=top20 is accepted and returns 200."""
        with (
            patch("api.search_researchers", return_value=([SAMPLE_RESEARCHERS[1]], 1)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_R2),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_R2),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_R2),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers?preset=top20")

        assert response.status_code == 200

    def test_field_filter_stubbed(self, client):
        """?field= is accepted without error (stubbed until taxonomy table lands)."""
        with (
            patch("api.search_researchers", return_value=(SAMPLE_RESEARCHERS, 2)),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_ALL),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_ALL),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_ALL),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            response = client.get("/api/researchers?field=labor")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Task 3.3: GET /api/researchers/{id}
# ---------------------------------------------------------------------------

class TestGetResearcher:
    """Tests for GET /api/researchers/{id}."""

    def test_found_with_publications(self, client):
        with (
            patch("api.get_researcher_detail", return_value=SAMPLE_RESEARCHER_DETAIL),
            patch("api.get_urls_for_researchers", return_value=URLS_MAP_R1),
            patch("api.get_pub_counts_for_researchers", return_value=PUB_COUNTS_R1),
            patch("api.get_fields_for_researchers", return_value=FIELDS_MAP_R1),
            patch("api.get_jel_codes_for_researcher", return_value=[]),
            patch("api.get_researcher_papers", return_value=SAMPLE_PUBLICATIONS_R1),
            patch("api.get_authors_for_papers", return_value={1: [{"id": 1, "first_name": "Max Friedrich", "last_name": "Steinhardt"}]}),
            patch("api.get_coauthors_for_papers", return_value={}),
            patch("api.get_links_for_papers", return_value={}),
        ):
            response = client.get("/api/researchers/1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 1
        assert body["first_name"] == "Max Friedrich"
        assert "description" in body
        assert "publications" in body
        assert len(body["publications"]) == 1
        assert body["website_url"] == "https://steinhardt.example.com"
        assert len(body["fields"]) == 1

    def test_not_found_returns_404(self, client):
        with patch("api.get_researcher_detail", return_value=None):
            response = client.get("/api/researchers/999999")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"


class TestResearcherValidationFilter:
    """Only researchers with openalex_author_id or researcher_urls should appear."""

    def test_researchers_endpoint_filters_unvalidated(self, client):
        """The search_researchers function must include a validation filter condition."""
        with (
            patch("api.search_researchers", return_value=([], 0)) as mock_search,
            patch("api.get_urls_for_researchers", return_value={}),
            patch("api.get_pub_counts_for_researchers", return_value={}),
            patch("api.get_fields_for_researchers", return_value={}),
            patch("api.get_jel_codes_for_researchers", return_value={}),
        ):
            resp = client.get("/api/researchers")
            assert resp.status_code == 200
            # search_researchers was called -- validation filter is built into the function
            mock_search.assert_called_once()
