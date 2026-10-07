"""Narrow repair planning and transactional execution, with an in-memory DB double."""
from copy import deepcopy

import pytest

from scripts.repair_feed_history import create_plan, apply_plan, validate_plan


class Connection:
    def __init__(self):
        self.identity = {"database_name": "fixture_newsfeed", "server_uuid": "fixture-server"}
        self.state = {
            'papers': [{"id": 1, "title": "A paper", "status": "working_paper"},
                       {"id": 2, "title": "Another paper", "status": "accepted"}],
            'snapshots': [{"id": 20, "paper_id": 1, "status": "accepted"},
                          {"id": 21, "paper_id": 2, "status": "published"}],
            'events': [
                {"id": 3, "paper_id": 1, "event_type": "new_paper", "old_status": None,
                 "new_status": "working_paper", "created_at": "2026-10-06T01:00:00"},
                {"id": 9, "paper_id": 1, "event_type": "new_paper", "old_status": None,
                 "new_status": "working_paper", "created_at": "2026-10-05T01:00:00"},
                {"id": 12, "paper_id": 1, "event_type": "status_change", "old_status": "accepted",
                 "new_status": "published", "created_at": "2026-10-07T01:00:00"},
            ],
            'reviews': [{"id": 30, "feed_event_id": 3, "issues": '["fixture"]'}],
        }
        self.writes = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_delete = False
        self.snapshot = None

    def start_transaction(self, **kwargs):
        self.transaction_args = kwargs
        self.snapshot = deepcopy(self.state)

    def cursor(self, **kwargs):
        return Cursor(self)

    def rollback(self):
        self.rollbacks += 1
        self.state = self.snapshot

    def commit(self):
        self.commits += 1


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        from scripts.repair_feed_history import _actions
        state = self.conn.state
        self.rowcount = 0
        if sql.startswith('SELECT DATABASE()'):
            rows = [self.conn.identity]
        elif sql.startswith('SELECT paper_id AS id'):
            rows = []
            for paper in state['papers']:
                actions = _actions([e for e in state['events'] if e['paper_id'] == paper['id']])
                if actions['delete_new_paper_event_ids']:
                    rows.append({'id': paper['id']})
        elif sql.startswith('SELECT * FROM papers'):
            rows = [p for p in state['papers'] if p['id'] == params[0]]
        elif sql.startswith('SELECT * FROM feed_events'):
            rows = [e for e in state['events'] if e['paper_id'] == params[0] and e['event_type'] == 'new_paper']
        elif sql.startswith('SELECT * FROM feed_event_reviews'):
            rows = [r for r in state['reviews'] if r['feed_event_id'] in params]
        elif sql.startswith('DELETE FROM feed_events'):
            self.conn.writes.append((sql, params))
            if self.conn.fail_delete:
                raise RuntimeError('simulated delete failure')
            before = len(state['events'])
            state['events'] = [e for e in state['events'] if not
                               (e['id'] == params[0] and e['paper_id'] == params[1]
                                and e['event_type'] == 'new_paper')]
            self.rowcount = before - len(state['events'])
            state['reviews'] = [r for r in state['reviews'] if r['feed_event_id'] != params[0]]
            rows = []
        else:
            raise AssertionError(f'Unexpected SQL: {sql}')
        self.rows = deepcopy(sorted(rows, key=lambda row: row.get('id', 0)))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


def test_read_only_plan_keeps_complete_before_images_and_exact_actions():
    conn = Connection()
    before = deepcopy(conn.state)
    plan = create_plan(conn)
    assert conn.transaction_args['readonly'] is True
    assert conn.state == before and not conn.writes and conn.commits == 0
    assert plan['database'] == conn.identity
    repair = plan['repairs'][0]
    assert repair['paper_before'] == before['papers'][0]
    assert repair['events_before'] == [e for e in before['events'] if e['event_type'] == 'new_paper']
    assert repair['reviews_before'] == before['reviews']
    assert repair['actions'] == {'keep_new_paper_event_id': 9, 'delete_new_paper_event_ids': [3]}


def test_apply_preserves_earliest_announcement_and_leaves_status_history_unchanged():
    conn = Connection()
    before = deepcopy(conn.state)
    plan = create_plan(conn)
    result = apply_plan(conn, plan)
    assert result == {'duplicate_events_deleted': 1}
    assert conn.state['papers'] == before['papers']
    assert conn.state['snapshots'] == before['snapshots']
    assert conn.state['events'][-1] == before['events'][-1]
    assert [e['id'] for e in conn.state['events']] == [9, 12]
    assert conn.commits == 1
    # Deleted review rows are retained in the immutable plan for recovery.
    assert not conn.state['reviews'] and plan['repairs'][0]['reviews_before'][0]['id'] == 30


@pytest.mark.parametrize('change', ['paper', 'event', 'review', 'insert_event', 'delete_paper'])
def test_any_changed_before_image_aborts_all_repairs_before_writing(change):
    conn = Connection()
    plan = create_plan(conn)
    if change == 'paper':
        conn.state['papers'][0]['status'] = 'published'
    elif change == 'event':
        conn.state['events'][0]['created_at'] = '2026-10-04T01:00:00'
    elif change == 'review':
        conn.state['reviews'][0]['issues'] = '[]'
    elif change == 'insert_event':
        row = deepcopy(conn.state['events'][0])
        row['id'] = 99
        conn.state['events'].append(row)
    else:
        conn.state['papers'].pop(0)
    changed = deepcopy(conn.state)
    with pytest.raises(ValueError, match='changed|no longer exists'):
        apply_plan(conn, plan)
    assert conn.state == changed and not conn.writes and conn.commits == 0


def test_different_database_identity_rejects_plan():
    conn = Connection()
    plan = create_plan(conn)
    conn.identity['server_uuid'] = 'different-server'
    with pytest.raises(ValueError, match='different database'):
        apply_plan(conn, plan)
    assert not conn.writes


@pytest.mark.parametrize('mutation', ['action', 'sql', 'boolean_id', 'duplicate_paper', 'status_action', 'old_format'])
def test_modified_actions_and_invalid_plan_fields_are_rejected(mutation):
    conn = Connection()
    plan = create_plan(conn)
    if mutation == 'action':
        plan['repairs'][0]['actions']['delete_new_paper_event_ids'] = [9]
    elif mutation == 'sql':
        plan['sql'] = 'DELETE FROM papers'
    elif mutation == 'boolean_id':
        plan['repairs'][0]['paper_before']['id'] = True
    elif mutation == 'status_action':
        plan['repairs'][0]['actions']['promote_status_to'] = 'published'
    elif mutation == 'old_format':
        plan['format'] = 'econ-newsfeed-feed-history-v1'
    else:
        plan['repairs'].append(deepcopy(plan['repairs'][0]))
    with pytest.raises(ValueError):
        validate_plan(plan)
    assert not conn.writes


def test_mid_apply_failure_rolls_back_deletions():
    conn = Connection()
    extra = deepcopy(conn.state['events'][0])
    extra['id'] = 4
    conn.state['events'].append(extra)
    plan = create_plan(conn)
    before = deepcopy(conn.state)
    original_cursor = conn.cursor
    def cursor_with_second_delete_failure(**kwargs):
        cursor = original_cursor(**kwargs)
        original_execute = cursor.execute
        def execute(sql, params=()):
            if sql.startswith('DELETE FROM feed_events') and params[0] == 4:
                conn.fail_delete = True
            return original_execute(sql, params)
        cursor.execute = execute
        return cursor
    conn.cursor = cursor_with_second_delete_failure
    with pytest.raises(RuntimeError, match='delete failure'):
        apply_plan(conn, plan)
    assert conn.state == before and conn.commits == 0


def test_validates_all_papers_before_deleting_any_events():
    conn = Connection()
    for event in deepcopy(conn.state['events'][:2]):
        event['id'] += 100
        event['paper_id'] = 2
        conn.state['events'].append(event)
    plan = create_plan(conn)
    conn.state['papers'][1]['title'] = 'Concurrent edit'
    with pytest.raises(ValueError, match='changed'):
        apply_plan(conn, plan)
    assert not conn.writes


def test_equal_timestamps_keep_smallest_event_id():
    conn = Connection()
    conn.state['events'][1]['created_at'] = conn.state['events'][0]['created_at']
    plan = create_plan(conn)
    assert plan['repairs'][0]['actions']['keep_new_paper_event_id'] == 3
    assert plan['repairs'][0]['actions']['delete_new_paper_event_ids'] == [9]
