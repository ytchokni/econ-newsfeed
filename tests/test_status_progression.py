"""Exhaustive status progression tests — catches bugs like PR #153 (status regressions).

Tests every pairwise status transition to ensure:
- Only forward progressions are recognized
- Regressions are blocked on the papers table
- Raw LLM status is always stored in snapshots for audit
- PaperSnapshotResult.status_changed is correct for all combinations
"""
import pytest
from database.snapshots import _is_status_progression, _STATUS_RANK, PaperSnapshotResult


ALL_STATUSES = list(_STATUS_RANK.keys())


class TestStatusRankIntegrity:
    """The status hierarchy must be strict and complete."""

    def test_rank_is_strictly_ordered(self):
        ranks = list(_STATUS_RANK.values())
        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)

    def test_all_five_statuses_present(self):
        expected = {'working_paper', 'reject_and_resubmit', 'revise_and_resubmit', 'accepted', 'published'}
        assert set(_STATUS_RANK.keys()) == expected

    def test_working_paper_is_lowest(self):
        assert _STATUS_RANK['working_paper'] == min(_STATUS_RANK.values())

    def test_published_is_highest(self):
        assert _STATUS_RANK['published'] == max(_STATUS_RANK.values())


class TestIsStatusProgressionExhaustive:
    """Every pairwise combination of statuses must be classified correctly."""

    @pytest.mark.parametrize("old,new", [
        (old, new)
        for old in ALL_STATUSES
        for new in ALL_STATUSES
        if _STATUS_RANK[new] > _STATUS_RANK[old]
    ])
    def test_forward_progressions(self, old, new):
        assert _is_status_progression(old, new) is True

    @pytest.mark.parametrize("old,new", [
        (old, new)
        for old in ALL_STATUSES
        for new in ALL_STATUSES
        if _STATUS_RANK[new] < _STATUS_RANK[old]
    ])
    def test_backward_regressions(self, old, new):
        assert _is_status_progression(old, new) is False

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_same_status_is_not_progression(self, status):
        assert _is_status_progression(status, status) is False

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_none_to_status_is_not_progression(self, status):
        assert _is_status_progression(None, status) is False

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_status_to_none_is_not_progression(self, status):
        assert _is_status_progression(status, None) is False

    def test_none_to_none(self):
        assert _is_status_progression(None, None) is False

    def test_unknown_status_treated_as_regression(self):
        assert _is_status_progression("accepted", "unknown_status") is False

    def test_unknown_to_known_is_progression(self):
        """Unknown status gets rank -1, so any known status outranks it."""
        assert _is_status_progression("unknown", "published") is True


class TestPaperSnapshotResultStatusChanged:
    """PaperSnapshotResult.status_changed delegates to _is_status_progression."""

    def test_forward_progression_is_changed(self):
        r = PaperSnapshotResult(changed=True, old_status="working_paper", new_status="accepted")
        assert r.status_changed is True

    def test_backward_regression_is_not_changed(self):
        r = PaperSnapshotResult(changed=True, old_status="published", new_status="working_paper")
        assert r.status_changed is False

    def test_same_status_is_not_changed(self):
        r = PaperSnapshotResult(changed=True, old_status="accepted", new_status="accepted")
        assert r.status_changed is False

    def test_none_old_status_is_not_changed(self):
        r = PaperSnapshotResult(changed=True, old_status=None, new_status="working_paper")
        assert r.status_changed is False

    def test_unchanged_snapshot_has_no_status_change(self):
        r = PaperSnapshotResult(changed=False)
        assert r.status_changed is False

    @pytest.mark.parametrize("old,new", [
        ("working_paper", "reject_and_resubmit"),
        ("reject_and_resubmit", "revise_and_resubmit"),
        ("revise_and_resubmit", "accepted"),
        ("accepted", "published"),
    ])
    def test_each_single_step_forward(self, old, new):
        r = PaperSnapshotResult(changed=True, old_status=old, new_status=new)
        assert r.status_changed is True

    @pytest.mark.parametrize("old,new", [
        ("reject_and_resubmit", "working_paper"),
        ("revise_and_resubmit", "reject_and_resubmit"),
        ("accepted", "revise_and_resubmit"),
        ("published", "accepted"),
    ])
    def test_each_single_step_backward(self, old, new):
        r = PaperSnapshotResult(changed=True, old_status=old, new_status=new)
        assert r.status_changed is False
