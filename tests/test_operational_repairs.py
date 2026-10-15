"""Behavioral checks for fail-closed migrations and delayed digest delivery."""
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.database.schema import _ensure_snapshot_guard
from backend.database.users import get_feed_events_for_researchers
from backend.digest import _render_digest_html


def test_trigger_creation_failure_preserves_old_guard():
    cursor = MagicMock()
    cursor.fetchone.return_value = (0,)
    def execute(sql, params=None):
        if sql.startswith("CREATE TRIGGER"):
            raise RuntimeError("permission denied")
    cursor.execute.side_effect = execute
    with pytest.raises(RuntimeError, match="permission denied"):
        _ensure_snapshot_guard(cursor)
    assert not any("DROP TRIGGER" in c.args[0] for c in cursor.execute.call_args_list)


def test_existing_versioned_trigger_is_never_recreated():
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    _ensure_snapshot_guard(cursor)
    assert not any("CREATE TRIGGER" in c.args[0] for c in cursor.execute.call_args_list)
    assert all("trg_feed_events_snapshot_guard_v2" not in c.args[0]
               for c in cursor.execute.call_args_list if "DROP TRIGGER" in c.args[0])


def test_digest_escapes_external_text_and_url_attributes():
    event = {"paper_id": 1, "title": '</a><a href="https://evil.invalid">fake</a>',
             "first_name": "<b>First</b>", "last_name": "Last", "status": "accepted"}
    now = datetime.now(timezone.utc)
    html = _render_digest_html([event], '<img src=x>', 'https://app.invalid/?a=" onclick="bad', now, now)
    assert '<a href="https://evil.invalid">' not in html
    assert "&lt;/a&gt;&lt;a href=" in html
    assert "&lt;img src=x&gt;" in html
    assert "<b>First</b>" not in html
    assert 'a=&quot; onclick=&quot;bad' in html


def test_late_inserted_event_is_delivered_once_despite_old_source_date():
    # Execute the real selection SQL, rather than mocking its filtering logic.
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
        INSERT INTO papers VALUES(1,'Title','accepted','2026','Journal');
        INSERT INTO researchers VALUES(1,'Given','Surname');
        INSERT INTO authorship VALUES(1,1);
        INSERT INTO feed_events VALUES(1,1,'status_change','working_paper','accepted',
          '2026-10-01','2026-10-07',1,'Journal');
        INSERT INTO digest_deliveries VALUES(7,1);
        INSERT INTO feed_events VALUES(2,1,'status_change','accepted','published',
          '2026-10-02','2026-10-15',1,'Journal');
        INSERT INTO feed_events VALUES(3,1,'status_change','working_paper','accepted',
          '2026-09-20',NULL,1,'Journal');
    """)
    def execute(sql, params):
        return [dict(r) for r in conn.execute(sql.replace('%s', '?'), params)]
    try:
        with patch('backend.database.users.fetch_all', side_effect=execute):
            rows = get_feed_events_for_researchers([1], '2026-09-01', user_id=7, legacy_since='2026-10-08')
            assert [r['event_id'] for r in rows] == [2]
            conn.execute('INSERT INTO digest_deliveries VALUES(7,2)')
            assert get_feed_events_for_researchers([1], '2026-09-01', user_id=7, legacy_since='2026-10-08') == []
    finally:
        conn.close()


def test_unavailable_digest_cannot_be_enabled(client):
    from backend.auth import get_current_user
    from backend.api import app
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    try:
        with patch('backend.api.scheduler.DIGEST_ENABLED', False), patch('backend.api.Database.update_notification_prefs') as update:
            assert client.get('/api/capabilities').json() == {"weekly_digest_available": False}
            assert client.patch('/api/users/notifications', json={"digest_enabled": True}).status_code == 503
            update.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_readiness_requires_database_and_guard(client):
    with patch('backend.api.fetch_one', return_value={"schema_ready": 1}):
        assert client.get('/api/ready').status_code == 200
    with patch('backend.api.fetch_one', return_value={"schema_ready": 0}):
        assert client.get('/api/ready').status_code == 503
    with patch('backend.api.fetch_one', side_effect=RuntimeError('DB unavailable')):
        assert client.get('/api/ready').status_code == 503
