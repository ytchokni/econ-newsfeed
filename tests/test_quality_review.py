import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.enrichment import quality_review, review_batch


def _event(**overrides):
    base = {
        "event_id": 42,
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


def test_batch_correction_skips_stale_fingerprint(monkeypatch):
    submitted_event = _event(status="working_paper")
    stale_fingerprint = quality_review.build_event_fingerprint(submitted_event)
    current_event = _event(status="published")

    def fail_update(*args, **kwargs):
        raise AssertionError("stale batch review must not update papers")

    monkeypatch.setattr(quality_review, "_execute_guarded_update", fail_update)

    actions = quality_review.apply_corrections(
        current_event,
        _status_review("accepted"),
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
