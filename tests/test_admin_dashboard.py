"""Tests for admin dashboard stats queries."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, MagicMock
from database.admin import get_admin_dashboard_stats


def test_get_admin_dashboard_stats_returns_all_sections():
    """Stats response contains all 6 dashboard sections."""
    mock_fetch_all = MagicMock(return_value=[])
    mock_fetch_one = MagicMock(return_value={
        "total_papers": 0,
        "total_researchers": 0,
        "total_urls": 0,
        "papers_with_abstract": 0,
        "papers_with_doi": 0,
        "papers_with_openalex": 0,
        "papers_with_draft_url": 0,
        "draft_url_valid": 0,
        "researchers_with_description": 0,
        "researchers_with_jel": 0,
        "researchers_with_openalex_id": 0,
        "total_cost_usd": 0,
        "total_tokens": 0,
        "total_scrapes": 0,
        "total_pubs_extracted": 0,
    })

    with patch("database.admin.fetch_all", mock_fetch_all), \
         patch("database.admin.fetch_one", mock_fetch_one):
        result = get_admin_dashboard_stats()

    assert "health" in result
    assert "content" in result
    assert "quality" in result
    assert "costs" in result
    assert "scrapes" in result
    assert "activity" in result

    # Health section
    assert "last_scrape" in result["health"]
    assert "scrape_in_progress" in result["health"]
    assert "total_researcher_urls" in result["health"]
    assert "urls_by_page_type" in result["health"]

    # Content section
    assert "total_papers" in result["content"]
    assert "total_researchers" in result["content"]
    assert "papers_by_status" in result["content"]
    assert "papers_by_year" in result["content"]
    assert "researchers_by_position" in result["content"]

    # Quality section
    assert "papers_with_abstract" in result["quality"]
    assert "papers_with_doi" in result["quality"]

    # Costs section
    assert "total_cost_usd" in result["costs"]
    assert "by_call_type" in result["costs"]
    assert "by_model" in result["costs"]
    assert "last_30_days" in result["costs"]

    # Scrapes section
    assert "recent" in result["scrapes"]
    assert "totals" in result["scrapes"]

    # Activity section
    assert "events_last_7d" in result["activity"]
    assert "events_last_30d" in result["activity"]
    assert "recent_events" in result["activity"]


def test_admin_dashboard_endpoint_requires_api_key(client):
    """GET /api/admin/dashboard returns 401 without API key."""
    resp = client.get("/api/admin/dashboard")
    assert resp.status_code == 401


def test_admin_dashboard_endpoint_returns_data(client):
    """GET /api/admin/dashboard returns stats with valid API key."""
    mock_stats = {
        "health": {"last_scrape": None, "next_scrape_at": None,
                   "scrape_in_progress": False, "total_researcher_urls": 0,
                   "urls_by_page_type": {}},
        "content": {"total_papers": 10, "total_researchers": 5,
                    "papers_by_status": {}, "papers_by_year": [],
                    "researchers_by_position": {}},
        "quality": {"papers_with_abstract": 0, "papers_with_doi": 0,
                    "papers_with_openalex": 0, "papers_with_draft_url": 0,
                    "draft_url_valid": 0, "researchers_with_description": 0,
                    "researchers_with_jel": 0, "researchers_with_openalex_id": 0},
        "costs": {"total_cost_usd": 0, "total_tokens": 0, "by_call_type": [],
                  "by_model": [], "batch_vs_realtime": {"batch_cost": 0, "realtime_cost": 0},
                  "last_30_days": []},
        "scrapes": {"recent": [], "totals": {"total_scrapes": 0, "total_pubs_extracted": 0}},
        "activity": {"events_last_7d": {}, "events_last_30d": {}, "recent_events": []},
    }
    with patch("api.Database.get_admin_dashboard_stats", return_value=mock_stats):
        resp = client.get(
            "/api/admin/dashboard",
            headers={"X-API-Key": "test-secret-key-for-ci-runs"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "content" in data
    assert data["content"]["total_papers"] == 10
