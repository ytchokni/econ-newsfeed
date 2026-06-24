"""Tests for digest.py — email rendering and digest job."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from backend.digest import _render_digest_html, run_weekly_digest


def _sample_events():
    return [
        {
            "event_type": "new_paper",
            "old_status": None,
            "new_status": "working_paper",
            "created_at": datetime(2026, 6, 8, tzinfo=timezone.utc),
            "paper_id": 100,
            "title": "Monetary Policy in Small Open Economies",
            "status": "working_paper",
            "year": "2026",
            "venue": None,
            "researcher_id": 1,
            "first_name": "Alice",
            "last_name": "Smith",
        },
        {
            "event_type": "status_change",
            "old_status": "working_paper",
            "new_status": "published",
            "created_at": datetime(2026, 6, 9, tzinfo=timezone.utc),
            "paper_id": 101,
            "title": "Trade and Development",
            "status": "published",
            "year": "2025",
            "venue": "AER",
            "researcher_id": 2,
            "first_name": "Bob",
            "last_name": "Jones",
        },
    ]


def test_render_digest_html_groups_by_researcher():
    html = _render_digest_html(
        _sample_events(),
        "Test User",
        "https://example.com/unsub",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 8, tzinfo=timezone.utc),
    )
    assert "Alice Smith" in html
    assert "Bob Jones" in html
    assert "Monetary Policy" in html
    assert "Trade and Development" in html
    assert "Unsubscribe" in html


def test_render_digest_html_empty_events():
    html = _render_digest_html(
        [],
        None,
        "https://example.com/unsub",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 8, tzinfo=timezone.utc),
    )
    assert "No new activity" in html


def test_run_weekly_digest_no_recipients():
    with patch("backend.digest.Database") as mock_db:
        mock_db.get_digest_recipients.return_value = []
        sent = run_weekly_digest()
    assert sent == 0


def test_run_weekly_digest_sends_email():
    recipient = {
        "id": 1,
        "email": "user@example.com",
        "name": "Test",
        "last_digest_sent": None,
        "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        "researcher_ids": [1, 2],
    }
    with patch("backend.digest.Database") as mock_db, \
         patch("backend.digest._send_email", return_value=True) as mock_send:
        mock_db.get_digest_recipients.return_value = [recipient]
        mock_db.get_feed_events_for_researchers.return_value = _sample_events()
        mock_db.generate_unsubscribe_token.return_value = "1.abc123"
        sent = run_weekly_digest()

    assert sent == 1
    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args[0][0] == "user@example.com"
    assert "Weekly Digest" in call_args[0][1]
    mock_db.update_last_digest_sent.assert_called_once()


def test_run_weekly_digest_skips_empty_events():
    recipient = {
        "id": 1,
        "email": "user@example.com",
        "name": "Test",
        "last_digest_sent": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        "researcher_ids": [1],
    }
    with patch("backend.digest.Database") as mock_db:
        mock_db.get_digest_recipients.return_value = [recipient]
        mock_db.get_feed_events_for_researchers.return_value = []
        sent = run_weekly_digest()
    assert sent == 0
