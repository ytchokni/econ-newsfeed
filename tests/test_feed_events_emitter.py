"""Tests for FeedEventEmitter — event creation logic separated from persistence."""
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass


@dataclass
class FakeSaveResult:
    paper_id: int
    title: str
    is_new: bool
    new_to_this_url: bool
    status: str | None


class TestEmitNewPaperEvents:
    """FeedEventEmitter.emit_new_paper_events creates new_paper events."""

    @patch("feed_events.Database.get_connection")
    def test_skips_seed_publications(self, mock_get_conn):
        from feed_events import FeedEventEmitter
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Paper", True, True, "working_paper")],
            url="http://example.com",
            is_seed=True,
        )
        assert result == 0
        mock_get_conn.assert_not_called()

    @patch("feed_events.Database.get_connection")
    def test_skips_published_status(self, mock_get_conn):
        from feed_events import FeedEventEmitter
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(3,), None]
        mock_conn.cursor.return_value = cursor
        mock_get_conn.return_value = mock_conn

        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Paper", True, True, "published")],
            url="http://example.com",
        )
        assert result == 0

    @patch("feed_events._get_previous_snapshot_html", return_value=None)
    @patch("feed_events.Database.get_connection")
    def test_creates_event_for_new_working_paper_with_baseline(self, mock_get_conn, _):
        from feed_events import FeedEventEmitter
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        cursor.fetchone.return_value = (3,)
        mock_conn.cursor.return_value = cursor
        mock_get_conn.return_value = mock_conn

        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "New Paper", True, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 1
        insert_calls = [c for c in cursor.execute.call_args_list if "INSERT INTO feed_events" in str(c)]
        assert len(insert_calls) == 1

    @patch("feed_events._get_previous_snapshot_html", return_value=None)
    @patch("feed_events.Database.get_connection")
    def test_suppresses_event_without_baseline(self, mock_get_conn, _):
        from feed_events import FeedEventEmitter
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = cursor
        mock_get_conn.return_value = mock_conn

        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Paper", True, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 0

    @patch("feed_events._get_previous_snapshot_html", return_value="<p>paper already here</p>")
    @patch("feed_events.Database.get_connection")
    def test_suppresses_event_when_title_in_previous_snapshot(self, mock_get_conn, _):
        from feed_events import FeedEventEmitter
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        cursor.fetchone.return_value = (3,)
        mock_conn.cursor.return_value = cursor
        mock_get_conn.return_value = mock_conn

        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Paper Already Here", True, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 0

    @patch("feed_events._get_previous_snapshot_html", return_value=None)
    @patch("feed_events.Database.get_connection")
    def test_duplicate_paper_new_to_url_no_prior_event(self, mock_get_conn, _):
        """Duplicate paper appearing on a new URL for the first time gets an event if no prior event exists."""
        from feed_events import FeedEventEmitter
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(3,), (0,)]
        mock_conn.cursor.return_value = cursor
        mock_get_conn.return_value = mock_conn

        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(10, "Existing Paper", False, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 1


class TestEmitStatusChange:
    @patch("feed_events.Database.get_connection")
    def test_creates_status_change_event(self, mock_get_conn):
        from feed_events import FeedEventEmitter
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        mock_conn.cursor.return_value = cursor
        mock_get_conn.return_value = mock_conn

        FeedEventEmitter.emit_status_change(1, "working_paper", "accepted")
        insert_calls = [c for c in cursor.execute.call_args_list if "status_change" in str(c)]
        assert len(insert_calls) == 1


class TestEmitTitleChange:
    @patch("feed_events.Database.get_connection")
    def test_creates_title_change_event(self, mock_get_conn):
        from feed_events import FeedEventEmitter
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        mock_conn.cursor.return_value = cursor
        mock_get_conn.return_value = mock_conn

        FeedEventEmitter.emit_title_change(1, "Old Title", "New Title")
        insert_calls = [c for c in cursor.execute.call_args_list if "title_change" in str(c)]
        assert len(insert_calls) == 1
