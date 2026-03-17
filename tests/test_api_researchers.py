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
    # (id, first_name, last_name, position, affiliation)
    (1, "Max Friedrich", "Steinhardt", "Professor", "Freie Universität Berlin"),
    (2, "Jane", "Doe", "Assistant Professor", "MIT"),
]

SAMPLE_URLS_R1 = [
    # (url_id, page_type, url) — used for single-researcher endpoint
    (10, "PUB", "https://example.com/steinhardt/pubs"),
    (11, "WP", "https://example.com/steinhardt/wp"),
    (12, "homepage", "https://steinhardt.example.com"),
]

SAMPLE_URLS_R2 = [
    (20, "PUB", "https://example.com/doe/pubs"),
]

SAMPLE_PUB_COUNT_R1 = (23,)
SAMPLE_PUB_COUNT_R2 = (5,)

# Batch formats for list endpoint
# (researcher_id, url_id, page_type, url)
BATCH_URLS_ALL = [
    (1, 10, "PUB", "https://example.com/steinhardt/pubs"),
    (1, 11, "WP", "https://example.com/steinhardt/wp"),
    (2, 20, "PUB", "https://example.com/doe/pubs"),
]
BATCH_URLS_R1 = [
    (1, 10, "PUB", "https://example.com/steinhardt/pubs"),
    (1, 11, "WP", "https://example.com/steinhardt/wp"),
]
# (researcher_id, count)
BATCH_PUB_COUNTS_ALL = [(1, 23), (2, 5)]
BATCH_PUB_COUNTS_R1 = [(1, 23)]
# (researcher_id, field.id, field.name, field.slug)
BATCH_FIELDS_ALL = [(1, 1, "Labour Economics", "labour-economics")]
BATCH_FIELDS_R1 = [(1, 1, "Labour Economics", "labour-economics")]

SAMPLE_RESEARCHER_DETAIL = (1, "Max Friedrich", "Steinhardt", "Professor", "Freie Universität Berlin")

SAMPLE_PUBLICATIONS_R1 = [
    # (pub.id, pub.title, pub.year, pub.venue, pub.url, pub.timestamp, pub.status, pub.draft_url)
    (1, "Trade and Wages", "2024", "JLE", "https://example.com/pub", datetime(2026, 3, 15, 14, 30), "published", None),
]

SAMPLE_FIELDS_R1 = [
    # (field.id, field.name, field.slug)
    (1, "Labour Economics", "labour-economics"),
]


# ---------------------------------------------------------------------------
# Task 3.2: GET /api/researchers
# ---------------------------------------------------------------------------

class TestListResearchers:
    """Tests for GET /api/researchers."""

    def test_returns_all_researchers(self, client):
        with patch("api.Database.fetch_all") as mock_fetch:
            mock_fetch.side_effect = [
                SAMPLE_RESEARCHERS,      # researchers query
                BATCH_URLS_ALL,          # batch URLs for all researchers
                BATCH_PUB_COUNTS_ALL,    # batch pub counts for all researchers
                BATCH_FIELDS_ALL,        # batch fields for all researchers
            ]
            response = client.get("/api/researchers")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2

    def test_researcher_item_shape(self, client):
        """Each researcher must have id, first_name, last_name, position, affiliation, urls, website_url, publication_count, fields."""
        with patch("api.Database.fetch_all") as mock_fetch:
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
        assert len(item["urls"]) == 3
        assert item["urls"][0]["id"] == 10
        assert item["urls"][0]["page_type"] == "PUB"
        assert item["urls"][0]["url"] == "https://example.com/steinhardt/pubs"
        assert item["website_url"] == "https://steinhardt.example.com"
        assert len(item["fields"]) == 1
        assert item["fields"][0]["name"] == "Labour Economics"


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
                [(1, 1, "Max Friedrich", "Steinhardt")],  # batch authors: (pub_id, r_id, first, last)
            ]
            response = client.get("/api/researchers/1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 1
        assert body["first_name"] == "Max Friedrich"
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
