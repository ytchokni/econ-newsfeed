"""Tests that paper_saver passes status through without modification."""
import pytest
from unittest.mock import patch, MagicMock
from backend.pipeline.paper_saver import PaperSaver, _author_id_cache


@pytest.fixture(autouse=True)
def clear_author_cache():
    _author_id_cache.clear()
    yield
    _author_id_cache.clear()


def _mock_conn(lastrowid=1):
    """Create a mock DB connection."""
    cursor = MagicMock()
    cursor.lastrowid = lastrowid
    cursor.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cursor


@patch("backend.pipeline.paper_saver.get_researcher_id", return_value=1)
@patch("backend.pipeline.paper_saver.get_connection")
@patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
def test_working_paper_without_draft_url_stays_working_paper(mock_hash, mock_get_conn, mock_rid):
    """A working_paper without draft_url should NOT be downgraded — status is LLM-determined."""
    conn, cursor = _mock_conn(lastrowid=1)
    mock_get_conn.return_value = conn

    results = PaperSaver.save_publications("https://example.com", [{
        "title": "My Paper",
        "authors": [["John", "Doe"]],
        "status": "working_paper",
        "year": "2026",
        "venue": None,
        "abstract": None,
        "draft_url": None,
    }])

    insert_calls = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO papers" in str(c)]
    assert len(insert_calls) == 1
    insert_args = insert_calls[0][0][1]
    assert insert_args[7] == "working_paper"
    assert len(results) == 1
    assert results[0].status == "working_paper"


@patch("backend.pipeline.paper_saver.get_researcher_id", return_value=1)
@patch("backend.pipeline.paper_saver.get_connection")
@patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
def test_work_in_progress_passed_through(mock_hash, mock_get_conn, mock_rid):
    """A work_in_progress status from the LLM should be saved as-is."""
    conn, cursor = _mock_conn(lastrowid=1)
    mock_get_conn.return_value = conn

    results = PaperSaver.save_publications("https://example.com", [{
        "title": "My Early Stage Paper",
        "authors": [["Jane", "Doe"]],
        "status": "work_in_progress",
        "year": "2026",
        "venue": None,
        "abstract": None,
        "draft_url": None,
    }])

    insert_calls = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO papers" in str(c)]
    assert len(insert_calls) == 1
    insert_args = insert_calls[0][0][1]
    assert insert_args[7] == "work_in_progress"
    assert len(results) == 1
    assert results[0].status == "work_in_progress"


@patch("backend.pipeline.paper_saver.get_researcher_id", return_value=1)
@patch("backend.pipeline.paper_saver.get_connection")
@patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
def test_published_paper_not_affected(mock_hash, mock_get_conn, mock_rid):
    """A paper with status=published should be saved as-is."""
    conn, cursor = _mock_conn(lastrowid=1)
    mock_get_conn.return_value = conn

    results = PaperSaver.save_publications("https://example.com", [{
        "title": "My Paper",
        "authors": [["John", "Doe"]],
        "status": "published",
        "year": "2026",
        "venue": "AER",
        "abstract": None,
        "draft_url": None,
    }])

    insert_calls = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO papers" in str(c)]
    assert len(insert_calls) == 1
    insert_args = insert_calls[0][0][1]
    assert insert_args[7] == "published"
    assert len(results) == 1
    assert results[0].status == "published"
