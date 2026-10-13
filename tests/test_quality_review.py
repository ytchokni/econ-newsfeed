"""Tests for quality-review auto-correction safeguards.

Covers the forward-only status guard, NOT_NEW/hide event-type gating,
stale-batch fingerprint checks, and optimistic (guarded) updates.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.enrichment import quality_review, review_batch


def _event(**overrides):
    base = {
        "event_id": 42,
        "event_type": "new_paper",
        "paper_id": 7,
        "title": "Monetary Policy and Exchange Rates",
        "year": "2024",
        "venue": "NBER Working Paper",
        "status": "working_paper",
        "source_url": "https://example.edu/research",
    }
    base.update(overrides)
    return base


def _status_review(new_status="published"):
    return {
        "issues": [
            {
                "type": "MISCLASSIFICATION",
                "severity": "high",
                "description": "Paper is published.",
                "correction": new_status,
            }
        ]
    }


def _review(issue_type: str, correction: str):
    return {
        "issues": [{
            "type": issue_type,
            "severity": "high",
            "description": "test issue",
            "correction": correction,
        }],
    }


# ---------------------------------------------------------------------------
# Forward-only status guard
# ---------------------------------------------------------------------------

def test_apply_corrections_blocks_backward_status_regression(monkeypatch):
    def fail_update(*args, **kwargs):
        raise AssertionError("backward status correction must not update papers")

    monkeypatch.setattr(quality_review, "_execute_guarded_update", fail_update)

    actions = quality_review.apply_corrections(
        _event(status="published"),
        _status_review("working_paper"),
    )

    assert actions == []


def test_apply_corrections_only_hides_not_new_for_new_paper_events(monkeypatch):
    execute = MagicMock()
    monkeypatch.setattr(quality_review, "execute_query", execute)

    actions = quality_review.apply_corrections(
        _event(event_type="status_change"),
        _review("NOT_NEW", "hide"),
    )

    assert actions == []
    execute.assert_not_called()


def test_apply_corrections_hides_not_new_new_paper_events(monkeypatch):
    execute = MagicMock()
    monkeypatch.setattr(quality_review, "execute_query", execute)

    actions = quality_review.apply_corrections(
        _event(event_type="new_paper"),
        _review("NOT_NEW", "hide"),
    )

    assert actions == [{
        "type": "hide_event",
        "event_id": 42,
        "paper_title": "Monetary Policy and Exchange Rates",
    }]
    execute.assert_called_once_with(
        "DELETE FROM feed_events WHERE id = %s", (42,),
    )


# ---------------------------------------------------------------------------
# Stale-batch fingerprint guard
# ---------------------------------------------------------------------------

def test_batch_correction_skips_stale_fingerprint(monkeypatch):
    submitted_event = _event(status="working_paper")
    stale_fingerprint = quality_review.build_event_fingerprint(submitted_event)
    current_event = _event(status="accepted")

    def fail_update(*args, **kwargs):
        raise AssertionError("stale batch review must not update papers")

    monkeypatch.setattr(quality_review, "_execute_guarded_update", fail_update)

    actions = quality_review.apply_corrections(
        current_event,
        _status_review("published"),
        expected_fingerprint=stale_fingerprint,
        require_fingerprint=True,
    )

    assert actions == []


def test_batch_correction_skips_legacy_unfingerprinted_items(monkeypatch):
    def fail_update(*args, **kwargs):
        raise AssertionError("legacy batch review must not update papers")

    monkeypatch.setattr(quality_review, "_execute_guarded_update", fail_update)

    actions = quality_review.apply_corrections(
        _event(),
        _status_review(),
        require_fingerprint=True,
    )

    assert actions == []


# ---------------------------------------------------------------------------
# Guarded (optimistic) updates
# ---------------------------------------------------------------------------

def test_current_correction_uses_guarded_status_update(monkeypatch):
    calls = []

    def fake_update(query, params):
        calls.append((query, params))
        return 1

    monkeypatch.setattr(quality_review, "_execute_guarded_update", fake_update)
    event = _event(status="working_paper")

    actions = quality_review.apply_corrections(event, _status_review("published"))

    assert actions == [
        {
            "type": "update_status",
            "paper_id": 7,
            "old_value": "working_paper",
            "new_value": "published",
        }
    ]
    assert calls == [
        (
            "UPDATE papers SET status = %s WHERE id = %s AND status <=> %s",
            ("published", 7, "working_paper"),
        )
    ]


def test_raced_current_correction_reports_no_action(monkeypatch):
    monkeypatch.setattr(quality_review, "_execute_guarded_update", lambda *args: 0)

    actions = quality_review.apply_corrections(_event(), _status_review())

    assert actions == []


# ---------------------------------------------------------------------------
# Batch custom-id parsing and deleted-event handling
# ---------------------------------------------------------------------------

def test_review_batch_custom_id_preserves_event_id_and_fingerprint():
    assert review_batch._parse_custom_id("evt_123_abcdef123456") == (
        123,
        "abcdef123456",
    )
    assert review_batch._parse_custom_id("evt_123") == (123, None)
    assert review_batch._parse_event_id("evt_123_abcdef123456") == 123


def test_completed_batch_does_not_save_review_for_deleted_event(monkeypatch):
    result_line = json.dumps(
        {
            "custom_id": "evt_99_abcdef123456",
            "response": {
                "status_code": 200,
                "body": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"issues": [], "notes": "No issues found"}
                                )
                            }
                        }
                    ],
                },
            },
        }
    )
    client = MagicMock()
    client.files.content.return_value = SimpleNamespace(text=result_line)
    batch = SimpleNamespace(id="batch_1", output_file_id="file_1")

    monkeypatch.setattr(
        review_batch,
        "_load_events_for_batch",
        lambda lines: ([json.loads(result_line)], {}),
    )
    save_review = MagicMock()
    monkeypatch.setattr(review_batch, "save_review", save_review)
    monkeypatch.setattr(review_batch, "execute_query", MagicMock())

    processed = review_batch._process_completed_batch(client, batch, db_id=1)

    assert processed == 1
    save_review.assert_not_called()


def _high_severity_review():
    return {'issues': [
        {'type': 'MISCLASSIFICATION', 'severity': 'high', 'correction': 'published'},
        {'type': 'WRONG_VENUE', 'severity': 'high', 'correction': 'none'},
        {'type': 'NOT_NEW', 'severity': 'high', 'correction': 'hide'},
    ]}


def _forbid_source_mutations(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Automatic model review must not mutate papers or feed events')
    monkeypatch.setattr(quality_review, 'apply_corrections', forbidden)
    monkeypatch.setattr(quality_review, '_execute_guarded_update', forbidden)
    monkeypatch.setattr(quality_review, 'execute_query', forbidden)


def test_realtime_review_saves_high_severity_findings_without_applying_them(monkeypatch):
    _forbid_source_mutations(monkeypatch)
    review = _high_severity_review()
    monkeypatch.setattr(quality_review, 'get_unreviewed_events', lambda **kwargs: [_event()])
    monkeypatch.setattr(quality_review, '_call_model', lambda prompt: review)
    save_review = MagicMock()
    monkeypatch.setattr(quality_review, 'save_review', save_review)

    summary = quality_review.review_events(limit=1)

    assert summary == {'reviewed': 1, 'issues': 3, 'corrections': 0, 'dry_run': False}
    save_review.assert_called_once_with(42, review, None)


@pytest.mark.parametrize('fingerprint', [None, 'stale', 'current'])
def test_batch_review_saves_high_severity_findings_without_applying_them(monkeypatch, fingerprint):
    _forbid_source_mutations(monkeypatch)
    event = _event(status='working_paper', venue='Verified series')
    if fingerprint == 'current':
        fingerprint = quality_review.build_event_fingerprint(event)
    custom_id = f'evt_42_{fingerprint}' if fingerprint else 'evt_42'
    review = _high_severity_review()
    result = {
        'custom_id': custom_id,
        'response': {'status_code': 200, 'body': {
            'choices': [{'message': {'content': json.dumps(review)}}],
        }},
    }
    client = MagicMock()
    client.files.content.return_value = SimpleNamespace(text=json.dumps(result))
    batch = SimpleNamespace(id='batch_1', output_file_id='file_1')
    monkeypatch.setattr(review_batch, '_load_events_for_batch', lambda lines: ([result], {42: event}))
    save_review = MagicMock()
    monkeypatch.setattr(review_batch, 'save_review', save_review)
    queries = MagicMock()
    monkeypatch.setattr(review_batch, 'execute_query', queries)

    assert review_batch._process_completed_batch(client, batch, db_id=1) == 1

    save_review.assert_called_once_with(42, review, None)
    assert queries.call_count == 1
    assert 'UPDATE review_batch_jobs' in queries.call_args.args[0]


def test_reviewer_client_uses_bounded_requests_without_hidden_retries(monkeypatch):
    constructor = MagicMock()
    monkeypatch.setattr('openai.OpenAI', constructor)
    monkeypatch.setattr(quality_review, '_openai_client', None)
    quality_review._get_openai_client()
    constructor.assert_called_once_with(timeout=60.0, max_retries=0)


def test_realtime_review_stops_when_all_model_calls_fail(monkeypatch):
    events = MagicMock(return_value=[_event(), _event(event_id=43)])
    model = MagicMock(return_value=None)
    save_review = MagicMock()
    monkeypatch.setattr(quality_review, 'get_unreviewed_events', events)
    monkeypatch.setattr(quality_review, '_call_model', model)
    monkeypatch.setattr(quality_review, 'save_review', save_review)

    summary = quality_review.review_events(limit=100)

    assert summary['reviewed'] == 0
    assert events.call_count == 1
    assert model.call_count == 2
    save_review.assert_not_called()


def test_realtime_dry_run_previews_one_batch_without_persisting_or_repeating(monkeypatch):
    _forbid_source_mutations(monkeypatch)
    events = MagicMock(return_value=[_event()])
    model = MagicMock(return_value=_high_severity_review())
    save_review = MagicMock()
    monkeypatch.setattr(quality_review, 'get_unreviewed_events', events)
    monkeypatch.setattr(quality_review, '_call_model', model)
    monkeypatch.setattr(quality_review, 'save_review', save_review)

    summary = quality_review.review_events(batch_size=1, limit=10, dry_run=True)

    assert summary == {'reviewed': 1, 'issues': 3, 'corrections': 0, 'dry_run': True}
    assert events.call_count == model.call_count == 1
    save_review.assert_not_called()


def test_advisory_upsert_preserves_previously_applied_corrections(monkeypatch):
    import re
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.execute('''CREATE TABLE feed_event_reviews (
        feed_event_id INTEGER PRIMARY KEY, model TEXT, issues TEXT,
        corrections_applied TEXT, reviewed_at TEXT)''')

    def execute(sql, params):
        sql = sql.replace('%s', '?').replace(
            'ON DUPLICATE KEY UPDATE', 'ON CONFLICT(feed_event_id) DO UPDATE SET')
        sql = re.sub(r'VALUES\((\w+)\)', r'excluded.\1', sql)
        conn.execute(sql, tuple(value.isoformat() if hasattr(value, 'isoformat') else value
                                for value in params))

    monkeypatch.setattr(quality_review, 'execute_query', execute)
    applied = [{'type': 'update_venue', 'old_value': 'wrong', 'new_value': 'verified'}]
    try:
        quality_review.save_review(42, {'issues': []}, applied)
        advisory = _high_severity_review()
        quality_review.save_review(42, advisory, None)
        quality_review.save_review(43, advisory, None)
        rows = conn.execute('SELECT issues, corrections_applied FROM feed_event_reviews ORDER BY feed_event_id').fetchall()
        assert json.loads(rows[0][0]) == advisory['issues']
        assert json.loads(rows[0][1]) == applied
        assert rows[1][1] is None
    finally:
        conn.close()
