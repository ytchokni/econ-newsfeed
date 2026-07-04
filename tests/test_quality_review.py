"""Tests for quality-review auto-correction safeguards."""

from unittest.mock import patch

from backend.enrichment.quality_review import apply_corrections


def _event(**overrides):
    event = {
        "event_id": 10,
        "event_type": "new_paper",
        "paper_id": 20,
        "title": "Example Paper",
        "status": "accepted",
        "venue": "Example Journal",
    }
    event.update(overrides)
    return event


def _review(issue_type: str, correction: str):
    return {
        "issues": [{
            "type": issue_type,
            "severity": "high",
            "description": "test issue",
            "correction": correction,
        }],
    }


def test_apply_corrections_allows_forward_status_progression():
    with patch("backend.enrichment.quality_review.execute_query") as execute:
        actions = apply_corrections(
            _event(status="accepted"),
            _review("MISCLASSIFICATION", "published"),
        )

    assert actions == [{
        "type": "update_status",
        "paper_id": 20,
        "old_value": "accepted",
        "new_value": "published",
    }]
    execute.assert_called_once_with(
        "UPDATE papers SET status = %s WHERE id = %s",
        ("published", 20),
    )


def test_apply_corrections_blocks_backward_status_regression():
    with patch("backend.enrichment.quality_review.execute_query") as execute:
        actions = apply_corrections(
            _event(status="published"),
            _review("MISCLASSIFICATION", "working_paper"),
        )

    assert actions == []
    execute.assert_not_called()


def test_apply_corrections_only_hides_not_new_for_new_paper_events():
    with patch("backend.enrichment.quality_review.execute_query") as execute:
        actions = apply_corrections(
            _event(event_type="status_change"),
            _review("NOT_NEW", "hide"),
        )

    assert actions == []
    execute.assert_not_called()


def test_apply_corrections_hides_not_new_new_paper_events():
    with patch("backend.enrichment.quality_review.execute_query") as execute:
        actions = apply_corrections(
            _event(event_type="new_paper"),
            _review("NOT_NEW", "hide"),
        )

    assert actions == [{
        "type": "hide_event",
        "event_id": 10,
        "paper_title": "Example Paper",
    }]
    execute.assert_called_once_with(
        "DELETE FROM feed_events WHERE id = %s",
        (10,),
    )
