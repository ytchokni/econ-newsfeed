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


class TestTitleMatchingNormalization:
    """Suppression matching must survive HTML entities, \\uXXXX escapes, and inline tags.

    These are the three failure modes that produced bogus new_paper events in
    production (titles already on the page but missed by naive substring match).
    """

    def test_finds_title_despite_html_entity_ampersand(self):
        from feed_events import _title_in_previous_snapshot, _normalize_html_for_matching
        html = "<li>Media Literacy &amp; Perceived Media Bias in the US</li>"
        assert _title_in_previous_snapshot(
            "Media Literacy & Perceived Media Bias in the US",
            _normalize_html_for_matching(html),
        )

    def test_finds_title_despite_unicode_escapes(self):
        from feed_events import _title_in_previous_snapshot, _normalize_html_for_matching
        # Google Sites embeds content in JS with \uXXXX escapes
        html = '{"title":"Should Inequality Factor into Central Banks\\u2019 Decisions?"}'
        assert _title_in_previous_snapshot(
            "Should Inequality Factor into Central Banks' Decisions?",
            _normalize_html_for_matching(html),
        )

    def test_finds_title_split_across_inline_tags(self):
        from feed_events import _title_in_previous_snapshot, _normalize_html_for_matching
        html = "<p>Through a Glass, <em>Darkly</em>: Strategic Information</p>"
        assert _title_in_previous_snapshot(
            "Through a Glass, Darkly: Strategic Information",
            _normalize_html_for_matching(html),
        )

    def test_does_not_find_absent_title(self):
        from feed_events import _title_in_previous_snapshot, _normalize_html_for_matching
        html = "<p>Completely unrelated page content</p>"
        assert not _title_in_previous_snapshot(
            "Worldwide Environmental Engel Curves",
            _normalize_html_for_matching(html),
        )

    def test_none_html_returns_false(self):
        from feed_events import _title_in_previous_snapshot
        assert not _title_in_previous_snapshot("Any Title", None)


class TestPreviousSnapshotQuery:
    """html_snapshots archives the OLD state before each overwrite, so the
    most-recent snapshot already IS the previous page state. The lookup must
    not skip it with OFFSET 1."""

    @patch("feed_events.Database.get_connection")
    def test_uses_most_recent_snapshot_not_offset_1(self, mock_get_conn):
        import zlib
        from feed_events import _get_previous_snapshot_html
        cursor = MagicMock()
        cursor.fetchone.return_value = (zlib.compress(b"<p>Prev State</p>"),)
        _get_previous_snapshot_html(cursor, "http://example.com")
        sql = cursor.execute.call_args[0][0]
        assert "OFFSET 1" not in sql
