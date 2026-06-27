"""Tests for the Searlo discovery search client."""
import os
from unittest.mock import MagicMock, patch

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


def _mock_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


def test_search_researcher_accepts_documented_items_schema():
    """The API reference returns web results under `items`."""
    with patch.dict(os.environ, {"SEARLO_API_KEY": "test-key"}), \
         patch("backend.discovery.web_search.requests.get") as mock_get:
        mock_get.return_value = _mock_response({
            "items": [
                {
                    "title": "Jane Doe",
                    "link": "https://janedoe.com",
                    "snippet": "Economist personal website",
                }
            ]
        })

        from backend.discovery.web_search import search_researcher

        query, results = search_researcher("Jane", "Doe", "MIT")

    assert query == '"Jane Doe" MIT economist'
    assert results == [
        {
            "title": "Jane Doe",
            "url": "https://janedoe.com",
            "snippet": "Economist personal website",
        }
    ]


def test_search_researcher_accepts_organic_schema():
    """Some Searlo docs and examples use the `organic` key."""
    with patch.dict(os.environ, {"SEARLO_API_KEY": "test-key"}), \
         patch("backend.discovery.web_search.requests.get") as mock_get:
        mock_get.return_value = _mock_response({
            "organic": [
                {
                    "title": "John Smith",
                    "link": "https://johnsmith.github.io",
                    "snippet": "Academic website",
                }
            ]
        })

        from backend.discovery.web_search import search_researcher

        _, results = search_researcher("John", "Smith")

    assert results[0]["url"] == "https://johnsmith.github.io"
