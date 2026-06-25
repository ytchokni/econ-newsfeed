"""Tests for discovery engine orchestration."""
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

from unittest.mock import patch, MagicMock
from backend.discovery.classifier import WebsiteClassification


def test_run_discovery_batch_no_candidates():
    """Batch with no candidates returns zeros."""
    with patch("backend.discovery.engine.get_discovery_candidates", return_value=[]):
        from backend.discovery.engine import run_discovery_batch
        result = run_discovery_batch(limit=10)
    assert result == {"searched": 0, "found": 0, "no_result": 0, "errors": 0}


def test_run_discovery_batch_found():
    """Batch finds a URL via search + classify + crawl."""
    candidates = [{"id": 1, "first_name": "Jane", "last_name": "Doe", "affiliation": "MIT"}]
    search_results = [
        {"title": "Jane Doe", "url": "https://janedoe.com", "snippet": "economist"},
    ]
    classification = WebsiteClassification(
        url="https://janedoe.com", confidence=0.95, reasoning="clear match"
    )
    subpages = [{"page_type": "research", "url": "https://janedoe.com/research"}]

    mock_insert = MagicMock()

    with patch("backend.discovery.engine.get_discovery_candidates", return_value=candidates), \
         patch("backend.discovery.engine.search_researcher", return_value=('"Jane Doe" MIT economist', search_results)), \
         patch("backend.discovery.engine.classify_search_results", return_value=classification), \
         patch("backend.discovery.engine.crawl_subpages", return_value=subpages), \
         patch("backend.discovery.engine.insert_discovery", mock_insert):
        from backend.discovery.engine import run_discovery_batch
        result = run_discovery_batch(limit=1)

    assert result["found"] == 1
    assert result["searched"] == 1
    mock_insert.assert_called_once_with(1, "https://janedoe.com", subpages, 0.95, '"Jane Doe" MIT economist')


def test_run_discovery_batch_no_result():
    """Batch handles no search results gracefully."""
    candidates = [{"id": 1, "first_name": "Jane", "last_name": "Doe", "affiliation": None}]
    mock_insert = MagicMock()

    with patch("backend.discovery.engine.get_discovery_candidates", return_value=candidates), \
         patch("backend.discovery.engine.search_researcher", return_value=("query", [])), \
         patch("backend.discovery.engine.insert_discovery", mock_insert):
        from backend.discovery.engine import run_discovery_batch
        result = run_discovery_batch(limit=1)

    assert result["no_result"] == 1
    mock_insert.assert_called_once_with(1, None, None, None, "query")


def test_subpage_crawler_finds_research():
    """Subpage crawler finds /research link from HTML."""
    html = '<html><body><nav><a href="/research">Research</a><a href="/cv">CV</a></nav></body></html>'

    with patch("backend.discovery.subpage_crawler.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_get.return_value = mock_resp

        from backend.discovery.subpage_crawler import crawl_subpages
        result = crawl_subpages("https://example.com")

    page_types = {sp["page_type"] for sp in result}
    assert "research" in page_types


def test_classifier_prompt_format():
    """Classifier builds a valid prompt and calls extract_json."""
    search_results = [
        {"title": "Test", "url": "https://test.com", "snippet": "test"},
    ]
    mock_response = MagicMock()
    mock_response.parsed = WebsiteClassification(
        url="https://test.com", confidence=0.9, reasoning="test"
    )

    with patch("backend.discovery.classifier.extract_json", return_value=mock_response) as mock_extract:
        from backend.discovery.classifier import classify_search_results
        result = classify_search_results("Jane", "Doe", "MIT", search_results)

    assert result is not None
    assert result.url == "https://test.com"
    mock_extract.assert_called_once()
    prompt_arg = mock_extract.call_args[0][0]
    assert "Jane Doe" in prompt_arg
    assert "MIT" in prompt_arg
