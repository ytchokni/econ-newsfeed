"""Integration tests for snapshot append operations and feed event creation."""
from unittest.mock import MagicMock, patch

import pytest
from database.snapshots import (
    append_paper_snapshot,
    append_researcher_snapshot,
    _compute_paper_content_hash,
    _compute_researcher_content_hash,
    _is_status_progression,
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
    """append_paper_snapshot returns status change info (no longer creates feed events)."""

    def test_new_snapshot_inserts_and_updates(self):
        """First snapshot inserts into paper_snapshots and updates papers."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result.changed is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO paper_snapshots" in s for s in sqls)
        assert any("UPDATE papers" in s for s in sqls)

    def test_unchanged_content_returns_unchanged(self):
        """If content hash matches, no insert occurs."""
        h = _compute_paper_content_hash("accepted", "JLE", "abs", None, "2024")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": h, "status": "accepted"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result.changed is False
        sqls = _sql_statements(mock_cursor)
        assert not any("INSERT INTO paper_snapshots" in s for s in sqls)

    def test_status_change_returns_old_and_new_status(self):
        """When status changes, result contains old_status and new_status for caller to emit."""
        old_hash = _compute_paper_content_hash("working_paper", "JLE", "abs", None, "2024")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "working_paper"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result.changed is True
        assert result.status_changed is True
        assert result.old_status == "working_paper"
        assert result.new_status == "accepted"
        sqls = _sql_statements(mock_cursor)
        assert not any("INSERT INTO feed_events" in s for s in sqls)

    def test_content_change_without_status_change(self):
        """When content changes but status stays the same, status_changed is False."""
        old_hash = _compute_paper_content_hash("accepted", "AER", "old abs", None, "2023")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "accepted"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "new abs", None, "2024")

        assert result.changed is True
        assert result.status_changed is False
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO paper_snapshots" in s for s in sqls)
        assert not any("INSERT INTO feed_events" in s for s in sqls)

    def test_first_snapshot_no_status_change(self):
        """First-ever snapshot (no previous) has no old_status."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result.changed is True
        assert result.status_changed is False
        assert result.old_status is None

    def test_commits_transaction(self):
        """A successful insert must call conn.commit()."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "accepted", "JLE", "abs", None, "2024")

        assert result.changed is True
        mock_conn.commit.assert_called_once()

    def test_title_included_in_snapshot(self):
        """When title is provided, it is included in the INSERT."""
        mock_conn, mock_cursor = _make_mock_conn(prev_row=None)
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(
                1, "accepted", "JLE", "abs", None, "2024",
                title="My Paper Title",
            )

        assert result.changed is True
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

        assert result.changed is True
        sqls = _sql_statements(mock_cursor)
        assert any("INSERT INTO paper_snapshots" in s for s in sqls)


class TestStatusProgression:

    @pytest.mark.parametrize("old,new,expected", [
        ("working_paper", "accepted", True),
        ("working_paper", "published", True),
        ("revise_and_resubmit", "accepted", True),
        ("accepted", "published", True),
        ("published", "working_paper", False),
        ("accepted", "working_paper", False),
        ("published", "revise_and_resubmit", False),
        ("accepted", "accepted", False),
        (None, "working_paper", False),
        ("working_paper", None, False),
    ])
    def test_is_status_progression(self, old, new, expected):
        assert _is_status_progression(old, new) is expected

    def test_regression_suppresses_status_changed(self):
        """published → working_paper should not report status_changed."""
        old_hash = _compute_paper_content_hash("published", "AER", "abs", None, "2024")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "published"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "working_paper", "AER", "abs", None, "2024")

        assert result.changed is True
        assert result.status_changed is False

    def test_regression_preserves_old_status_in_papers_update(self):
        """When status regresses, the UPDATE papers should use the old status."""
        old_hash = _compute_paper_content_hash("published", "AER", "abs", None, "2024")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "published"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            append_paper_snapshot(1, "working_paper", "AER", "new abs", None, "2024")

        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE papers" in str(c)
        ]
        assert len(update_calls) == 1
        args = update_calls[0][0][1]
        assert args[0] == "published"

    def test_snapshot_stores_raw_llm_status(self):
        """The snapshot INSERT should contain the raw LLM status, even for regressions."""
        old_hash = _compute_paper_content_hash("published", "AER", "abs", None, "2024")
        mock_conn, mock_cursor = _make_mock_conn(
            prev_row={"content_hash": old_hash, "status": "published"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            append_paper_snapshot(1, "working_paper", "AER", "new abs", None, "2024")

        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO paper_snapshots" in str(c)
        ]
        assert len(insert_calls) == 1
        args = insert_calls[0][0][1]
        assert "working_paper" in args


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


def _make_two_read_mock_conn(snapshot_row, papers_row):
    """Mock conn whose cursor returns snapshot_row, then papers_row, from fetchone().

    snapshot_row : dict | None — previous paper_snapshots row (content_hash check)
    papers_row : dict | None — papers row read with FOR UPDATE (event baseline)
    """
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.side_effect = [snapshot_row, papers_row]
    return mock_conn, mock_cursor


def _update_papers_args(mock_cursor):
    """Return the params of the single UPDATE papers call."""
    calls = [
        c for c in mock_cursor.execute.call_args_list
        if "UPDATE papers" in str(c)
    ]
    assert len(calls) == 1
    return calls[0][0][1]


class TestEffectiveStatusBaseline:
    """old_status must come from papers.status (monotone), not the latest raw snapshot.

    Flapping scenario this prevents: paper is 'published' on page A but
    'working_paper' on page B. Snapshots alternate between the two raw values,
    so a snapshot-based baseline re-emits working_paper→published every cycle.
    papers.status never regresses, so an effective-status baseline emits nothing.
    """

    def test_flapping_does_not_reemit_forward_event(self):
        """Latest snapshot regressed to working_paper, papers.status is published:
        re-seeing published is NOT a status change."""
        old_hash = _compute_paper_content_hash("working_paper", "AER", "abs", None, "2024")
        mock_conn, _ = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": "published"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "published", "AER", "abs", None, "2024")

        assert result.changed is True
        assert result.status_changed is False
        assert result.old_status == "published"

    def test_progression_past_effective_status_emits(self):
        """A rank increase beyond papers.status still reports status_changed."""
        old_hash = _compute_paper_content_hash("accepted", "AER", "abs", None, "2024")
        mock_conn, _ = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": "accepted"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "published", "AER", "abs", None, "2024")

        assert result.status_changed is True
        assert result.old_status == "accepted"
        assert result.new_status == "published"

    def test_regression_below_effective_status_keeps_papers_status(self):
        """A raw status below papers.status: no event, papers keeps its status."""
        old_hash = _compute_paper_content_hash("published", "AER", "abs", None, "2024")
        mock_conn, mock_cursor = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": "published"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "working_paper", "AER", "new abs", None, "2024")

        assert result.status_changed is False
        assert _update_papers_args(mock_cursor)[0] == "published"

    def test_null_effective_status_takes_new_without_event(self):
        """papers.status NULL: adopt the new status, but emit nothing."""
        old_hash = _compute_paper_content_hash(None, "AER", "abs", None, "2024")
        mock_conn, mock_cursor = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": None},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "working_paper", "AER", "abs", None, "2024")

        assert result.status_changed is False
        assert _update_papers_args(mock_cursor)[0] == "working_paper"

    def test_papers_row_read_with_for_update(self):
        """The papers.status read must lock the row (FOR UPDATE) so concurrent
        extractions of co-author pages cannot both emit for the same rank."""
        mock_conn, mock_cursor = _make_two_read_mock_conn(None, {"status": "accepted"})
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            append_paper_snapshot(1, "published", "AER", "abs", None, "2024")

        sqls = _sql_statements(mock_cursor)
        assert any("FROM papers" in s and "FOR UPDATE" in s for s in sqls)
