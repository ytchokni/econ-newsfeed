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


# Sample data that mimics Database.fetch_all / fetch_one return shapes (dicts)
# Feed events row shape (15 keys): event_id, event_type, old_status, new_status, created_at,
#   paper_id, title, year, venue, url, discovered_at, status, draft_url, abstract, draft_url_status
SAMPLE_PUBLICATIONS = [
    {"event_id": 100, "event_type": "new_paper", "old_status": None, "new_status": "working_paper",
     "created_at": datetime(2026, 3, 15, 14, 30), "paper_id": 1, "title": "Trade and Wages",
     "year": "2024", "venue": "JLE", "source_url": "https://example.com/pub",
     "discovered_at": datetime(2026, 3, 15, 14, 30), "status": "working_paper",
     "draft_url": "https://ssrn.com/abstract=1", "abstract": None, "draft_url_status": "valid"},
    {"event_id": 101, "event_type": "new_paper", "old_status": None, "new_status": "accepted",
     "created_at": datetime(2026, 3, 14, 10, 0), "paper_id": 2, "title": "Immigration Effects",
     "year": "2023", "venue": "QJE", "source_url": "https://example.com/pub2",
     "discovered_at": datetime(2026, 3, 14, 10, 0), "status": "accepted",
     "draft_url": None, "abstract": None, "draft_url_status": None},
    {"event_id": 102, "event_type": "new_paper", "old_status": None, "new_status": "working_paper",
     "created_at": datetime(2026, 3, 13, 9, 0), "paper_id": 3, "title": "Labor Markets",
     "year": "2024", "venue": "AER", "source_url": "https://example.com/pub3",
     "discovered_at": datetime(2026, 3, 13, 9, 0), "status": "working_paper",
     "draft_url": None, "abstract": None, "draft_url_status": None},
]

# 10-key papers dict for single publication detail endpoint
SAMPLE_PUB_DETAIL = {
    "id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/pub", "discovered_at": datetime(2026, 3, 15, 14, 30),
    "status": "working_paper", "draft_url": "https://ssrn.com/abstract=1",
    "abstract": None, "draft_url_status": "valid",
}

SAMPLE_AUTHORS_PUB1 = [
    # {id, first_name, last_name} — used for single-pub endpoint
    {"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
    {"id": 11, "first_name": "Jane", "last_name": "Doe"},
]

SAMPLE_AUTHORS_PUB2 = [
    {"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
]

# Batch author format: {publication_id, researcher_id, first_name, last_name}
BATCH_AUTHORS_PUBS_1_2_3 = [
    {"publication_id": 1, "researcher_id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
    {"publication_id": 1, "researcher_id": 11, "first_name": "Jane", "last_name": "Doe"},
    {"publication_id": 2, "researcher_id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
]

BATCH_AUTHORS_PUB1 = [
    {"publication_id": 1, "researcher_id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
    {"publication_id": 1, "researcher_id": 11, "first_name": "Jane", "last_name": "Doe"},
]

BATCH_AUTHORS_PUB2 = [
    {"publication_id": 2, "researcher_id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"},
]


# ---------------------------------------------------------------------------
# Task 2.2: GET /api/publications
# ---------------------------------------------------------------------------

class TestListPublications:
    """Tests for GET /api/publications."""

    def test_default_pagination(self, client):
        """Default page=1, per_page=20."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 3}),  # total count
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            # First call: publications; second: batch authors for all pubs
            mock_fetch.side_effect = [
                SAMPLE_PUBLICATIONS,
                BATCH_AUTHORS_PUBS_1_2_3,
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
            patch("api.Database.fetch_one", return_value={"total": 3}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[1]],
                BATCH_AUTHORS_PUB2,
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
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[0]],
                BATCH_AUTHORS_PUB1,
            ]
            response = client.get("/api/publications?year=2024")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_researcher_id_filter(self, client):
        """Filter publications by researcher_id."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[0]],
                BATCH_AUTHORS_PUB1,
            ]
            response = client.get("/api/publications?researcher_id=10")

        assert response.status_code == 200

    def test_invalid_page_returns_400(self, client):
        response = client.get("/api/publications?page=-1")
        assert response.status_code == 400

    def test_since_filter(self, client):
        """?since= filters publications discovered after the given timestamp."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[0]],
                BATCH_AUTHORS_PUB1,
            ]
            response = client.get("/api/publications?since=2026-03-15T00:00:00Z")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1

    def test_since_invalid_format_returns_400(self, client):
        """Invalid ?since= value returns 400."""
        response = client.get("/api/publications?since=not-a-date")
        assert response.status_code == 400

    def test_publication_item_shape(self, client):
        """Each item must have id, title, authors, year, venue, source_url, discovered_at."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [
                [SAMPLE_PUBLICATIONS[0]],
                BATCH_AUTHORS_PUB1,
            ]
            response = client.get("/api/publications")

        item = response.json()["items"][0]
        assert item["id"] == 1
        assert item["title"] == "Trade and Wages"
        assert item["year"] == "2024"
        assert item["venue"] == "JLE"
        assert item["source_url"] == "https://example.com/pub"
        assert "discovered_at" in item
        assert item["status"] == "working_paper"
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
            patch("api.Database.fetch_one", return_value=SAMPLE_PUB_DETAIL),
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
