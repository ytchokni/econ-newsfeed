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
# Task 4.1: Health check
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# Task 4.3: Metrics endpoint
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_metrics_returns_counts(self, client):
        with patch("api.Database.fetch_one", return_value={
            "publications": 42, "researchers": 10, "scrapes": 5,
        }):
            response = client.get("/api/metrics")
        assert response.status_code == 200
        body = response.json()
        assert body["publications"] == 42
        assert body["researchers"] == 10
        assert body["scrapes"] == 5


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
# Task 4.2: OpenAPI response models
# ---------------------------------------------------------------------------

class TestOpenAPIResponseModels:
    def test_openapi_schema_has_response_models(self, client):
        schema = client.get("/openapi.json").json()
        schema_str = str(schema.get("components", {}).get("schemas", {}))
        assert "PublicationResponse" in schema_str
        assert "ResearcherResponse" in schema_str


# ---------------------------------------------------------------------------
# Task 5.3: Full request cycle integration test
# ---------------------------------------------------------------------------

# Feed events row shape: fe.id, fe.event_type, fe.old_status, fe.new_status, fe.created_at,
#   p.id, p.title, p.year, p.venue, p.url, p.timestamp, p.status, p.draft_url, p.abstract, p.draft_url_status
SAMPLE_PUB = {
    "event_id": 100, "event_type": "new_paper", "old_status": None, "new_status": "working_paper",
    "created_at": datetime(2026, 3, 15, 14, 30),
    "paper_id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/p", "discovered_at": datetime(2026, 3, 15, 14, 30),
    "status": "working_paper", "draft_url": None, "abstract": None, "draft_url_status": None,
}
SAMPLE_AUTHORS = [{"publication_id": 1, "researcher_id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"}]
SAMPLE_AUTHORS_SINGLE = [{"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"}]
# Single publication detail (10-column papers row, used by GET /api/publications/{id})
SAMPLE_PUB_DETAIL = {
    "id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/p", "discovered_at": datetime(2026, 3, 15, 14, 30),
    "status": "working_paper", "draft_url": None, "abstract": None, "draft_url_status": None,
}
SAMPLE_RESEARCHER = {"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt", "position": "Professor", "affiliation": "FU Berlin", "description": None}
SAMPLE_URLS_BATCH = [{"researcher_id": 10, "id": 1, "page_type": "PUB", "url": "https://example.com/pubs"}]
SAMPLE_URLS_SINGLE = [{"id": 1, "page_type": "PUB", "url": "https://example.com/pubs"}]
SAMPLE_FIELDS: list = []
SAMPLE_SCRAPE = {"id": 1, "status": "completed", "started_at": datetime(2026, 3, 16, 10, 0), "finished_at": datetime(2026, 3, 16, 10, 5), "urls_checked": 10, "urls_changed": 2, "pubs_extracted": 3}


class TestFullCycle:
    """Integration test exercising all endpoints in sequence."""

    def test_full_api_cycle(self, client):
        # 1. List publications
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[SAMPLE_PUB], SAMPLE_AUTHORS]
            resp = client.get("/api/publications")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

        # 2. Get single publication (uses old 10-column papers row shape)
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_PUB_DETAIL),
            patch("api.Database.fetch_all", return_value=SAMPLE_AUTHORS_SINGLE),
        ):
            resp = client.get("/api/publications/1")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Trade and Wages"

        # 3. List researchers
        with (
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.fetch_one", return_value={"total": 5}),
            patch("api.Database.get_jel_codes_for_researchers", return_value={}),
        ):
            mock_all.side_effect = [[SAMPLE_RESEARCHER], SAMPLE_URLS_BATCH, [{"researcher_id": 10, "cnt": 5}], SAMPLE_FIELDS]
            resp = client.get("/api/researchers")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

        # 4. Get single researcher
        with (
            patch("api.Database.fetch_one") as mock_one,
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.get_jel_codes_for_researcher", return_value=[]),
        ):
            mock_one.side_effect = [SAMPLE_RESEARCHER, {"cnt": 5}]
            mock_all.side_effect = [SAMPLE_URLS_SINGLE, SAMPLE_FIELDS, [SAMPLE_PUB_DETAIL], SAMPLE_AUTHORS]
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
        with (
            patch("api.scheduler.is_scrape_running", return_value=False),
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


# ---------------------------------------------------------------------------
# Smoke tests — would have caught "page stuck on loading"
# ---------------------------------------------------------------------------

# Sample data for smoke tests (publication batch format)
_SMOKE_PUB = {
    "event_id": 100, "event_type": "new_paper", "old_status": None, "new_status": "working_paper",
    "created_at": datetime(2026, 3, 15, 14, 30),
    "paper_id": 1, "title": "Trade and Wages", "year": "2024", "venue": "JLE",
    "source_url": "https://example.com/p", "discovered_at": datetime(2026, 3, 15, 14, 30),
    "status": "working_paper", "draft_url": None, "abstract": None, "draft_url_status": None,
}
_SMOKE_BATCH_AUTHORS = [{"publication_id": 1, "researcher_id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt"}]

# Sample data for researchers (batch format)
_SMOKE_RESEARCHER = {"id": 10, "first_name": "Max Friedrich", "last_name": "Steinhardt", "position": "Professor", "affiliation": "FU Berlin", "description": "Economist."}
_SMOKE_BATCH_URLS = [{"researcher_id": 10, "id": 1, "page_type": "homepage", "url": "https://example.com"}]
_SMOKE_BATCH_PUB_COUNTS = [{"researcher_id": 10, "cnt": 5}]
_SMOKE_BATCH_FIELDS = [{"researcher_id": 10, "id": 1, "name": "Labour Economics", "slug": "labour-economics"}]


class TestPublicationsSmoke:
    """High-level smoke tests that verify endpoints actually return data.

    These catch startup/configuration failures that leave the page stuck
    on "Loading..." — the full middleware stack runs, only the DB is mocked.
    """

    @pytest.fixture
    def client(self):
        with (
            patch("database.Database.create_tables"),
            patch("scheduler.start_scheduler"),
            patch("scheduler.shutdown_scheduler"),
        ):
            from api import app

            with TestClient(app) as c:
                yield c

    @pytest.mark.timeout(5)
    def test_publications_endpoint_responds_with_data(self, client):
        """GET /api/publications must return 200 with the expected shape — not hang."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_all,
        ):
            mock_all.side_effect = [[_SMOKE_PUB], _SMOKE_BATCH_AUTHORS]
            resp = client.get("/api/publications")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["items"], list)
        assert len(body["items"]) > 0
        assert isinstance(body["total"], int)
        assert isinstance(body["page"], int)
        assert isinstance(body["pages"], int)
        # Verify the publication has the expected fields
        pub = body["items"][0]
        assert "id" in pub
        assert "title" in pub
        assert "authors" in pub
        assert "discovered_at" in pub

    @pytest.mark.timeout(5)
    def test_researchers_endpoint_responds_with_data(self, client):
        """GET /api/researchers must return 200 with the expected shape — not hang."""
        with (
            patch("api.Database.fetch_one", return_value={"total": 1}),
            patch("api.Database.fetch_all") as mock_all,
            patch("api.Database.get_jel_codes_for_researchers", return_value={}),
        ):
            mock_all.side_effect = [
                [_SMOKE_RESEARCHER],
                _SMOKE_BATCH_URLS,
                _SMOKE_BATCH_PUB_COUNTS,
                _SMOKE_BATCH_FIELDS,
            ]
            resp = client.get("/api/researchers")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["items"], list)
        assert len(body["items"]) > 0
        assert isinstance(body["total"], int)
        assert isinstance(body["page"], int)
        assert isinstance(body["pages"], int)
        # Verify the researcher has the expected fields
        researcher = body["items"][0]
        assert "id" in researcher
        assert "first_name" in researcher
        assert "last_name" in researcher
        assert "publication_count" in researcher

    @pytest.mark.timeout(5)
    def test_app_starts_without_crashing(self, client):
        """The app lifespan must complete — catches env var and DB migration failures."""
        # If we got here, the TestClient started the app successfully.
        # Verify the healthiest endpoint responds.
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
