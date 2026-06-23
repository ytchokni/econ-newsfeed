"""Tests for work_in_progress downgrade in paper_saver."""
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
def test_new_working_paper_without_draft_url_becomes_wip(mock_hash, mock_get_conn, mock_rid):
    """A new paper with status=working_paper and no draft_url should be saved as work_in_progress."""
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

    # The INSERT should use 'work_in_progress' instead of 'working_paper'
    insert_calls = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO papers" in str(c)]
    assert len(insert_calls) == 1, f"Expected 1 INSERT INTO papers, got {len(insert_calls)}"
    insert_args = insert_calls[0][0][1]  # positional args tuple
    # status is the 8th value (index 7) in the INSERT VALUES
    assert insert_args[7] == "work_in_progress", (
        f"Expected 'work_in_progress' but got '{insert_args[7]}' — "
        "new working_paper with no draft_url should be downgraded"
    )

    # SaveResult should also reflect the effective status
    assert len(results) == 1
    assert results[0].status == "work_in_progress"


@patch("backend.pipeline.paper_saver.get_researcher_id", return_value=1)
@patch("backend.pipeline.paper_saver.get_connection")
@patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
def test_new_working_paper_with_draft_url_stays_working_paper(mock_hash, mock_get_conn, mock_rid):
    """A new paper with status=working_paper and a draft_url should stay working_paper."""
    conn, cursor = _mock_conn(lastrowid=1)
    mock_get_conn.return_value = conn

    results = PaperSaver.save_publications("https://example.com", [{
        "title": "My Paper",
        "authors": [["John", "Doe"]],
        "status": "working_paper",
        "year": "2026",
        "venue": None,
        "abstract": None,
        "draft_url": "https://ssrn.com/abstract=123",
    }])

    insert_calls = [c for c in cursor.execute.call_args_list if "INSERT IGNORE INTO papers" in str(c)]
    assert len(insert_calls) == 1
    insert_args = insert_calls[0][0][1]
    assert insert_args[7] == "working_paper", (
        f"Expected 'working_paper' but got '{insert_args[7]}' — "
        "working_paper with a draft_url should NOT be downgraded"
    )

    assert len(results) == 1
    assert results[0].status == "working_paper"


@patch("backend.pipeline.paper_saver.get_researcher_id", return_value=1)
@patch("backend.pipeline.paper_saver.get_connection")
@patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
def test_published_paper_not_affected(mock_hash, mock_get_conn, mock_rid):
    """A new paper with status=published should not be touched."""
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
    assert insert_args[7] == "published", (
        f"Expected 'published' but got '{insert_args[7]}' — "
        "published papers should not be downgraded"
    )

    assert len(results) == 1
    assert results[0].status == "published"


@patch("backend.pipeline.paper_saver.get_researcher_id", return_value=1)
@patch("backend.pipeline.paper_saver.get_connection")
@patch("backend.pipeline.paper_saver.compute_title_hash", return_value="abc123")
def test_duplicate_working_paper_without_draft_url_status_not_modified(mock_hash, mock_get_conn, mock_rid):
    """Existing paper (dedup path, lastrowid=0) status is not touched."""
    conn, cursor = _mock_conn(lastrowid=0)
    mock_get_conn.return_value = conn
    cursor.fetchone.side_effect = [
        (10,),           # SELECT id FROM papers WHERE title_hash
        (None, None, None),  # SELECT abstract, year, venue
        None,            # page owner lookup
    ]
    cursor.rowcount = 1

    results = PaperSaver.save_publications("https://example.com", [{
        "title": "My Paper",
        "authors": [["John", "Doe"]],
        "status": "working_paper",
        "year": "2026",
        "venue": None,
        "abstract": None,
        "draft_url": None,
    }])

    # For the duplicate path, no INSERT INTO papers happens (INSERT IGNORE with lastrowid=0)
    # The result status should be the original pub.get('status') for the dedup case
    assert len(results) == 1
    assert results[0].is_new is False
