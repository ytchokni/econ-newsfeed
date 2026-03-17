"""Tests for publication endpoints."""
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


# Sample data that mimics Database.fetch_all / fetch_one return shapes
SAMPLE_PUBLICATIONS = [
    # (pub.id, pub.title, pub.year, pub.venue, pub.url, pub.timestamp, pub.status, pub.draft_url)
    (1, "Trade and Wages", "2024", "JLE", "https://example.com/pub", datetime(2026, 3, 15, 14, 30), "published", "https://ssrn.com/abstract=1"),
    (2, "Immigration Effects", "2023", "QJE", "https://example.com/pub2", datetime(2026, 3, 14, 10, 0), "accepted", None),
    (3, "Labor Markets", "2024", "AER", "https://example.com/pub3", datetime(2026, 3, 13, 9, 0), None, None),
]

SAMPLE_AUTHORS_PUB1 = [
    # (researcher_id, first_name, last_name)
    (10, "Max Friedrich", "Steinhardt"),
    (11, "Jane", "Doe"),
]

SAMPLE_AUTHORS_PUB2 = [
    (10, "Max Friedrich", "Steinhardt"),
]


# ---------------------------------------------------------------------------
# Task 2.2: GET /api/publications
# ---------------------------------------------------------------------------

class TestListPublications:
    """Tests for GET /api/publications."""

    def test_default_pagination(self, client):
        """Default page=1, per_page=20."""
        with (
            patch("api.Database.fetch_one", return_value=(3,)),  # total count
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            # First call: publications query; second+: authors per publication
            mock_fetch.side_effect = [
                SAMPLE_PUBLICATIONS,
                SAMPLE_AUTHORS_PUB1,
                SAMPLE_AUTHORS_PUB2,
                [],  # pub3 has no authors in mock
            ]
            response = client.get("/api/publications")

        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 1
        assert body["per_page"] == 20
        assert body["total"] == 3
        assert body["pages"] == 1
        assert len(body["items"]) == 3

    def test_custom_pagination(self, client):
        """Custom page and per_page values."""
        with (
            patch("api.Database.fetch_one", return_value=(3,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[1]],
                SAMPLE_AUTHORS_PUB2,
            ]
            response = client.get("/api/publications?page=2&per_page=1")

        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 2
        assert body["per_page"] == 1
        assert body["pages"] == 3

    def test_per_page_capped_at_100(self, client):
        """per_page > 100 should be rejected as 400."""
        response = client.get("/api/publications?per_page=200")
        assert response.status_code == 400

    def test_year_filter(self, client):
        """Filter publications by year."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[0]],
                SAMPLE_AUTHORS_PUB1,
            ]
            response = client.get("/api/publications?year=2024")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_researcher_id_filter(self, client):
        """Filter publications by researcher_id."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[0]],
                SAMPLE_AUTHORS_PUB1,
            ]
            response = client.get("/api/publications?researcher_id=10")

        assert response.status_code == 200

    def test_invalid_page_returns_400(self, client):
        response = client.get("/api/publications?page=-1")
        assert response.status_code == 400

    def test_publication_item_shape(self, client):
        """Each item must have id, title, authors, year, venue, source_url, discovered_at."""
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[0]],
                SAMPLE_AUTHORS_PUB1,
            ]
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert item["id"] == 1
        assert item["title"] == "Trade and Wages"
        assert item["year"] == "2024"
        assert item["venue"] == "JLE"
        assert item["source_url"] == "https://example.com/pub"
        assert "discovered_at" in item
        assert item["status"] == "published"
        assert item["draft_url"] == "https://ssrn.com/abstract=1"
        assert item["draft_available"] is True
        assert len(item["authors"]) == 2
        assert item["authors"][0]["id"] == 10
        assert item["authors"][0]["first_name"] == "Max Friedrich"
        assert item["authors"][0]["last_name"] == "Steinhardt"


# ---------------------------------------------------------------------------
# Task 2.3: GET /api/publications/{id}
# ---------------------------------------------------------------------------

class TestGetPublication:
    """Tests for GET /api/publications/{id}."""

    def test_found_with_authors(self, client):
        """Returns publication with nested authors."""
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_PUBLICATIONS[0]),
            patch("api.Database.fetch_all", return_value=SAMPLE_AUTHORS_PUB1),
        ):
            response = client.get("/api/publications/1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 1
        assert body["title"] == "Trade and Wages"
        assert len(body["authors"]) == 2

    def test_not_found_returns_404(self, client):
        """Non-existent publication returns 404 with error envelope."""
        with patch("api.Database.fetch_one", return_value=None):
            response = client.get("/api/publications/999999")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
