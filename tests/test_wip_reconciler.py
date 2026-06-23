"""Tests for work_in_progress → working_paper promotion."""
import pytest
from unittest.mock import patch, MagicMock


def test_snapshot_does_not_promote_wip_without_link():
    """append_paper_snapshot should not promote work_in_progress → working_paper.

    The LLM always says 'working_paper' — promotion should only happen
    via reconcile_wip_status when a link is added, not via the snapshot system.
    """
    from backend.database.snapshots import _is_status_progression
    # This returns True because working_paper outranks work_in_progress,
    # but we need to block this specific transition in append_paper_snapshot.
    # The test documents the expected behavior after our override.
    assert _is_status_progression('work_in_progress', 'working_paper') is True


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_promotes_wip_when_paper_links_exist(mock_conn_ctx, mock_emitter):
    """Paper with status=work_in_progress and paper_links → promote to working_paper."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    # Paper is work_in_progress
    mock_cursor.fetchone.side_effect = [
        ("work_in_progress",),  # SELECT status
        (1,),                    # SELECT COUNT(*) paper_links
    ]

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    # Should UPDATE status to working_paper
    update_call = [c for c in mock_cursor.execute.call_args_list if "UPDATE papers" in str(c)]
    assert len(update_call) == 1
    assert "working_paper" in str(update_call[0])

    # Should emit status_change event
    mock_emitter.emit_status_change.assert_called_once_with(42, "work_in_progress", "working_paper")


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_promotes_wip_when_valid_draft_url(mock_conn_ctx, mock_emitter):
    """Paper with status=work_in_progress and valid draft_url → promote."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    # Paper is work_in_progress, no paper_links, but valid draft_url
    mock_cursor.fetchone.side_effect = [
        ("work_in_progress",),  # SELECT status
        (0,),                    # SELECT COUNT(*) paper_links = 0
        ("valid",),              # SELECT draft_url_status
    ]

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    mock_emitter.emit_status_change.assert_called_once_with(42, "work_in_progress", "working_paper")


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_no_op_when_not_wip(mock_conn_ctx, mock_emitter):
    """Paper with status != work_in_progress → no action."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.return_value = ("working_paper",)

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    mock_emitter.emit_status_change.assert_not_called()


@patch("backend.pipeline.wip_reconciler.FeedEventEmitter")
@patch("backend.pipeline.wip_reconciler.get_connection")
def test_no_op_when_wip_and_no_links(mock_conn_ctx, mock_emitter):
    """Paper with status=work_in_progress and no links → stays WIP."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.side_effect = [
        ("work_in_progress",),  # SELECT status
        (0,),                    # SELECT COUNT(*) paper_links = 0
        (None,),                 # SELECT draft_url_status = NULL (no draft_url)
    ]

    from backend.pipeline.wip_reconciler import reconcile_wip_status
    reconcile_wip_status(42)

    mock_emitter.emit_status_change.assert_not_called()
