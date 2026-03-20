"""Tests for researcher endpoints."""
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


# Sample DB return shapes
SAMPLE_RESEARCHERS = [
    # {id, first_name, last_name, position, affiliation, description}
    {"id": 1, "first_name": "Max Friedrich", "last_name": "Steinhardt", "position": "Professor", "affiliation": "Freie Universität Berlin", "description": "A leading researcher in trade economics."},
    {"id": 2, "first_name": "Jane", "last_name": "Doe", "position": "Assistant Professor", "affiliation": "MIT", "description": None},
]

SAMPLE_URLS_R1 = [
    # {id, page_type, url} — used for single-researcher endpoint
    {"id": 10, "page_type": "PUB", "url": "https://example.com/steinhardt/pubs"},
    {"id": 11, "page_type": "WP", "url": "https://example.com/steinhardt/wp"},
    {"id": 12, "page_type": "homepage", "url": "https://steinhardt.example.com"},
]

SAMPLE_URLS_R2 = [
    {"id": 20, "page_type": "PUB", "url": "https://example.com/doe/pubs"},
]

SAMPLE_PUB_COUNT_R1 = {"cnt": 23}
SAMPLE_PUB_COUNT_R2 = {"cnt": 5}

# Batch formats for list endpoint
# {researcher_id, id, page_type, url}
BATCH_URLS_ALL = [
    {"researcher_id": 1, "id": 10, "page_type": "PUB", "url": "https://example.com/steinhardt/pubs"},
    {"researcher_id": 1, "id": 11, "page_type": "WP", "url": "https://example.com/steinhardt/wp"},
    {"researcher_id": 1, "id": 12, "page_type": "homepage", "url": "https://steinhardt.example.com"},
    {"researcher_id": 2, "id": 20, "page_type": "PUB", "url": "https://example.com/doe/pubs"},
]
BATCH_URLS_R1 = [
    {"researcher_id": 1, "id": 10, "page_type": "PUB", "url": "https://example.com/steinhardt/pubs"},
    {"researcher_id": 1, "id": 11, "page_type": "WP", "url": "https://example.com/steinhardt/wp"},
    {"researcher_id": 1, "id": 12, "page_type": "homepage", "url": "https://steinhardt.example.com"},
]
BATCH_URLS_R2 = [
    {"researcher_id": 2, "id": 20, "page_type": "PUB", "url": "https://example.com/doe/pubs"},
]
# {researcher_id, cnt}
BATCH_PUB_COUNTS_ALL = [{"researcher_id": 1, "cnt": 23}, {"researcher_id": 2, "cnt": 5}]
BATCH_PUB_COUNTS_R1 = [{"researcher_id": 1, "cnt": 23}]
BATCH_PUB_COUNTS_R2 = [{"researcher_id": 2, "cnt": 5}]
# {researcher_id, id, name, slug}
BATCH_FIELDS_ALL = [{"researcher_id": 1, "id": 1, "name": "Labour Economics", "slug": "labour-economics"}]
BATCH_FIELDS_R1 = [{"researcher_id": 1, "id": 1, "name": "Labour Economics", "slug": "labour-economics"}]
BATCH_FIELDS_R2 = []

SAMPLE_RESEARCHER_DETAIL = {"id": 1, "first_name": "Max Friedrich", "last_name": "Steinhardt", "position": "Professor", "affiliation": "Freie Universität Berlin", "description": "A leading researcher in trade economics."}

SAMPLE_PUBLICATIONS_R1 = [
    # {id, title, year, venue, source_url, discovered_at, status, draft_url, abstract, draft_url_status}
    {"id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE", "source_url": "https://example.com/pub", "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "published", "draft_url": None, "abstract": None, "draft_url_status": None},
]

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
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 2},            # total count
            ]
            mock_fetch.side_effect = [
                SAMPLE_RESEARCHERS,      # paginated researchers
                BATCH_URLS_ALL,          # batch URLs for all researchers
                BATCH_PUB_COUNTS_ALL,    # batch pub counts for all researchers
                BATCH_FIELDS_ALL,        # batch fields for all researchers
            ]
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
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 1},        # total count
            ]
            mock_fetch.side_effect = [
                [SAMPLE_RESEARCHERS[0]],
                BATCH_URLS_R1,
                BATCH_PUB_COUNTS_R1,
                BATCH_FIELDS_R1,
            ]
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
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 2},            # total count
            ]
            mock_fetch.side_effect = [
                SAMPLE_RESEARCHERS,      # paginated researchers
                BATCH_URLS_ALL,          # batch URLs
                BATCH_PUB_COUNTS_ALL,    # batch pub counts
                BATCH_FIELDS_ALL,        # batch fields
            ]
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
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 2},            # total count
            ]
            mock_fetch.side_effect = [
                [SAMPLE_RESEARCHERS[1]], # paginated researchers (page 2)
                BATCH_URLS_R2,           # batch URLs
                BATCH_PUB_COUNTS_R2,     # batch pub counts
                BATCH_FIELDS_R2,         # batch fields
            ]
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
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 1},            # total count
            ]
            mock_fetch.side_effect = [
                [SAMPLE_RESEARCHERS[1]], # paginated researchers
                BATCH_URLS_R2,           # batch URLs
                BATCH_PUB_COUNTS_R2,     # batch pub counts
                BATCH_FIELDS_R2,         # batch fields
            ]
            response = client.get("/api/researchers?institution=MIT")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_position_filter(self, client):
        """?position= performs partial match on position."""
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 1},                # total count
            ]
            mock_fetch.side_effect = [
                [SAMPLE_RESEARCHERS[0]],     # paginated researchers
                BATCH_URLS_R1,               # batch URLs
                BATCH_PUB_COUNTS_R1,         # batch pub counts
                BATCH_FIELDS_R1,             # batch fields
            ]
            response = client.get("/api/researchers?position=Professor")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_preset_top20_accepted(self, client):
        """?preset=top20 is accepted and returns 200."""
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 1},                # total count
            ]
            mock_fetch.side_effect = [
                [SAMPLE_RESEARCHERS[1]],     # paginated researchers
                BATCH_URLS_R2,               # batch URLs
                BATCH_PUB_COUNTS_R2,         # batch pub counts
                BATCH_FIELDS_R2,             # batch fields
            ]
            response = client.get("/api/researchers?preset=top20")

        assert response.status_code == 200

    def test_field_filter_stubbed(self, client):
        """?field= is accepted without error (stubbed until taxonomy table lands)."""
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                {"total": 2},            # total count
            ]
            mock_fetch.side_effect = [
                SAMPLE_RESEARCHERS,      # paginated researchers
                BATCH_URLS_ALL,          # batch URLs
                BATCH_PUB_COUNTS_ALL,    # batch pub counts
                BATCH_FIELDS_ALL,        # batch fields
            ]
            response = client.get("/api/researchers?field=labor")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Task 3.3: GET /api/researchers/{id}
# ---------------------------------------------------------------------------

class TestGetResearcher:
    """Tests for GET /api/researchers/{id}."""

    def test_found_with_publications(self, client):
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_one.side_effect = [
                SAMPLE_RESEARCHER_DETAIL,    # researcher row
                SAMPLE_PUB_COUNT_R1,         # pub count
            ]
            mock_fetch.side_effect = [
                SAMPLE_URLS_R1,              # urls
                SAMPLE_FIELDS_R1,            # fields
                SAMPLE_PUBLICATIONS_R1,      # publications
                [{"publication_id": 1, "researcher_id": 1, "first_name": "Max Friedrich", "last_name": "Steinhardt"}],  # batch authors
                [],                          # batch links
            ]
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
        with patch("api.Database.fetch_one", return_value=None):
            response = client.get("/api/researchers/999999")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
