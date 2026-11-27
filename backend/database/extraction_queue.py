"""Order of the extraction queue (pages whose content_hash != extracted_hash).

The worker used to take pages in researcher_urls.id order, so the newest
URLs (mostly HOME pages added by site mining, ids at the end of the table)
waited behind every change to older pages. The order is now, highest first:

1. Changed pages that have waited at least CHANGED_PAGE_GRACE_HOURS.
   Diff extraction compares the page with the previous *fetched* version,
   so a change left waiting through a second change would be compared with
   the wrong baseline and lost. Changed pages are a small share of the
   capacity (a few hundred a day), and the feed shows them.
2. Pages never extracted (coverage backlog).
3. Changed pages that changed recently.

Within each tier, pages of researchers with a top-5 journal paper or a
recent feed event, and publication/research pages, come first; each hour
waited counts like an hour of head start, so nothing waits forever behind
newer pages of the same tier. Ties go to the lower URL id (old behaviour).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from backend.database.connection import fetch_all

logger = logging.getLogger(__name__)

CHANGED_PAGE_GRACE_HOURS = 12
# Head start in hours of waiting.
TOP5_BONUS_HOURS = 48
RECENT_ACTIVITY_BONUS_HOURS = 24
PAPER_PAGE_BONUS_HOURS = 12
RECENT_ACTIVITY_DAYS = 365
SIGNALS_TTL_SECONDS = 3600

_PAPER_PAGE_TYPES = frozenset({"RESEARCH", "PUBLICATIONS", "WORKING_PAPERS", "PAPERS"})

# Venue spellings seen in production include split words
# ("Journal of Polit ical Economy", "Review of Economic Stud ies").
TOP5_VENUE_REGEXP = (
    "american economic review|econometrica|journal of polit ?ical economy"
    "|quarterly journal of economics|review of economic stud ?ies"
)
_TOP5_SQL = """
    SELECT DISTINCT a.researcher_id
    FROM papers p JOIN authorship a ON a.publication_id = p.id
    WHERE p.venue REGEXP %s
"""
_RECENT_SQL = """
    SELECT DISTINCT a.researcher_id
    FROM feed_events fe JOIN authorship a ON a.publication_id = fe.paper_id
    WHERE fe.event_type IN ('new_paper', 'status_change')
      AND fe.created_at >= NOW() - INTERVAL %s DAY
"""

_signals_lock = threading.Lock()
_signals: tuple[float, frozenset[int], frozenset[int]] | None = None


def researcher_signals() -> tuple[frozenset[int], frozenset[int]]:
    """(top-5 researcher ids, recently active researcher ids), cached for an hour.

    Two separate indexed queries take about a second on production; joined
    into the queue query as subqueries they took a minute. A failed refresh
    keeps the previous sets (or none): ordering is an optimisation and must
    never stop extraction.
    """
    global _signals
    now = time.monotonic()
    with _signals_lock:
        if _signals is not None and now - _signals[0] < SIGNALS_TTL_SECONDS:
            return _signals[1], _signals[2]
        try:
            top5 = frozenset(r["researcher_id"] for r in fetch_all(_TOP5_SQL, (TOP5_VENUE_REGEXP,)))
            recent = frozenset(r["researcher_id"] for r in fetch_all(_RECENT_SQL, (RECENT_ACTIVITY_DAYS,)))
        except Exception as e:
            logger.warning("Extraction queue: researcher signals unavailable: %s: %s",
                           type(e).__name__, e)
            if _signals is not None:
                # Retry after a few minutes rather than on every poll.
                _signals = (now - SIGNALS_TTL_SECONDS + 300, _signals[1], _signals[2])
                return _signals[1], _signals[2]
            return frozenset(), frozenset()
        _signals = (now, top5, recent)
        return top5, recent


def reset_signals_cache() -> None:
    global _signals
    with _signals_lock:
        _signals = None


def _hours_waiting(row: dict, now: datetime) -> float:
    since = row.get("pending_since")
    if since is None:
        return 0.0
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return max((now - since).total_seconds() / 3600, 0.0)


def priority_key(row: dict, top5: frozenset[int], recent: frozenset[int],
                 now: datetime) -> tuple:
    """Sort key: ascending order is extraction order."""
    waited = _hours_waiting(row, now)
    never = bool(row.get("never_extracted"))
    if not never and waited >= CHANGED_PAGE_GRACE_HOURS:
        tier = 2
    elif never:
        tier = 1
    else:
        tier = 0
    researcher_id = row.get("researcher_id")
    bonus = 0.0
    if researcher_id in top5:
        bonus += TOP5_BONUS_HOURS
    if researcher_id in recent:
        bonus += RECENT_ACTIVITY_BONUS_HOURS
    if (row.get("page_type") or "").strip().upper() in _PAPER_PAGE_TYPES:
        bonus += PAPER_PAGE_BONUS_HOURS
    return (-tier, -(bonus + waited), row.get("id") or 0)


def prioritise(rows: list[dict], limit: int | None = None,
               now: datetime | None = None) -> list[dict]:
    """Return rows in extraction order, at most `limit` of them."""
    if not rows:
        return []
    top5, recent = researcher_signals()
    now = now or datetime.now(timezone.utc)
    ordered = sorted(rows, key=lambda r: priority_key(r, top5, recent, now))
    return ordered[:limit] if limit is not None else ordered
