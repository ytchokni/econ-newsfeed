"""Integration tests for feed event guard logic — catches bugs like PR #145, #158, #153.

PR #145: Status regressions generated bogus status_change events.
PR #158: Bogus new_paper events — 6/24 were false positives (title on page for weeks).
PR #153: Feed events for backward status transitions.

Tests verify all guards independently AND in combination, with realistic data shapes.
"""
import zlib
import pytest
from dataclasses import dataclass
from unittest.mock import patch, MagicMock

from backend.pipeline.feed_events import (
    FeedEventEmitter,
    _title_in_previous_snapshot,
    _get_previous_snapshot_html,
    _url_has_baseline,
)


@dataclass
class FakeSaveResult:
    paper_id: int
    title: str
    is_new: bool
    new_to_this_url: bool
    status: str | None


def _make_conn_and_cursor(snapshot_count=3, prev_html=None, prior_event_count=0):
    """Build mock conn/cursor that simulates the guard queries in order.

    Query order in emit_new_paper_events:
    1. _url_has_baseline → SELECT COUNT(*) FROM html_snapshots
    2. _get_previous_snapshot_html → SELECT raw_html_compressed FROM html_snapshots
    Then per-result:
    3. (if is_new) INSERT INTO feed_events
    4. (if not is_new) SELECT COUNT(*) FROM feed_events WHERE paper_id=... → prior_event_count
    """
    mock_cursor = MagicMock()

    compressed = zlib.compress(prev_html.encode("utf-8")) if prev_html else None
    fetchone_returns = [
        (snapshot_count,),
        (compressed,) if compressed else None,
    ]
    if prior_event_count is not None:
        fetchone_returns.append((prior_event_count,))

    mock_cursor.fetchone.side_effect = fetchone_returns
    mock_cursor.execute = MagicMock()

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    return mock_conn, mock_cursor


class TestUrlHasBaseline:
    """_url_has_baseline guards against first-ever extractions."""

    def test_zero_snapshots_returns_false(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        assert _url_has_baseline(cursor, "http://example.com") is False

    def test_one_snapshot_returns_false(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        assert _url_has_baseline(cursor, "http://example.com") is False

    def test_two_snapshots_returns_true(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (2,)
        assert _url_has_baseline(cursor, "http://example.com") is True

    def test_many_snapshots_returns_true(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (100,)
        assert _url_has_baseline(cursor, "http://example.com") is True

    def test_custom_min_snapshots(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (4,)
        assert _url_has_baseline(cursor, "http://example.com", min_snapshots=5) is False
        assert _url_has_baseline(cursor, "http://example.com", min_snapshots=4) is True

    def test_null_row_returns_false(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        assert _url_has_baseline(cursor, "http://example.com") is False


class TestTitleInPreviousSnapshotEdgeCases:
    """Title matching edge cases that caused bogus events (PR #158)."""

    def test_title_with_html_entities_in_snapshot(self):
        """PR #158: titles hidden behind HTML entities must match after the
        snapshot is normalized (entity decode + tag strip + collapse)."""
        from backend.pipeline.feed_events import _normalize_html_for_matching
        html = "<p>Trade &amp; Wages: Evidence from Germany</p>"
        normalized = _normalize_html_for_matching(html)
        result = _title_in_previous_snapshot("Trade & Wages: Evidence from Germany", normalized)
        assert result is True

    def test_title_split_across_inline_tags(self):
        """PR #158: titles split by <em>/<b> must match after normalization."""
        from backend.pipeline.feed_events import _normalize_html_for_matching
        html = "<p>Trade and <em>Wages</em>: Evidence from <b>Germany</b></p>"
        normalized = _normalize_html_for_matching(html)
        result = _title_in_previous_snapshot("Trade and Wages: Evidence from Germany", normalized)
        assert result is True

    def test_title_behind_unicode_escapes(self):
        """PR #158: Google Sites embeds content as \\uXXXX JSON escapes."""
        from backend.pipeline.feed_events import _normalize_html_for_matching
        html = r'{"content":"Trade & Wages: Evidence from Germany"}'
        normalized = _normalize_html_for_matching(html)
        result = _title_in_previous_snapshot("Trade & Wages: Evidence from Germany", normalized)
        assert result is True

    def test_title_substring_match(self):
        html = "<p>the effect of monetary policy on wages and employment</p>"
        result = _title_in_previous_snapshot(
            "The Effect of Monetary Policy on Wages and Employment", html
        )
        assert result is True

    def test_title_not_present(self):
        html = "<p>some completely different paper about trade</p>"
        result = _title_in_previous_snapshot("Monetary Policy Shocks", html)
        assert result is False

    def test_empty_title_matches_anything(self):
        """Empty string is a substring of everything — callers must never pass
        empty titles (validate_publication drops them upstream)."""
        html = "<p>anything</p>"
        result = _title_in_previous_snapshot("", html)
        assert result is True

    def test_none_html_returns_false(self):
        result = _title_in_previous_snapshot("Any Title", None)
        assert result is False

    def test_title_in_json_embedded_html(self):
        html = '<script>{"content":"monetary policy in the eurozone"}</script>'
        result = _title_in_previous_snapshot("Monetary Policy in the Eurozone", html.lower())
        assert result is True


class TestGetPreviousSnapshotHtml:
    """Snapshot retrieval and decompression edge cases."""

    def _make_cursor(self, row):
        cursor = MagicMock()
        cursor.fetchone.return_value = row
        return cursor

    def test_decompresses_and_normalizes(self):
        """PR #158: returns normalized text (tags stripped, lowercased, collapsed)."""
        html = "<H1>My Paper Title</H1>"
        compressed = zlib.compress(html.encode("utf-8"))
        cursor = self._make_cursor((compressed,))
        result = _get_previous_snapshot_html(cursor, "http://example.com")
        assert result == "my paper title"

    def test_none_row_returns_none(self):
        cursor = self._make_cursor(None)
        assert _get_previous_snapshot_html(cursor, "http://example.com") is None

    def test_none_blob_returns_none(self):
        cursor = self._make_cursor((None,))
        assert _get_previous_snapshot_html(cursor, "http://example.com") is None

    def test_corrupt_zlib_returns_none(self):
        cursor = self._make_cursor((b"not-valid-zlib",))
        assert _get_previous_snapshot_html(cursor, "http://example.com") is None

    def test_non_ascii_does_not_crash(self):
        """Accented chars are collapsed by normalization (non-[a-z0-9] → space);
        the call must not raise and ASCII stems must survive."""
        html_bytes = "café résumé naïve".encode("utf-8")
        compressed = zlib.compress(html_bytes)
        cursor = self._make_cursor((compressed,))
        result = _get_previous_snapshot_html(cursor, "http://example.com")
        assert result is not None
        assert "caf" in result


class TestEmitNewPaperGuardsCombined:
    """All guards must work together — no single guard bypass allows false events."""

    @patch("backend.pipeline.feed_events.get_connection")
    def test_seed_suppresses_regardless_of_other_conditions(self, mock_get_conn):
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Paper", True, True, "working_paper")],
            url="http://example.com",
            is_seed=True,
        )
        assert result == 0
        mock_get_conn.assert_not_called()

    @patch("backend.pipeline.feed_events.get_connection")
    def test_published_status_suppressed(self, mock_get_conn):
        conn, cursor = _make_conn_and_cursor(snapshot_count=5, prev_html=None)
        mock_get_conn.return_value = conn
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Paper", True, True, "published")],
            url="http://example.com",
        )
        assert result == 0

    @patch("backend.pipeline.feed_events.get_connection")
    def test_no_baseline_suppresses_working_paper(self, mock_get_conn):
        conn, cursor = _make_conn_and_cursor(snapshot_count=1, prev_html=None)
        mock_get_conn.return_value = conn
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "New Paper", True, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 0

    @patch("backend.pipeline.feed_events.get_connection")
    def test_title_in_prev_snapshot_suppresses(self, mock_get_conn):
        conn, cursor = _make_conn_and_cursor(
            snapshot_count=5,
            prev_html="<p>Trade and Wages: Evidence from Germany</p>",
        )
        mock_get_conn.return_value = conn
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Trade and Wages: Evidence from Germany", True, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 0

    @patch("backend.pipeline.feed_events.get_connection")
    def test_all_guards_pass_creates_event(self, mock_get_conn):
        conn, cursor = _make_conn_and_cursor(
            snapshot_count=5,
            prev_html="<p>Some Other Paper Title</p>",
        )
        mock_get_conn.return_value = conn
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Brand New Paper", True, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 1
        insert_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in str(c)
        ]
        assert len(insert_calls) == 1

    @patch("backend.pipeline.feed_events.get_connection")
    def test_none_status_suppressed(self, mock_get_conn):
        conn, cursor = _make_conn_and_cursor(snapshot_count=5, prev_html=None)
        mock_get_conn.return_value = conn
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(1, "Paper", True, True, None)],
            url="http://example.com",
        )
        assert result == 0

    @patch("backend.pipeline.feed_events.get_connection")
    def test_empty_results_list_creates_no_events(self, mock_get_conn):
        result = FeedEventEmitter.emit_new_paper_events(
            [], url="http://example.com",
        )
        assert result == 0
        mock_get_conn.assert_not_called()

    @patch("backend.pipeline.feed_events.get_connection")
    def test_multiple_papers_mixed_guards(self, mock_get_conn):
        """Mix of valid and invalid papers: only valid ones get events."""
        conn, cursor = _make_conn_and_cursor(
            snapshot_count=5,
            prev_html="<p>existing paper on page</p>",
        )
        mock_get_conn.return_value = conn
        results = [
            FakeSaveResult(1, "Existing Paper on Page", True, True, "working_paper"),
            FakeSaveResult(2, "Published Paper", True, True, "published"),
            FakeSaveResult(3, "Genuinely New Paper", True, True, "working_paper"),
        ]
        count = FeedEventEmitter.emit_new_paper_events(results, url="http://example.com")
        assert count == 1


class TestDuplicatePaperEventGuard:
    """Duplicate papers (existing paper, new to URL) need dedup check."""

    @patch("backend.pipeline.feed_events.get_connection")
    def test_duplicate_with_prior_event_suppressed(self, mock_get_conn):
        conn, cursor = _make_conn_and_cursor(
            snapshot_count=5,
            prev_html=None,
            prior_event_count=1,
        )
        mock_get_conn.return_value = conn
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(10, "Existing Paper", False, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 0

    @patch("backend.pipeline.feed_events.get_connection")
    def test_duplicate_without_prior_event_creates(self, mock_get_conn):
        conn, cursor = _make_conn_and_cursor(
            snapshot_count=5,
            prev_html=None,
            prior_event_count=0,
        )
        mock_get_conn.return_value = conn
        result = FeedEventEmitter.emit_new_paper_events(
            [FakeSaveResult(10, "Existing Paper", False, True, "working_paper")],
            url="http://example.com",
        )
        assert result == 1


class TestStatusChangeEventIntegrity:
    """Status change events must only fire for forward progressions (PR #153)."""

    @patch("backend.pipeline.feed_events.get_connection")
    def test_forward_progression_creates_event(self, mock_get_conn):
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        mock_get_conn.return_value = conn

        FeedEventEmitter.emit_status_change(1, "working_paper", "accepted")
        insert_calls = [
            c for c in cursor.execute.call_args_list
            if "status_change" in str(c)
        ]
        assert len(insert_calls) == 1

    @patch("backend.pipeline.feed_events.get_connection")
    def test_event_stores_both_statuses(self, mock_get_conn):
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        mock_get_conn.return_value = conn

        FeedEventEmitter.emit_status_change(1, "working_paper", "published")
        args = cursor.execute.call_args[0][1]
        assert "working_paper" in args
        assert "published" in args


class TestTitleChangeEventIntegrity:
    """Title change events store old and new titles."""

    @patch("backend.pipeline.feed_events.get_connection")
    def test_title_change_stores_both_titles(self, mock_get_conn):
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        mock_get_conn.return_value = conn

        FeedEventEmitter.emit_title_change(1, "Old Title", "New Title")
        args = cursor.execute.call_args[0][1]
        assert "Old Title" in args
        assert "New Title" in args

    @patch("backend.pipeline.feed_events.get_connection")
    def test_title_change_event_type(self, mock_get_conn):
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        mock_get_conn.return_value = conn

        FeedEventEmitter.emit_title_change(1, "A", "B")
        sql = cursor.execute.call_args[0][0]
        assert "title_change" in sql
