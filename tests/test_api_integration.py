"""Integration tests — full request cycle across all endpoints and OpenAPI verification."""
from datetime import datetime
from unittest.mock import patch, MagicMock, call

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
# Task 5.1: OpenAPI docs
# ---------------------------------------------------------------------------

class TestOpenAPI:
    """Verify /docs renders and schema contains all endpoints."""

    def test_docs_endpoint_returns_200(self, client):
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_json_contains_all_paths(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        paths = schema["paths"]
        assert "/api/publications" in paths
        assert "/api/publications/{publication_id}" in paths
        assert "/api/researchers" in paths
        assert "/api/researchers/{researcher_id}" in paths
        assert "/api/fields" in paths
        assert "/api/scrape" in paths
        assert "/api/scrape/status" in paths

    def test_openapi_json_has_correct_methods(self, client):
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "get" in paths["/api/publications"]
        assert "get" in paths["/api/publications/{publication_id}"]
        assert "get" in paths["/api/researchers"]
        assert "get" in paths["/api/researchers/{researcher_id}"]
        assert "get" in paths["/api/fields"]
        assert "post" in paths["/api/scrape"]
        assert "get" in paths["/api/scrape/status"]


# ---------------------------------------------------------------------------
# Task 5.3: Full request cycle integration test
# ---------------------------------------------------------------------------

SAMPLE_PUB = (1, "Trade and Wages", "2024", "JLE", "https://example.com/p", datetime(2026, 3, 15, 14, 30), "published", None)
SAMPLE_AUTHORS = [(10, "Max Friedrich", "Steinhardt")]
SAMPLE_RESEARCHER = (10, "Max Friedrich", "Steinhardt", "Professor", "FU Berlin")
SAMPLE_URLS = [(1, "PUB", "https://example.com/pubs")]
SAMPLE_FIELDS: list = []
SAMPLE_SCRAPE = (1, "completed", datetime(2026, 3, 16, 10, 0), datetime(2026, 3, 16, 10, 5), 10, 2, 3)


class TestFullCycle:
    """Integration test exercising all endpoints in sequence."""

    def test_full_api_cycle(self, client):
        # 1. List publications
        with (
            patch("api.Database.fetch_one", return_value=(1,)),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
            resp = client.get("/api/publications")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

        # 2. Get single publication
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_PUB),
            patch("api.Database.fetch_all", return_value=SAMPLE_AUTHORS),
        ):
            resp = client.get("/api/publications/1")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Trade and Wages"

        # 3. List researchers
        with (
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.fetch_one", return_value=(5,)),
        ):
            mock_all.side_effect = [[SAMPLE_RESEARCHER], SAMPLE_URLS, SAMPLE_FIELDS]
            resp = client.get("/api/researchers")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

        # 4. Get single researcher
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_one.side_effect = [SAMPLE_RESEARCHER, (5,)]
            mock_all.side_effect = [SAMPLE_URLS, SAMPLE_FIELDS, [SAMPLE_PUB], SAMPLE_AUTHORS]
            resp = client.get("/api/researchers/10")
        assert resp.status_code == 200
        assert resp.json()["first_name"] == "Max Friedrich"

        # 5. Scrape status
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_SCRAPE),
            patch("scheduler.SCRAPE_INTERVAL_HOURS", 24),
        ):
            resp = client.get("/api/scrape/status")
        assert resp.status_code == 200
        assert resp.json()["last_scrape"]["status"] == "completed"

        # 6. Trigger scrape (auth required)
        resp = client.post("/api/scrape")
        assert resp.status_code == 401

        # 7. Trigger scrape (authenticated)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        with (
            patch("scheduler._scrape_lock", mock_lock),
            patch("api.create_scrape_log", return_value=1),
            patch("api.threading.Thread"),
        ):
            resp = client.post("/api/scrape", headers={"X-API-Key": "test-secret-key-for-ci-runs"})
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Task 5.4: Lifespan handler
# ---------------------------------------------------------------------------

class TestLifespan:
    """Verify lifespan handler calls create_tables and starts scheduler."""

    def test_lifespan_calls_create_tables_and_start_scheduler(self):
        from api import app

        with (
            patch("api.Database.create_tables") as mock_tables,
            patch("api.start_scheduler") as mock_start,
            patch("api.shutdown_scheduler") as mock_stop,
        ):
            with TestClient(app):
                mock_tables.assert_called_once()
                mock_start.assert_called_once()

            mock_stop.assert_called_once()
