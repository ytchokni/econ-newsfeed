"""Order of the extraction queue (backend.database.extraction_queue)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.database import extraction_queue as eq

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def _row(url_id, *, never=False, waited_h=0.0, researcher_id=None, page_type="HOME"):
    return {
        "id": url_id,
        "researcher_id": researcher_id if researcher_id is not None else 1000 + url_id,
        "url": f"https://example.org/{url_id}",
        "page_type": page_type,
        "content_hash": f"h{url_id}",
        "never_extracted": 1 if never else 0,
        # MySQL DATETIME comes back naive (UTC).
        "pending_since": (NOW - timedelta(hours=waited_h)).replace(tzinfo=None),
    }


@pytest.fixture(autouse=True)
def _fresh_signals():
    eq.reset_signals_cache()
    yield
    eq.reset_signals_cache()


def _order(rows, top5=(), recent=(), limit=None):
    with patch.object(eq, "researcher_signals", return_value=(frozenset(top5), frozenset(recent))):
        return [r["id"] for r in eq.prioritise(rows, limit=limit, now=NOW)]


def test_never_extracted_pages_come_before_recent_changes():
    rows = [_row(1, waited_h=2), _row(2, never=True, waited_h=1), _row(3, waited_h=3)]
    assert _order(rows) == [2, 3, 1]


def test_changed_page_waiting_past_grace_goes_first():
    # A pending change waiting through a second change would lose the first
    # (diffs compare with the previous *fetched* version).
    rows = [_row(1, never=True, waited_h=500), _row(2, waited_h=eq.CHANGED_PAGE_GRACE_HOURS + 1)]
    assert _order(rows) == [2, 1]


def test_top5_and_active_researchers_and_paper_pages_rank_first_within_tier():
    rows = [
        _row(1, never=True, researcher_id=10),
        _row(2, never=True, researcher_id=20),
        _row(3, never=True, researcher_id=30),
        _row(4, never=True, researcher_id=40, page_type="publications"),
    ]
    assert _order(rows, top5={20}, recent={30}) == [2, 3, 4, 1]


def test_waiting_ages_pages_past_bonus_so_none_starves():
    plain_old = _row(1, never=True, waited_h=eq.TOP5_BONUS_HOURS + 1, researcher_id=10)
    top5_new = _row(2, never=True, waited_h=0, researcher_id=20)
    assert _order([top5_new, plain_old], top5={20}) == [1, 2]


def test_researchers_changed_pages_go_before_their_never_extracted_pages():
    """A new paper on both a fresh page and a changed page must get its event
    from the changed page's diff, not be saved silently by the seed."""
    fresh_page = _row(1, never=True, researcher_id=7)
    changed_page = _row(2, waited_h=1, researcher_id=7)
    other_never = _row(3, never=True, researcher_id=8)
    assert _order([fresh_page, changed_page, other_never]) == [3, 2, 1]


def test_ties_keep_url_id_order_and_limit_applies():
    rows = [_row(i, never=True) for i in (5, 3, 4, 1, 2)]
    assert _order(rows, limit=3) == [1, 2, 3]


def test_missing_timestamp_counts_as_no_wait():
    rows = [dict(_row(1, never=True), pending_since=None), _row(2, never=True, waited_h=1)]
    assert _order(rows) == [2, 1]


def test_signals_are_cached_and_failures_fall_back():
    calls = []

    def fake_fetch(sql, params=None):
        calls.append(sql)
        if "has_top5_pub" in sql:
            return [{"researcher_id": 7}]
        return [{"researcher_id": 8}]

    with patch.object(eq, "fetch_all", side_effect=fake_fetch):
        assert eq.researcher_signals() == (frozenset({7}), frozenset({8}))
        assert eq.researcher_signals() == (frozenset({7}), frozenset({8}))
    assert len(calls) == 2  # second call served from the cache

    eq.reset_signals_cache()
    with patch.object(eq, "fetch_all", side_effect=RuntimeError("db down")):
        assert eq.researcher_signals() == (frozenset(), frozenset())


def test_failed_refresh_keeps_previous_signals():
    with patch.object(eq, "fetch_all", return_value=[{"researcher_id": 7}]):
        eq.researcher_signals()
    with patch.object(eq, "fetch_all", side_effect=RuntimeError("db down")), \
         patch.object(eq.time, "monotonic", return_value=eq.time.monotonic() + 2 * eq.SIGNALS_TTL_SECONDS):
        assert eq.researcher_signals() == (frozenset({7}), frozenset({7}))


def test_get_urls_needing_extraction_orders_all_pending_rows():
    from backend.database.researchers import get_urls_needing_extraction
    changed = {k: v for k, v in _row(1).items() if k != "pending_since"}
    never = {k: v for k, v in _row(2, never=True).items() if k != "pending_since"}
    waited = (NOW - timedelta(hours=eq.CHANGED_PAGE_GRACE_HOURS + 1)).replace(tzinfo=None)

    def fake_fetch(sql, params=None):
        if "extraction_retry_at" in sql:
            return []  # pages cooling down after failures (none here)
        if "IN (" in sql:
            assert params == (1, 2)
            return [{"url_id": 1, "pending_since": waited}, {"url_id": 2, "pending_since": None}]
        return [dict(changed), dict(never)]

    with patch("backend.database.researchers.fetch_all", side_effect=fake_fetch) as mock_fetch, \
         patch.object(eq, "researcher_signals", return_value=(frozenset(), frozenset())):
        result = get_urls_needing_extraction(limit=1)
    assert [r["id"] for r in result] == [1]  # overdue change before the never-extracted page
    queue_sql = next(c[0][0] for c in mock_fetch.call_args_list if "FROM researcher_urls" in c[0][0])
    assert "LIMIT" not in queue_sql  # ordering needs the whole queue
    # Index-only columns: reading timestamp here scans every stored page.
    assert "timestamp" not in queue_sql and "extracted_at" not in queue_sql
