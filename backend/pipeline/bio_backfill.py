"""Paced backfill of missing researcher bios from stored HOME pages (issue #258).

The #258 cleanup sets about 3,500 bios to NULL. Normal extraction only
refreshes a bio when the HOME page changes, so without this job most of them
would stay empty. Each run:

1. picks researchers with no bio whose active HOME/HOMEPAGE page has stored
   text, skipping pages already tried at the same content hash (a page with
   no bio is not asked again until it changes; failed calls retry after
   ``RETRY_FAILED_AFTER_HOURS``); researchers with more papers go first;
2. makes at most ``BIO_BACKFILL_BATCH_CALLS`` LLM calls, one at a time, with
   ``BIO_BACKFILL_DELAY_SECONDS`` between them, and at most
   ``BIO_BACKFILL_DAILY_LIMIT`` per UTC day (counted in
   ``researcher_bio_backfill``, so CLI and scheduled runs share it);
3. stops at the first quota/account refusal, so the extraction worker, which
   uses the same free-tier key, gets the quota back at once;
4. does nothing while more than ``BIO_BACKFILL_MAX_BACKLOG`` pages wait for
   extraction, so it only uses quota once the extraction backlog is cleared.

Bios are only written while the researcher still has none, so a bio that
normal extraction saved in the meantime is never replaced. The job is off by
default (``BIO_BACKFILL_ENABLED``); a MySQL advisory lock keeps CLI and
scheduled runs apart.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import mysql.connector

from backend.config import db_config
from backend.database import execute_query, fetch_all, fetch_one
from backend.database.snapshots import append_researcher_snapshot

logger = logging.getLogger(__name__)

_LOCK_NAME = "econ_bio_backfill"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BATCH_CALLS = _env_int("BIO_BACKFILL_BATCH_CALLS", 10)
DAILY_LIMIT = _env_int("BIO_BACKFILL_DAILY_LIMIT", 150)
DELAY_SECONDS = _env_float("BIO_BACKFILL_DELAY_SECONDS", 30.0)
MAX_BACKLOG = _env_int("BIO_BACKFILL_MAX_BACKLOG", 50)
RETRY_FAILED_AFTER_HOURS = 24
_MAX_CONSECUTIVE_FAILURES = 3

_stop_event = threading.Event()


def request_stop() -> None:
    """Ask a running batch to stop after its current call (scheduler shutdown)."""
    _stop_event.set()


@dataclass
class BioBackfillStats:
    considered: int = 0
    llm_calls: int = 0
    saved: int = 0
    no_bio: int = 0
    rejected: int = 0
    failed: int = 0
    skipped_has_bio: int = 0
    calls_today_before: int = 0
    extraction_backlog: int = 0
    stopped: str = "done"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _acquire_lock():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT GET_LOCK(%s, 0)", (_LOCK_NAME,))
        row = cursor.fetchone()
        cursor.close()
        if row and row[0] == 1:
            return conn
        conn.close()
    except Exception as e:
        logger.error("Bio backfill: could not take advisory lock: %s", e)
    return None


def _release_lock(conn) -> None:
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
        cursor.fetchone()
        cursor.close()
    except Exception:
        logger.debug("Bio backfill: lock release failed", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def count_backfill_calls_since(since: datetime) -> int:
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM researcher_bio_backfill WHERE attempted_at >= %s",
        (since,),
    )
    return int(row["n"]) if row else 0


def count_extraction_backlog() -> int:
    """Pages waiting for extraction (same filter as the extraction worker's queue)."""
    row = fetch_one(
        """SELECT COUNT(*) AS n
           FROM researcher_urls ru
           JOIN html_content hc ON hc.url_id = ru.id
           WHERE ru.is_active = TRUE
             AND hc.content_hash IS NOT NULL
             AND hc.has_stored_content = TRUE
             AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)"""
    )
    return int(row["n"]) if row else 0


def get_backfill_candidates(limit: int, now: datetime) -> list[dict]:
    """Researchers with no bio and a stored HOME page not yet tried at this hash."""
    rows = fetch_all(
        """SELECT r.id AS researcher_id, u.id AS url_id, u.url, h.content_hash,
                  (SELECT COUNT(*) FROM authorship a WHERE a.researcher_id = r.id) AS n_pubs
           FROM researchers r
           JOIN researcher_urls u
             ON u.researcher_id = r.id AND u.is_active
            AND UPPER(TRIM(u.page_type)) IN ('HOME', 'HOMEPAGE')
           JOIN html_content h
             ON h.url_id = u.id AND h.content_hash IS NOT NULL
           LEFT JOIN researcher_bio_backfill b ON b.researcher_id = r.id
           WHERE r.description IS NULL
             AND (b.researcher_id IS NULL
                  OR NOT (b.content_hash <=> h.content_hash)
                  OR (b.outcome = 'failed' AND b.attempted_at < %s))
           ORDER BY n_pubs DESC, r.id
           LIMIT %s""",
        (now - timedelta(hours=RETRY_FAILED_AFTER_HOURS), limit * 3),
    )
    seen: set[int] = set()
    out = []
    for row in rows:
        if row["researcher_id"] in seen:
            continue
        seen.add(row["researcher_id"])
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _record_attempt(researcher_id: int, url_id: int, content_hash: str | None,
                    outcome: str, now: datetime) -> None:
    execute_query(
        """INSERT INTO researcher_bio_backfill
               (researcher_id, url_id, content_hash, outcome, attempted_at, attempts)
           VALUES (%s, %s, %s, %s, %s, 1)
           ON DUPLICATE KEY UPDATE url_id = VALUES(url_id), content_hash = VALUES(content_hash),
               outcome = VALUES(outcome), attempted_at = VALUES(attempted_at),
               attempts = attempts + 1""",
        (researcher_id, url_id, content_hash, outcome, now),
    )


def run_bio_backfill_batch(
    *,
    max_calls: int | None = None,
    daily_limit: int | None = None,
    delay_seconds: float | None = None,
    max_backlog: int | None = None,
    use_lock: bool = True,
) -> dict:
    """Run one bounded backfill batch. Returns run statistics.

    ``max_backlog`` < 0 disables the extraction-backlog check (CLI use).
    """
    max_backlog = MAX_BACKLOG if max_backlog is None else max_backlog
    max_calls = BATCH_CALLS if max_calls is None else max_calls
    daily_limit = DAILY_LIMIT if daily_limit is None else daily_limit
    delay_seconds = DELAY_SECONDS if delay_seconds is None else delay_seconds
    stats = BioBackfillStats()

    lock_conn = _acquire_lock() if use_lock else None
    if use_lock and lock_conn is None:
        stats.stopped = "locked"
        logger.info("Bio backfill: another run holds the lock; skipping")
        return asdict(stats)
    _stop_event.clear()
    try:
        _run(stats, max_calls, daily_limit, delay_seconds, max_backlog)
    finally:
        if lock_conn is not None:
            _release_lock(lock_conn)
    logger.info("Bio backfill run: %s", asdict(stats))
    return asdict(stats)


def _run(stats: BioBackfillStats, max_calls: int, daily_limit: int, delay_seconds: float,
         max_backlog: int = -1) -> None:
    from backend.pipeline.description import request_description
    from backend.pipeline.html_fetcher import CONTENT_MAX_CHARS

    now = _utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stats.calls_today_before = count_backfill_calls_since(day_start)
    budget = min(max_calls, max(0, daily_limit - stats.calls_today_before))
    if budget <= 0:
        stats.stopped = "daily_limit"
        return
    if max_backlog >= 0:
        stats.extraction_backlog = count_extraction_backlog()
        if stats.extraction_backlog > max_backlog:
            stats.stopped = "extraction_backlog"
            logger.info("Bio backfill: %d pages wait for extraction (> %d); yielding",
                        stats.extraction_backlog, max_backlog)
            return

    candidates = get_backfill_candidates(budget, now)
    if not candidates:
        stats.stopped = "nothing_due"
        return

    consecutive_failures = 0
    for idx, cand in enumerate(candidates):
        if _stop_event.is_set():
            stats.stopped = "stop_requested"
            break
        rid, url_id, url = cand["researcher_id"], cand["url_id"], cand["url"]
        stats.considered += 1
        page = fetch_one(
            "SELECT content, content_hash FROM html_content WHERE url_id = %s", (url_id,),
        )
        text = (page or {}).get("content") or ""
        if not text.strip():
            _record_attempt(rid, url_id, cand["content_hash"], "no_bio", now)
            stats.no_bio += 1
            continue

        result = request_description(text[:CONTENT_MAX_CHARS], url)
        if result.retry_after is not None:
            # Quota/account refusal: hand the shared key back to extraction.
            stats.stopped = "rate_limited"
            logger.warning(
                "Bio backfill: provider refused the call (retry after %.0fs); "
                "stopping this run so extraction keeps the quota", result.retry_after,
            )
            break
        stats.llm_calls += 1

        if result.description:
            r_row = fetch_one(
                "SELECT position, affiliation, description FROM researchers WHERE id = %s",
                (rid,),
            )
            if r_row and r_row["description"] is None:
                append_researcher_snapshot(
                    rid, r_row["position"], r_row["affiliation"], result.description,
                    source_url=url,
                )
                stats.saved += 1
            else:
                stats.skipped_has_bio += 1
            outcome = "saved"
            consecutive_failures = 0
        elif result.failed:
            outcome = "failed"
            stats.failed += 1
            consecutive_failures += 1
        else:
            outcome = "rejected" if result.rejected else "no_bio"
            if result.rejected:
                stats.rejected += 1
            else:
                stats.no_bio += 1
            consecutive_failures = 0
        _record_attempt(rid, url_id, page.get("content_hash"), outcome, now)

        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            stats.stopped = "failures"
            logger.warning("Bio backfill: %d consecutive failures; stopping run",
                           consecutive_failures)
            break
        if idx + 1 < len(candidates) and _stop_event.wait(delay_seconds):
            stats.stopped = "stop_requested"
            break
