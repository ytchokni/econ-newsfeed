"""Tests for digest.py — email rendering and digest job."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.database.users import get_feed_events_for_researchers
from backend.digest import _event_label, _render_digest_html, run_weekly_digest

SNAPSHOT = Path(__file__).parent / "snapshots" / "digest_events.html"


def _sample_events():
    return [
        {
            "event_type": "new_paper",
            "old_status": None,
            "new_status": "working_paper",
            "created_at": datetime(2026, 6, 8, tzinfo=timezone.utc),
            "paper_id": 100,
            "event_id": 10,
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
            "event_id": 11,
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
         patch("backend.digest._send_email", return_value=True) as mock_send, \
         patch("backend.digest.record_digest_delivery") as record_delivery:
        mock_db.get_digest_recipients.return_value = [recipient]
        mock_db.get_feed_events_for_researchers.return_value = _sample_events()
        mock_db.generate_unsubscribe_token.return_value = "1.abc123"
        sent = run_weekly_digest()

    assert sent == 1
    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args[0][0] == "user@example.com"
    assert "Weekly Digest" in call_args[0][1]
    record_delivery.assert_called_once_with(1, [10, 11], record_delivery.call_args.args[2])
    mock_db.get_feed_events_for_researchers.assert_called_once_with(
        [1, 2], recipient["created_at"], user_id=1,
        legacy_since=recipient["created_at"],
    )


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


# ---------------------------------------------------------------------------
# Event selection and per-event labels (#272)
# ---------------------------------------------------------------------------

def _ev(event_id, paper_id, event_type, old_status, new_status, *, title, status,
        venue=None, first="Ran", last="Abramitzky", day=10):
    return {
        "event_id": event_id, "event_type": event_type,
        "old_status": old_status, "new_status": new_status,
        "created_at": datetime(2026, 11, day, tzinfo=timezone.utc),
        "paper_id": paper_id, "title": title, "status": status, "year": "2026",
        "venue": venue, "researcher_id": 1, "first_name": first, "last_name": last,
    }


def _snapshot_events():
    """One item per visible event shape; ``status`` is the paper's current one,
    deliberately different from the event's to catch labels taken from it."""
    return [
        _ev(1, 1, "new_paper", None, "revise_and_resubmit", venue="Econometrica",
            title="Intergenerational Mobility of Immigrants", status="accepted"),
        _ev(2, 2, "new_paper", None, "working_paper",
            title="Assimilation Across Destinations", status="revise_and_resubmit"),
        _ev(3, 3, "status_change", "revise_and_resubmit", "accepted",
            venue="Econometrica", title="Names and Assimilation", status="published"),
        _ev(4, 4, "new_paper", None, "work_in_progress",
            title="Early Draft on Migration", status="working_paper"),
        # Two events for one paper in the same digest: shown once, as new.
        _ev(6, 5, "status_change", "working_paper", "revise_and_resubmit", venue="AER",
            title="Efficient Deployment of Police Resources", status="accepted",
            first="Esther", last="Duflo", day=12),
        _ev(5, 5, "new_paper", None, "working_paper",
            title="Efficient Deployment of Police Resources", status="accepted",
            first="Esther", last="Duflo", day=9),
        _ev(7, 6, "status_change", "working_paper", "reject_and_resubmit", venue="QJE",
            title="Poverty Traps <Revisited>", status="reject_and_resubmit",
            first="Esther", last="Duflo"),
    ]


def _render_snapshot():
    with patch("backend.digest.FRONTEND_URL", "https://econ-newsfeed.example"):
        return _render_digest_html(
            _snapshot_events(), "Test Reader",
            "https://econ-newsfeed.example/api/users/unsubscribe?token=u.sig",
            datetime(2026, 11, 6, tzinfo=timezone.utc),
            datetime(2026, 11, 13, tzinfo=timezone.utc),
        )


def test_digest_html_matches_snapshot():
    """Rendered event section of a sample digest (header and footer excluded,
    so footer changes don't churn it). Regenerate with UPDATE_SNAPSHOTS=1."""
    html = _render_snapshot()
    start = html.index("<h3")
    end = html.index("<hr", start)
    events_html = html[start:end].strip().replace("</ul><h3", "</ul>\n<h3") + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(events_html, encoding="utf-8")
    assert events_html == SNAPSHOT.read_text(encoding="utf-8")


def test_digest_labels_come_from_the_event_not_the_paper():
    html = _render_snapshot()
    assert "(New paper · Revise &amp; Resubmit at Econometrica)" in html
    assert "(New working paper)" in html
    assert "(New work in progress)" in html
    assert "(Revise &amp; Resubmit → Accepted at Econometrica)" in html
    assert "(Working Paper → Reject &amp; Resubmit at QJE)" in html
    # Paper 5: one item, labelled new, at its latest status.
    assert html.count("Efficient Deployment of Police Resources") == 1
    assert "(New paper · Revise &amp; Resubmit at AER)" in html
    # The papers' current statuses must not leak into the labels.
    assert "(Accepted)" not in html and "(Published)" not in html


@pytest.mark.parametrize("events, expected", [
    ([{"event_type": "new_paper", "new_status": "working_paper", "venue": "NBER"}],
     "New working paper"),
    ([{"event_type": "new_paper", "new_status": "accepted", "venue": "  "}],
     "New paper · Accepted"),
    ([{"event_type": "status_change", "old_status": None, "new_status": "accepted",
       "venue": "JPE"}], "Now Accepted at JPE"),
    ([{"event_type": "status_change", "old_status": "accepted", "new_status": None,
       "status": "published", "venue": "JPE"}], "Accepted → Published at JPE"),
])
def test_event_label_edge_cases(events, expected):
    assert _event_label(events) == expected


def test_digest_selects_only_feed_visible_events():
    """Run the real selection SQL: title changes and accepted→published
    (both hidden by the feed) stay out; the delivery ledger still applies."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE papers(id INTEGER, title TEXT, status TEXT, year TEXT, venue TEXT);
        CREATE TABLE researchers(id INTEGER, first_name TEXT, last_name TEXT);
        CREATE TABLE authorship(publication_id INTEGER, researcher_id INTEGER);
        CREATE TABLE feed_events(id INTEGER, paper_id INTEGER, event_type TEXT,
          old_status TEXT, new_status TEXT, created_at TEXT, recorded_at TEXT,
          event_metadata_captured INTEGER, event_venue TEXT);
        CREATE TABLE digest_deliveries(user_id INTEGER, event_id INTEGER);
        INSERT INTO papers VALUES(1,'Title','published','2026','Journal');
        INSERT INTO researchers VALUES(1,'Given','Surname');
        INSERT INTO authorship VALUES(1,1);
        INSERT INTO feed_events VALUES
          (1,1,'new_paper',NULL,'working_paper','2026-11-01','2026-11-01',1,NULL),
          (2,1,'title_change',NULL,NULL,'2026-11-02','2026-11-02',1,NULL),
          (3,1,'status_change','working_paper','accepted','2026-11-03','2026-11-03',1,'Journal'),
          (4,1,'status_change','accepted','published','2026-11-04','2026-11-04',1,'Journal'),
          (5,1,'status_change','working_paper','published','2026-11-05','2026-11-05',1,'Journal');
        INSERT INTO digest_deliveries VALUES(7,1);
    """)

    def execute(sql, params):
        return [dict(r) for r in conn.execute(sql.replace('%s', '?'), params)]
    try:
        with patch('backend.database.users.fetch_all', side_effect=execute):
            rows = get_feed_events_for_researchers([1], '2026-10-01', user_id=7)
    finally:
        conn.close()
    assert sorted(r['event_id'] for r in rows) == [3, 5]


def test_digest_and_feed_share_one_visibility_rule():
    from backend.database import papers, users
    assert users.feed_visible_event_sql is papers.feed_visible_event_sql
    sql, params = papers.feed_visible_event_sql("fe")
    assert params == papers._FEED_VISIBLE_EVENT_TYPES
    assert "title_change" not in params
