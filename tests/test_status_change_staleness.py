"""Tests for staleness guard on status_change events."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

from unittest.mock import patch, MagicMock
from dataclasses import dataclass


@dataclass
class FakeSnapshotResult:
    status_changed: bool
    old_status: str | None
    new_status: str | None


class TestStalenessGuard:
    """Status change events should be suppressed for very old papers."""

    @patch("extraction.FeedEventEmitter")
    @patch("extraction.append_paper_snapshot")
    @patch("extraction.fetch_all")
    @patch("extraction.compute_title_hash")
    def test_suppresses_status_change_for_old_paper(
        self, mock_hash, mock_fetch_all, mock_snapshot, mock_emitter
    ):
        from extraction import _append_snapshots

        mock_hash.side_effect = lambda t: f"hash_{t}"
        mock_fetch_all.return_value = [{"id": 1, "title_hash": "hash_Old Paper"}]
        mock_snapshot.return_value = FakeSnapshotResult(
            status_changed=True, old_status="working_paper", new_status="published"
        )

        pubs = [{"title": "Old Paper", "status": "published", "year": "1999",
                 "venue": None, "abstract": None, "draft_url": None}]
        _append_snapshots(pubs, "http://example.com")

        mock_emitter.emit_status_change.assert_not_called()

    @patch("extraction.FeedEventEmitter")
    @patch("extraction.append_paper_snapshot")
    @patch("extraction.fetch_all")
    @patch("extraction.compute_title_hash")
    def test_allows_status_change_for_recent_paper(
        self, mock_hash, mock_fetch_all, mock_snapshot, mock_emitter
    ):
        from extraction import _append_snapshots

        mock_hash.side_effect = lambda t: f"hash_{t}"
        mock_fetch_all.return_value = [{"id": 2, "title_hash": "hash_Recent Paper"}]
        mock_snapshot.return_value = FakeSnapshotResult(
            status_changed=True, old_status="working_paper", new_status="published"
        )

        pubs = [{"title": "Recent Paper", "status": "published", "year": "2024",
                 "venue": "AER", "abstract": None, "draft_url": None}]
        _append_snapshots(pubs, "http://example.com")

        mock_emitter.emit_status_change.assert_called_once()

    @patch("extraction.FeedEventEmitter")
    @patch("extraction.append_paper_snapshot")
    @patch("extraction.fetch_all")
    @patch("extraction.compute_title_hash")
    def test_allows_status_change_with_no_year(
        self, mock_hash, mock_fetch_all, mock_snapshot, mock_emitter
    ):
        from extraction import _append_snapshots

        mock_hash.side_effect = lambda t: f"hash_{t}"
        mock_fetch_all.return_value = [{"id": 3, "title_hash": "hash_No Year Paper"}]
        mock_snapshot.return_value = FakeSnapshotResult(
            status_changed=True, old_status="accepted", new_status="published"
        )

        pubs = [{"title": "No Year Paper", "status": "published", "year": None,
                 "venue": None, "abstract": None, "draft_url": None}]
        _append_snapshots(pubs, "http://example.com")

        mock_emitter.emit_status_change.assert_called_once()
