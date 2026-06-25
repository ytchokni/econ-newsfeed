"""Tests for admin dashboard stats queries."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("LLM_MODEL", "gemini-2.5-flash")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, MagicMock
from backend.database.admin import get_admin_dashboard_stats


def test_get_admin_dashboard_stats_returns_all_sections():
    """Stats response contains all 7 dashboard sections."""
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
        "active_count": 0,
        "deactivated_count": 0,
        "at_risk_count": 0,
        # extraction section keys
        "never_extracted": 0,
        "changed_pending": 0,
        "last_hour": 0,
        "last_24h": 0,
        "last_7d": 0,
        "last_call_at": None,
        "tokens_last_24h": 0,
        "last_extracted_at": None,
        # discovery section keys
        "total_pending": 0,
        "total_approved": 0,
        "total_rejected": 0,
    })

    mock_discovery_stats = {
        "total_pending": 0,
        "total_approved": 0,
        "total_rejected": 0,
        "recent": [],
    }

    with patch("backend.database.admin.fetch_all", mock_fetch_all), \
         patch("backend.database.admin.fetch_one", mock_fetch_one), \
         patch("backend.database.admin.get_discovery_stats", return_value=mock_discovery_stats):
        result = get_admin_dashboard_stats()

    assert "health" in result
    assert "content" in result
    assert "quality" in result
    assert "costs" in result
    assert "scrapes" in result
    assert "activity" in result
    assert "discovery" in result

    # Health section
    assert "last_scrape" in result["health"]
    assert "scrape_in_progress" in result["health"]
    assert "total_researcher_urls" in result["health"]
    assert "urls_by_page_type" in result["health"]
    assert "deactivated_urls" in result["health"]
    assert "at_risk_urls" in result["health"]

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

    # Extraction section
    assert "extraction" in result
    assert "worker_enabled" in result["extraction"]
    assert "queue" in result["extraction"]
    assert "throughput" in result["extraction"]
    assert "eta_days" in result["extraction"]
    assert "daily" in result["extraction"]
    assert "recent_calls" in result["extraction"]

    # Discovery section
    assert "discovery" in result
    assert "total_pending" in result["discovery"]
    assert "total_approved" in result["discovery"]
    assert "total_rejected" in result["discovery"]


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
        "discovery": {"total_pending": 0, "total_approved": 0, "total_rejected": 0},
    }
    with patch("backend.api.get_admin_dashboard_stats", return_value=mock_stats):
        resp = client.get(
            "/api/admin/dashboard",
            headers={"X-API-Key": "test-secret-key-for-ci-runs"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "content" in data
    assert data["content"]["total_papers"] == 10


def test_admin_dashboard_caches_result(client):
    """Repeated dashboard requests should hit cache, not re-query."""
    from backend.api import _admin_dashboard_cache
    _admin_dashboard_cache.clear()
    mock_stats = {
        "health": {}, "content": {}, "quality": {},
        "costs": {}, "scrapes": {}, "activity": {},
        "extraction": {}, "discovery": {},
    }
    with patch("backend.api.get_admin_dashboard_stats", return_value=mock_stats) as mock_fn:
        headers = {"X-API-Key": os.environ.get("SCRAPE_API_KEY", "test-key")}
        client.get("/api/admin/dashboard", headers=headers)
        client.get("/api/admin/dashboard", headers=headers)
    assert mock_fn.call_count == 1
    _admin_dashboard_cache.clear()


def _extraction_fetch_one(queue=None, completions=None, attempts=None, last_extracted=None):
    """Build a fetch_one side_effect for _get_extraction_stats's four queries, in call order."""
    rows = [
        queue or {"never_extracted": 0, "changed_pending": 0},
        completions or {"last_hour": 0, "last_24h": 0, "last_7d": 0},
        attempts or {"last_hour": 0, "last_24h": 0, "last_7d": 0,
                     "last_call_at": None, "tokens_last_24h": 0},
        last_extracted or {"last_extracted_at": None},
    ]
    return MagicMock(side_effect=rows)


def test_extraction_stats_queue_split_and_eta():
    """Queue split sums to total; ETA = total / (last_hour rate × 24), 1 decimal."""
    from backend.database.admin import _get_extraction_stats
    mock_one = _extraction_fetch_one(
        queue={"never_extracted": 6000, "changed_pending": 4000},
        completions={"last_hour": 40, "last_24h": 1000, "last_7d": 5000},
    )
    with patch("backend.database.admin.fetch_one", mock_one), \
         patch("backend.database.admin.fetch_all", MagicMock(return_value=[])):
        stats = _get_extraction_stats()
    assert stats["queue"] == {"never_extracted": 6000, "changed_pending": 4000, "total": 10000}
    # 40/hour → 960/day → 10000/960 = 10.4 days
    assert stats["eta_days"] == 10.4
    assert stats["throughput"]["completions"]["last_24h"] == 1000


def test_extraction_stats_eta_null_when_no_completions():
    """ETA is None when nothing completed in the last hour (avoid div-by-zero)."""
    from backend.database.admin import _get_extraction_stats
    mock_one = _extraction_fetch_one(queue={"never_extracted": 5, "changed_pending": 0})
    with patch("backend.database.admin.fetch_one", mock_one), \
         patch("backend.database.admin.fetch_all", MagicMock(return_value=[])):
        stats = _get_extraction_stats()
    assert stats["eta_days"] is None


def test_extraction_stats_recent_calls_and_daily_shapes():
    """recent_calls and daily map DB rows into the documented shape."""
    from datetime import datetime, timezone, date
    from backend.database.admin import _get_extraction_stats
    mock_one = _extraction_fetch_one()
    called = datetime(2026, 6, 10, 8, 0, 0, tzinfo=timezone.utc)
    mock_all = MagicMock(side_effect=[
        [{"date": date(2026, 6, 10), "count": 950}],                     # daily
        [{"called_at": called, "context_url": "https://x.com/pubs",
          "model": "gemma-4-31b-it", "total_tokens": 4102}],             # recent_calls
    ])
    with patch("backend.database.admin.fetch_one", mock_one), \
         patch("backend.database.admin.fetch_all", mock_all):
        stats = _get_extraction_stats()
    assert stats["daily"] == [{"date": "2026-06-10", "count": 950}]
    assert stats["recent_calls"] == [{
        "called_at": "2026-06-10T08:00:00Z", "context_url": "https://x.com/pubs",
        "model": "gemma-4-31b-it", "total_tokens": 4102,
    }]
