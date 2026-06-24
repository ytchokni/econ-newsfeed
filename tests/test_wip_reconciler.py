"""Tests for work_in_progress → working_paper promotion."""
import pytest
from unittest.mock import patch, MagicMock


def _mock_conn():
    """Create a mock DB connection with cursor context manager support."""
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn_ctx = MagicMock()
    mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn_ctx.__exit__ = MagicMock(return_value=False)
    return mock_conn_ctx, mock_conn, mock_cursor


def test_snapshot_does_not_promote_wip_without_link():
    """append_paper_snapshot should not promote work_in_progress → working_paper.

    The LLM always says 'working_paper' — promotion should only happen
    via reconcile_wip_status when a link is added, not via the snapshot system.
    """
    from backend.database.snapshots import _is_status_progression
    assert _is_status_progression('work_in_progress', 'working_paper') is True


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_promotes_wip_when_paper_links_exist(mock_get_conn, mock_emitter):
    """Paper with status=work_in_progress and paper_links → promote to working_paper."""
    mock_conn_ctx, mock_conn, mock_cursor = _mock_conn()
    mock_get_conn.return_value = mock_conn_ctx

    # Single query returns (status, draft_url_status, has_link)
    mock_cursor.fetchone.return_value = ("work_in_progress", None, 1)

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    update_call = [c for c in mock_cursor.execute.call_args_list if "UPDATE papers" in str(c)]
    assert len(update_call) == 1
    assert "working_paper" in str(update_call[0])
    mock_emitter.emit_status_change.assert_called_once_with(42, "work_in_progress", "working_paper")


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_promotes_wip_when_valid_draft_url(mock_get_conn, mock_emitter):
    """Paper with status=work_in_progress and valid draft_url → promote."""
    mock_conn_ctx, mock_conn, mock_cursor = _mock_conn()
    mock_get_conn.return_value = mock_conn_ctx

    # No paper_links (has_link=0), but valid draft_url
    mock_cursor.fetchone.return_value = ("work_in_progress", "valid", 0)

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    mock_emitter.emit_status_change.assert_called_once_with(42, "work_in_progress", "working_paper")


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_no_op_when_not_wip(mock_get_conn, mock_emitter):
    """Paper with status != work_in_progress → no action."""
    mock_conn_ctx, mock_conn, mock_cursor = _mock_conn()
    mock_get_conn.return_value = mock_conn_ctx

    mock_cursor.fetchone.return_value = ("working_paper", None, 1)

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    mock_emitter.emit_status_change.assert_not_called()


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_no_op_when_wip_and_no_links(mock_get_conn, mock_emitter):
    """Paper with status=work_in_progress and no links → stays WIP."""
    mock_conn_ctx, mock_conn, mock_cursor = _mock_conn()
    mock_get_conn.return_value = mock_conn_ctx

    mock_cursor.fetchone.return_value = ("work_in_progress", None, 0)

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    mock_emitter.emit_status_change.assert_not_called()


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_reconcile_is_idempotent(mock_get_conn, mock_emitter):
    """Calling reconcile_wip_status twice should only emit one event."""
    mock_conn_ctx, mock_conn, mock_cursor = _mock_conn()
    mock_get_conn.return_value = mock_conn_ctx

    # First call: paper is work_in_progress with a link
    mock_cursor.fetchone.return_value = ("work_in_progress", None, 1)

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)
    assert mock_emitter.emit_status_change.call_count == 1

    # Second call: paper is now working_paper (already promoted)
    mock_cursor.fetchone.return_value = ("working_paper", None, 1)
    reconcile_wip_status(42)
    assert mock_emitter.emit_status_change.call_count == 1
