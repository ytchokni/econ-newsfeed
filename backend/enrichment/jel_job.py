"""Incremental JEL classification job (issue #271).

Classifies tracked researchers (those with an active URL) into top-level JEL
codes from their bio, paper titles/abstracts and OpenAlex topics. Each run:

1. picks researchers that are due (never checked, inputs changed, or a past
   failure is ready for retry) — see ``get_jel_classification_candidates``;
2. skips the LLM when the evidence hash is unchanged or there is no evidence;
3. calls the LLM for the rest, within its own budget:
   - at most ``JEL_CLASSIFICATION_BATCH_CALLS`` calls per run,
   - at most ``JEL_CLASSIFICATION_DAILY_LIMIT`` calls per UTC day (counted in
     ``llm_usage``, so CLI runs and scheduled runs share it),
   - ``JEL_CLASSIFICATION_DELAY_SECONDS`` between calls (one call in flight);
4. stops the whole run on the first quota/account refusal, so the extraction
   worker (same free-tier key) keeps priority: it gets the quota back at once,
   and JEL waits for its next scheduled run.

When the LLM finds nothing (or fails), codes derived from OpenAlex topics are
used as a fallback. A MySQL advisory lock keeps CLI and scheduled runs apart.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import mysql.connector

from backend.config import db_config
from backend.database.jel import (
    count_jel_llm_calls_since,
    get_jel_classification_candidates,
    get_jel_inputs_for_researchers,
    save_jel_state,
    save_researcher_jel_codes,
    touch_jel_state,
)
from backend.enrichment.jel_classifier import (
    ResearcherProfile,
    classify_profile,
    clean_bio,
    rank_topic_names,
)
from backend.enrichment.jel_enrichment import aggregate_jel_from_topics
from backend.llm.client import get_model

logger = logging.getLogger(__name__)

_LOCK_NAME = "econ_jel_classification"


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


BATCH_CALLS = _env_int("JEL_CLASSIFICATION_BATCH_CALLS", 40)
DAILY_LIMIT = _env_int("JEL_CLASSIFICATION_DAILY_LIMIT", 600)
DELAY_SECONDS = _env_float("JEL_CLASSIFICATION_DELAY_SECONDS", 20.0)
RECLASSIFY_AFTER_DAYS = _env_int("JEL_RECLASSIFY_AFTER_DAYS", 30)
CANDIDATE_LIMIT = 500  # researchers examined per run (most need no LLM call)
_MAX_CONSECUTIVE_FAILURES = 5

_stop_event = threading.Event()


def request_stop() -> None:
    """Ask a running batch to stop after its current call (scheduler shutdown)."""
    _stop_event.set()


@dataclass
class JelRunStats:
    considered: int = 0
    llm_calls: int = 0
    classified: int = 0
    topic_fallback: int = 0
    unchanged: int = 0
    no_evidence: int = 0
    failed: int = 0
    calls_today_before: int = 0
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
        logger.error("JEL classification: could not take advisory lock: %s", e)
    return None


def _release_lock(conn) -> None:
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
        cursor.fetchone()
        cursor.close()
    except Exception:
        logger.debug("JEL classification: lock release failed", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_author_topics(author_ids: list[str]) -> dict[str, list[dict]]:
    if not author_ids:
        return {}
    try:
        from backend.enrichment.openalex import fetch_author_topics_batch
        return fetch_author_topics_batch(author_ids)
    except Exception:
        logger.warning("JEL classification: OpenAlex author topics unavailable", exc_info=True)
        return {}


def run_jel_classification_batch(
    *,
    max_calls: int | None = None,
    daily_limit: int | None = None,
    delay_seconds: float | None = None,
    candidate_limit: int | None = None,
    reclassify_after_days: int | None = None,
    researcher_ids: list[int] | None = None,
    force: bool = False,
    use_lock: bool = True,
) -> dict:
    """Run one bounded classification batch. Returns run statistics."""
    max_calls = BATCH_CALLS if max_calls is None else max_calls
    daily_limit = DAILY_LIMIT if daily_limit is None else daily_limit
    delay_seconds = DELAY_SECONDS if delay_seconds is None else delay_seconds
    candidate_limit = CANDIDATE_LIMIT if candidate_limit is None else candidate_limit
    reclassify_after_days = (
        RECLASSIFY_AFTER_DAYS if reclassify_after_days is None else reclassify_after_days
    )
    stats = JelRunStats()

    lock_conn = _acquire_lock() if use_lock else None
    if use_lock and lock_conn is None:
        stats.stopped = "locked"
        logger.info("JEL classification: another run holds the lock; skipping")
        return asdict(stats)
    _stop_event.clear()
    try:
        _run(stats, max_calls, daily_limit, delay_seconds, candidate_limit,
             reclassify_after_days, researcher_ids, force)
    finally:
        if lock_conn is not None:
            _release_lock(lock_conn)
    logger.info("JEL classification run: %s", asdict(stats))
    return asdict(stats)


def _run(stats, max_calls, daily_limit, delay_seconds, candidate_limit,
         reclassify_after_days, researcher_ids, force) -> None:
    now = _utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stats.calls_today_before = count_jel_llm_calls_since(day_start)
    call_budget = min(max_calls, max(0, daily_limit - stats.calls_today_before))

    candidates = get_jel_classification_candidates(
        candidate_limit, now, reclassify_after_days,
        researcher_ids=researcher_ids, force=force,
    )
    if not candidates:
        stats.stopped = "nothing_due"
        return
    inputs = get_jel_inputs_for_researchers([c["id"] for c in candidates])
    model = get_model()

    # Pass 1: settle everything that needs no LLM call, queue the rest.
    queue: list[tuple[dict, ResearcherProfile, str, list[dict]]] = []
    for row in candidates:
        rid = row["id"]
        evidence = inputs.get(rid, {"papers": [], "topics": []})
        profile = ResearcherProfile(
            researcher_id=rid,
            first_name=row["first_name"],
            last_name=row["last_name"],
            bio=clean_bio(row.get("description")),
            papers=evidence["papers"],
            topics=rank_topic_names(evidence["topics"]),
        )
        digest = profile.input_hash()
        stats.considered += 1

        if not profile.classifiable:
            stats.no_evidence += 1
            save_jel_state(rid, input_hash=digest, source="none", used_bio=False,
                           model=None, attempts=0, checked_at=now)
            continue
        if not force and row.get("input_hash") == digest and not row.get("attempts"):
            stats.unchanged += 1
            touch_jel_state(rid, now)
            continue
        queue.append((row, profile, digest, list(evidence["topics"])))

    if not queue:
        return
    if call_budget <= 0:
        stats.stopped = "daily_limit"
        return
    if len(queue) > call_budget:
        stats.stopped = "call_budget"
    queue = queue[:call_budget]

    # OpenAlex author topics: one budgeted request per 50 researchers queued.
    author_topics = _fetch_author_topics(
        [row["openalex_author_id"] for row, *_ in queue if row.get("openalex_author_id")]
    )

    consecutive_failures = 0
    for idx, (row, profile, digest, topic_rows) in enumerate(queue):
        if _stop_event.is_set():
            stats.stopped = "stop_requested"
            break
        rid = row["id"]
        extra = author_topics.get(row.get("openalex_author_id") or "", [])
        if extra:
            topic_rows = topic_rows + extra
            profile.topics = rank_topic_names(topic_rows)

        outcome = classify_profile(profile)
        if outcome.retry_after is not None:
            # Quota/account refusal: give the quota back to extraction.
            stats.stopped = "rate_limited"
            logger.warning(
                "JEL classification: provider refused the call (retry after %.0fs); "
                "stopping this run so extraction keeps the quota", outcome.retry_after,
            )
            break
        stats.llm_calls += 1

        codes = outcome.codes
        source = "llm"
        if not codes:
            codes = aggregate_jel_from_topics(topic_rows)
            source = "topics" if codes else "none"
        if outcome.ok:
            consecutive_failures = 0
            attempts = 0
        else:
            stats.failed += 1
            consecutive_failures += 1
            attempts = int(row.get("attempts") or 0) + 1
            # A failed call keeps existing codes; topic codes only fill a gap.
            if row.get("has_codes"):
                codes = []

        if codes:
            save_researcher_jel_codes(rid, codes)
            if source == "llm":
                stats.classified += 1
            else:
                stats.topic_fallback += 1
        save_jel_state(rid, input_hash=digest, source=source,
                       used_bio=bool(profile.bio), model=model,
                       attempts=attempts, checked_at=now)

        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            stats.stopped = "failures"
            logger.warning("JEL classification: %d consecutive failures; stopping run",
                           consecutive_failures)
            break
        if idx + 1 < len(queue) and _stop_event.wait(delay_seconds):
            stats.stopped = "stop_requested"
            break
