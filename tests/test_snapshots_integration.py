"""Integration tests for snapshot append operations and feed event creation."""
from unittest.mock import MagicMock, patch

from database.snapshots import (
    append_paper_snapshot,
    append_researcher_snapshot,
    _compute_paper_content_hash,
    _compute_researcher_content_hash,
)


def _make_mock_conn(prev_row=None):
    """Create a mock connection + cursor that behaves like the mysql pool.

    Parameters
    ----------
    prev_row : dict | None
        The row returned by ``cursor.fetchone()`` when looking up the
        previous snapshot.  ``None`` means "no previous snapshot exists".
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = prev_row

    mock_conn = MagicMock()
    # conn.cursor(dictionary=True) returns a context-manager yielding mock_cursor
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    # get_connection() returns a context-manager yielding mock_conn
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    return mock_conn, mock_cursor


def _sql_statements(mock_cursor):
    """Return a list of SQL strings passed to ``cursor.execute``."""
    return [str(c) for c in mock_cursor.execute.call_args_list]


# ---------------------------------------------------------------------------
# Paper snapshot tests
# ---------------------------------------------------------------------------

class TestAppendPaperSnapshotFeedEvents:
    """append_paper_snapshot must create feed_events on status change."""

    def test_new_snapshot_inserts_and_updates(self):
        """First snapshot inserts into paper_snapshots and updates papers."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO paper_snapshots" in s for s in sqls)
        assert any("UPDATE papers" in s for s in sqls)

    def test_unchanged_content_returns_false(self):
        """If content hash matches, no insert occurs."""
        h = _compute_paper_content_hash("accepted", "JLE", "abs", None, "2024")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": h, "status": "accepted"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result is False
        # Only the SELECT should have been executed — no INSERT/UPDATE
        sqls = _sql_statements(mock_cursor)
        assert not any("INSERT INTO paper_snapshots" in s for s in sqls)

    def test_status_change_creates_feed_event(self):
        """When status changes, a status_change feed_event is inserted."""
        old_hash = _compute_paper_content_hash("working_paper", "JLE", "abs", None, "2024")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "working_paper"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO feed_events" in s for s in sqls)

    def test_content_change_without_status_change_skips_feed_event(self):
        """When content changes but status stays the same, no feed_event."""
        old_hash = _compute_paper_content_hash("accepted", "AER", "old abs", None, "2023")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "accepted"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "new abs", None, "2024")

        assert result is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO paper_snapshots" in s for s in sqls)
        assert not any("INSERT INTO feed_events" in s for s in sqls)

    def test_first_snapshot_no_feed_event(self):
        """First-ever snapshot (no previous) should not create a feed_event."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result is True
        sqls = _sql_statements(mock_cursor)
        assert not any("INSERT INTO feed_events" in s for s in sqls)

    def test_commits_transaction(self):
        """A successful insert must call conn.commit()."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        mock_conn.commit.assert_called_once()

    def test_title_included_in_snapshot(self):
        """When title is provided, it is included in the INSERT."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(
                1, "accepted", "JLE", "abs", None, "2024",
                title="My Paper Title",
            )

        assert result is True
        sqls = _sql_statements(mock_cursor)
        insert_calls = [s for s in sqls if "INSERT INTO paper_snapshots" in s]
        assert len(insert_calls) == 1
        assert "title" in insert_calls[0]

    def test_title_change_triggers_new_snapshot(self):
        """A title-only change should produce a new snapshot (different hash)."""
        old_hash = _compute_paper_content_hash(
            "accepted", "JLE", "abs", None, "2024", title="Old Title"
        )
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "accepted"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(
                1, "accepted", "JLE", "abs", None, "2024",
                title="New Title",
            )

        assert result is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO paper_snapshots" in s for s in sqls)


# ---------------------------------------------------------------------------
# Researcher snapshot tests
# ---------------------------------------------------------------------------

class TestAppendResearcherSnapshotDenormalization:
    """append_researcher_snapshot must update the researchers table."""

    def test_new_snapshot_inserts_and_denormalizes(self):
        """First snapshot inserts into researcher_snapshots and updates researchers."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_researcher_snapshot(1, "Prof", "MIT", "Bio")

        assert result is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO researcher_snapshots" in s for s in sqls)
        assert any("UPDATE researchers" in s for s in sqls)

    def test_unchanged_content_returns_false(self):
        """If content hash matches, no insert occurs."""
        h = _compute_researcher_content_hash("Prof", "MIT", "Bio")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": h},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_researcher_snapshot(1, "Prof", "MIT", "Bio")

        assert result is False
        sqls = _sql_statements(mock_cursor)
        assert not any("INSERT INTO researcher_snapshots" in s for s in sqls)

    def test_changed_content_inserts_new_snapshot(self):
        """When content changes, a new snapshot is appended."""
        old_hash = _compute_researcher_content_hash("Assistant Prof", "MIT", "Old bio")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_researcher_snapshot(1, "Prof", "MIT", "New bio")

        assert result is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO researcher_snapshots" in s for s in sqls)
        assert any("UPDATE researchers" in s for s in sqls)

    def test_commits_transaction(self):
        """A successful insert must call conn.commit()."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            append_researcher_snapshot(1, "Prof", "MIT", "Bio")

        mock_conn.commit.assert_called_once()
