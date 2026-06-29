"""Tests for URL discovery DB operations and API endpoints."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("LLM_MODEL", "gemma-4-31b-it")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")
os.environ.setdefault("NEXTAUTH_SECRET", "test-nextauth-secret-for-ci")

import json
from unittest.mock import patch, MagicMock

from backend.database.discoveries import (
    insert_discovery,
    get_pending_discoveries,
    approve_discovery,
    reject_discovery,
    bulk_approve_discoveries,
    get_discovery_stats,
    get_recent_discoveries,
)


def test_insert_discovery_with_url():
    """insert_discovery stores a pending result with URL and subpages."""
    mock_execute = MagicMock()
    with patch("backend.database.discoveries.execute_query", mock_execute):
        insert_discovery(
            researcher_id=42,
            url="https://example.com",
            subpages=[{"page_type": "research", "url": "https://example.com/research"}],
            confidence=0.9,
            search_query='"John Doe" economist',
        )
    mock_execute.assert_called_once()
    args = mock_execute.call_args
    assert args[0][1][0] == 42  # researcher_id
    assert args[0][1][1] == "https://example.com"  # url
    assert "research" in args[0][1][2]  # subpages JSON
    assert args[0][1][4] == '"John Doe" economist'  # search_query
    assert args[0][1][5] == "pending"  # status


def test_insert_discovery_no_result():
    """insert_discovery with url=None sets status to no_result."""
    mock_execute = MagicMock()
    with patch("backend.database.discoveries.execute_query", mock_execute):
        insert_discovery(
            researcher_id=42,
            url=None,
            subpages=None,
            confidence=None,
            search_query='"John Doe" economist',
        )
    args = mock_execute.call_args
    assert args[0][1][1] is None  # url
    assert args[0][1][5] == "no_result"  # status


def test_get_pending_discoveries_parses_subpages_json():
    """Admin discovery responses expose subpages as arrays, not raw JSON strings."""
    mock_rows = [{
        "id": 1,
        "researcher_id": 42,
        "url": "https://example.com",
        "subpages": json.dumps([
            {"page_type": "research", "url": "https://example.com/research"},
        ]),
        "confidence": 0.9,
        "search_query": '"John Doe" economist',
        "searched_at": "2026-06-28T00:00:00",
        "first_name": "John",
        "last_name": "Doe",
        "affiliation": "Example University",
    }]

    with patch("backend.database.discoveries.fetch_all", return_value=mock_rows):
        rows = get_pending_discoveries()

    assert rows[0]["subpages"] == [
        {"page_type": "research", "url": "https://example.com/research"},
    ]


def test_get_recent_discoveries_parses_subpages_json():
    """Reviewed discovery history also returns subpages in frontend-safe form."""
    mock_rows = [{
        "id": 1,
        "researcher_id": 42,
        "url": "https://example.com",
        "subpages": json.dumps([
            {"page_type": "cv", "url": "https://example.com/cv.pdf"},
        ]),
        "confidence": 0.8,
        "status": "approved",
        "searched_at": "2026-06-28T00:00:00",
        "reviewed_at": "2026-06-28T01:00:00",
        "first_name": "John",
        "last_name": "Doe",
        "affiliation": "Example University",
    }]

    with patch("backend.database.discoveries.fetch_all", return_value=mock_rows):
        rows = get_recent_discoveries()

    assert rows[0]["subpages"] == [
        {"page_type": "cv", "url": "https://example.com/cv.pdf"},
    ]


def test_approve_discovery_copies_urls():
    """approve_discovery inserts root + subpages into researcher_urls."""
    mock_row = {
        "researcher_id": 42,
        "url": "https://example.com",
        "subpages": json.dumps([
            {"page_type": "research", "url": "https://example.com/research"},
        ]),
    }
    mock_add_url = MagicMock()
    mock_execute = MagicMock()

    with patch("backend.database.discoveries.fetch_one", return_value=mock_row), \
         patch("backend.database.discoveries.add_researcher_url", mock_add_url), \
         patch("backend.database.discoveries.execute_query", mock_execute):
        approve_discovery(1)

    assert mock_add_url.call_count == 2
    mock_add_url.assert_any_call(42, "personal", "https://example.com")
    mock_add_url.assert_any_call(42, "research", "https://example.com/research")


def test_reject_discovery():
    """reject_discovery sets status to rejected."""
    mock_execute = MagicMock()
    with patch("backend.database.discoveries.execute_query", mock_execute):
        reject_discovery(1)
    assert "rejected" in str(mock_execute.call_args)


def test_get_discovery_stats():
    """get_discovery_stats returns all expected keys."""
    mock_row = {
        "total_searched": 100,
        "pending_review": 20,
        "approved": 50,
        "rejected": 10,
        "no_result": 20,
    }
    mock_pool = {"cnt": 5000}
    call_count = [0]

    def mock_fetch_one(query, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_row
        return mock_pool

    with patch("backend.database.discoveries.fetch_one", side_effect=mock_fetch_one):
        stats = get_discovery_stats()

    assert stats["total_searched"] == 100
    assert stats["pool_remaining"] == 5000
