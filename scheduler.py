import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone

import mysql.connector
from apscheduler.schedulers.background import BackgroundScheduler

from database import Database
from db_config import db_config
from researcher import Researcher
from html_fetcher import HTMLFetcher
from publication import Publication, reconcile_title_renames, append_snapshots_for_pubs
from link_extractor import match_and_save_paper_links
from openalex import enrich_new_publications

logger = logging.getLogger(__name__)

_LOCK_NAME = 'econ_newsfeed_scrape'
_SCHEDULER_LOCK_NAME = 'econ_newsfeed_scheduler'
_lock_conn = None  # connection holding the advisory lock for the duration of a scrape
_scheduler = None
_scheduler_lock_conn = None  # connection holding the advisory lock for the scheduler singleton


def _acquire_db_lock() -> "mysql.connector.connection.MySQLConnection | None":
    """Try to acquire a MySQL advisory lock. Returns the connection if acquired, None otherwise."""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT GET_LOCK(%s, 0)", (_LOCK_NAME,))
        result = cursor.fetchone()
        cursor.close()
        if result and result[0] == 1:
            return conn
        conn.close()
        return None
    except Exception as e:
        logger.error(f"Failed to acquire DB advisory lock: {e}")
        return None


def _release_db_lock(conn: "mysql.connector.connection.MySQLConnection") -> None:
    """Release the MySQL advisory lock and close the connection."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
        cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to release DB advisory lock: {e}")


def is_scrape_running() -> bool:
    """Return True if another worker currently holds the scrape advisory lock."""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT IS_USED_LOCK(%s)", (_LOCK_NAME,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result is not None and result[0] is not None
    except Exception as e:
        logger.error(f"Failed to check DB advisory lock: {e}")
        return False

SCRAPE_INTERVAL_HOURS = int(os.environ.get('SCRAPE_INTERVAL_HOURS', '24'))
SCRAPE_ON_STARTUP = os.environ.get('SCRAPE_ON_STARTUP', 'false').lower() == 'true'
ENRICHMENT_WORKER_ENABLED = os.environ.get('ENRICHMENT_WORKER_ENABLED', 'false').lower() == 'true'
_ENRICHMENT_IDLE_SECONDS = 300  # 5 minutes
_ENRICHMENT_BATCH_SIZE = 50
_ENRICHMENT_BACKOFF_THRESHOLD = 5
_ENRICHMENT_BACKOFF_SECONDS = 600  # 10 minutes

_enrichment_thread = None
_enrichment_stop_event = threading.Event()


def create_scrape_log() -> int:
    """Create a new scrape_log entry and return its ID."""
    query = """
        INSERT INTO scrape_log (started_at, status)
        VALUES (%s, 'running')
    """
    return Database.execute_query(query, (datetime.now(timezone.utc),))


def update_scrape_log(log_id: int, status: str, urls_checked: int = 0, urls_changed: int = 0, pubs_extracted: int = 0, extraction_errors: int = 0, error_message: str | None = None) -> None:
    """Update an existing scrape_log entry with results."""
    # Aggregate token totals from llm_usage for this scrape run
    token_row = Database.fetch_one(
        """SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_total,
                  COALESCE(SUM(completion_tokens), 0) AS completion_total
           FROM llm_usage WHERE scrape_log_id = %s""",
        (log_id,),
    )
    prompt_tokens_total = token_row['prompt_total'] if token_row else 0
    completion_tokens_total = token_row['completion_total'] if token_row else 0

    query = """
        UPDATE scrape_log
        SET finished_at = %s, status = %s, urls_checked = %s,
            urls_changed = %s, pubs_extracted = %s, extraction_errors = %s,
            error_message = %s,
            prompt_tokens_total = %s, completion_tokens_total = %s
        WHERE id = %s
    """
    Database.execute_query(query, (
        datetime.now(timezone.utc), status, urls_checked,
        urls_changed, pubs_extracted, extraction_errors, error_message,
        prompt_tokens_total, completion_tokens_total, log_id
    ))


_STALE_SCRAPE_HOURS = 24


def _cleanup_stale_scrape_logs() -> None:
    """Mark scrape_log entries stuck in 'running' for >24h as 'failed'."""
    affected = Database.execute_query(
        """UPDATE scrape_log
           SET finished_at = NOW(), status = 'failed',
               error_message = 'Stale running entry — cleaned up on scheduler start'
           WHERE status = 'running'
             AND started_at < DATE_SUB(NOW(), INTERVAL %s HOUR)""",
        (_STALE_SCRAPE_HOURS,),
    )
    if affected:
        logger.info("Cleaned up %d stale scrape_log entries", affected)


_DRAFT_VALIDATION_BUDGET_SECONDS = 300  # 5-minute time budget
_DRAFT_VALIDATION_DELAY = 0.1  # 100ms between requests


def _validate_draft_urls() -> None:
    """Validate papers with unchecked draft URLs (rate-limited, time-budgeted)."""
    unchecked = Database.get_unchecked_draft_urls()
    if not unchecked:
        return
    logger.info(f"Validating {len(unchecked)} unchecked draft URLs")
    start = time.time()
    validated = 0
    for paper_id, draft_url in unchecked:
        if time.time() - start > _DRAFT_VALIDATION_BUDGET_SECONDS:
            logger.info(f"Draft URL validation time budget exceeded after {validated} URLs")
            break
        try:
            status = HTMLFetcher.validate_draft_url(draft_url)
            Database.update_draft_url_status(paper_id, status)
            logger.info(f"Draft URL for paper {paper_id}: {status}")
            validated += 1
            time.sleep(_DRAFT_VALIDATION_DELAY)
        except Exception as e:
            logger.error(f"Error validating draft URL for paper {paper_id}: {e}")


_EXTRACTION_CIRCUIT_BREAKER_THRESHOLD = 10


def run_scrape_job() -> None:
    """Orchestrates a full scraping cycle: fetch all HTML first, then extract.

    Fetch always completes. Extraction circuit-breaks after
    _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD consecutive failures (e.g. quota exhausted).
    """
    global _lock_conn
    lock_conn = _acquire_db_lock()
    if lock_conn is None:
        logger.warning("Scrape already in progress, skipping")
        return
    _lock_conn = lock_conn

    HTMLFetcher._robots_cache.clear()

    log_id = None
    try:
        log_id = create_scrape_log()
        urls = Researcher.get_all_researcher_urls()
        urls_checked = 0
        urls_changed = 0
        pubs_extracted = 0
        extraction_errors = 0

        # ── PHASE 1: Fetch all HTML ──────────────────────────────────
        scrape_start = time.time()
        changed_urls = []

        for url_row in urls:
            url_id = url_row['id']
            researcher_id = url_row['researcher_id']
            url = url_row['url']
            page_type = url_row['page_type']
            urls_checked += 1

            try:
                old_text = HTMLFetcher.get_previous_text(url_id)
                is_first_scrape = old_text is None

                t0 = time.time()
                changed = HTMLFetcher.fetch_and_save_if_changed(url_id, url, researcher_id)
                fetch_ms = (time.time() - t0) * 1000
                logger.info(f"[{urls_checked}/{len(urls)}] fetch {url} — {fetch_ms:.0f}ms (changed={changed})")

                if changed:
                    urls_changed += 1
                    changed_urls.append({
                        **url_row,
                        'old_text': old_text,
                        'is_first_scrape': is_first_scrape,
                    })

            except Exception as e:
                logger.error("Error fetching URL %s (id=%s): %s", url, url_id, e)
                continue

        fetch_phase_s = time.time() - scrape_start
        logger.info(f"Fetch phase done: {fetch_phase_s:.1f}s — {urls_checked} checked, {urls_changed} changed")

        # ── PHASE 2: Extract publications from changed URLs ──────────
        extract_start = time.time()
        consecutive_failures = 0
        circuit_broken = False

        for idx, entry in enumerate(changed_urls):
            url_id = entry['id']
            researcher_id = entry['researcher_id']
            url = entry['url']
            page_type = entry['page_type']
            old_text = entry.pop('old_text')
            is_first_scrape = entry.pop('is_first_scrape')

            if circuit_broken:
                break

            try:
                new_text = HTMLFetcher.get_latest_text(url_id)
                extraction_text = HTMLFetcher.compute_diff(old_text, new_text) if old_text else new_text

                if extraction_text:
                    t0 = time.time()
                    pubs = Publication.extract_publications(extraction_text, url, scrape_log_id=log_id)
                    extract_ms = (time.time() - t0) * 1000
                    logger.info(f"  LLM extract {url} — {extract_ms:.0f}ms, {len(pubs)} pubs")

                    if pubs:
                        consecutive_failures = 0

                        t0 = time.time()
                        Publication.save_publications(url, pubs, is_seed=is_first_scrape)
                        save_ms = (time.time() - t0) * 1000
                        logger.info(f"  save_publications — {save_ms:.0f}ms")

                        t0_recon = time.time()
                        reconcile_title_renames(url, pubs)
                        recon_ms = (time.time() - t0_recon) * 1000
                        logger.info(f"  title reconciliation — {recon_ms:.0f}ms")

                        pubs_extracted += len(pubs)

                        t0 = time.time()
                        match_and_save_paper_links(url_id, pubs)
                        links_ms = (time.time() - t0) * 1000
                        logger.info(f"  paper links — {links_ms:.0f}ms")

                        t0 = time.time()
                        append_snapshots_for_pubs(pubs, url)
                        snapshot_ms = (time.time() - t0) * 1000
                        logger.info(f"  paper snapshots — {snapshot_ms:.0f}ms")
                    else:
                        consecutive_failures += 1
                        extraction_errors += 1
                        if consecutive_failures >= _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD:
                            remaining = len(changed_urls) - idx - 1
                            logger.warning(
                                "Circuit breaker: %d consecutive extraction failures — "
                                "stopping extraction (likely LLM quota exhausted). "
                                "Remaining %d changed URLs will be extracted next run.",
                                consecutive_failures, remaining,
                            )
                            circuit_broken = True

                if page_type == "HOME" and not circuit_broken and new_text:
                    t0 = time.time()
                    description = HTMLFetcher.extract_description(new_text, url, scrape_log_id=log_id)
                    desc_ms = (time.time() - t0) * 1000
                    logger.info(f"  description extract — {desc_ms:.0f}ms (found={description is not None})")
                    if description:
                        r_row = Database.fetch_one(
                            "SELECT position, affiliation FROM researchers WHERE id = %s",
                            (researcher_id,),
                        )
                        position = r_row['position'] if r_row else None
                        affiliation = r_row['affiliation'] if r_row else None
                        Database.append_researcher_snapshot(
                            researcher_id, position, affiliation, description, source_url=url
                        )

            except Exception as e:
                logger.error("Error extracting URL %s (id=%s): %s", url, url_id, e)
                extraction_errors += 1
                consecutive_failures += 1
                if consecutive_failures >= _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD:
                    remaining = len(changed_urls) - idx - 1
                    logger.warning(
                        "Circuit breaker: %d consecutive extraction failures — "
                        "stopping extraction. Remaining %d changed URLs will be extracted next run.",
                        consecutive_failures, remaining,
                    )
                    circuit_broken = True
                continue

        extract_phase_s = time.time() - extract_start
        logger.info(f"Extract phase done: {extract_phase_s:.1f}s — {pubs_extracted} pubs, {extraction_errors} errors")

        t0 = time.time()
        _validate_draft_urls()
        validate_s = time.time() - t0
        logger.info(f"Draft URL validation: {validate_s:.1f}s")

        error_msg = None
        if circuit_broken:
            error_msg = f"Extraction circuit-breaker tripped after {_EXTRACTION_CIRCUIT_BREAKER_THRESHOLD} consecutive failures"

        total_s = time.time() - scrape_start
        update_scrape_log(log_id, "completed", urls_checked, urls_changed, pubs_extracted, extraction_errors, error_msg)
        logger.info(f"Scrape completed: {urls_checked} checked, {urls_changed} changed, {pubs_extracted} extracted, {extraction_errors} errors — {total_s:.1f}s total")

    except Exception as e:
        logger.error("Scrape job failed: %s: %s", type(e).__name__, e)
        if log_id:
            update_scrape_log(log_id, "failed", error_message=f"{type(e).__name__}: {e}")
    finally:
        _release_db_lock(lock_conn)
        _lock_conn = None

    t0 = time.time()
    try:
        from paper_merge import merge_duplicate_papers
        merge_duplicate_papers()
    except Exception as e:
        logger.error("Paper merge failed: %s: %s", type(e).__name__, e)
    merge_s = time.time() - t0
    logger.info(f"Paper merge: {merge_s:.1f}s")


def _handle_sigterm(signum: int, frame: object) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown in cloud environments.
    Waits for any running scrape job to complete before exiting."""
    logger.info("Received signal %s, shutting down scheduler gracefully...", signum)
    shutdown_scheduler()


def _enrichment_worker_loop() -> None:
    """Continuously enrich unenriched papers until stop event is set."""
    logger.info("Enrichment worker started")
    consecutive_failures = 0

    while not _enrichment_stop_event.is_set():
        try:
            enriched = enrich_new_publications(limit=_ENRICHMENT_BATCH_SIZE)
            consecutive_failures = 0
            if enriched:
                logger.info("Enrichment cycle: %d papers enriched", enriched)
            else:
                logger.info("Enrichment cycle: no papers to enrich, sleeping %ds", _ENRICHMENT_IDLE_SECONDS)
        except Exception as e:
            consecutive_failures += 1
            logger.error("Enrichment cycle failed (%d consecutive): %s: %s",
                         consecutive_failures, type(e).__name__, e)

        if consecutive_failures >= _ENRICHMENT_BACKOFF_THRESHOLD:
            logger.warning("Enrichment backing off for %ds after %d consecutive failures",
                           _ENRICHMENT_BACKOFF_SECONDS, consecutive_failures)
            _enrichment_stop_event.wait(_ENRICHMENT_BACKOFF_SECONDS)
        else:
            _enrichment_stop_event.wait(_ENRICHMENT_IDLE_SECONDS)

    logger.info("Enrichment worker stopped")


def start_enrichment_worker() -> None:
    """Start the enrichment background worker thread."""
    global _enrichment_thread
    if _enrichment_thread is not None and _enrichment_thread.is_alive():
        logger.warning("Enrichment worker already running")
        return

    _enrichment_stop_event.clear()
    _enrichment_thread = threading.Thread(
        target=_enrichment_worker_loop,
        name="enrichment-worker",
        daemon=True,
    )
    _enrichment_thread.start()
    logger.info("Enrichment worker thread started")


def stop_enrichment_worker() -> None:
    """Stop the enrichment worker gracefully."""
    global _enrichment_thread
    if _enrichment_thread is None:
        return
    _enrichment_stop_event.set()
    _enrichment_thread.join(timeout=30)
    _enrichment_thread = None
    logger.info("Enrichment worker thread stopped")


def start_scheduler() -> None:
    """Start the APScheduler BackgroundScheduler with the configured interval.

    Uses a MySQL advisory lock to ensure only one Gunicorn worker runs the
    scheduler, preventing duplicate scrape triggers from multiple workers.
    """
    global _scheduler, _scheduler_lock_conn
    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return

    # Acquire a non-blocking advisory lock so only one worker runs the scheduler.
    # The lock is held for the lifetime of the connection; if the worker dies,
    # the connection drops and another worker can acquire it.
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT GET_LOCK(%s, 0)", (_SCHEDULER_LOCK_NAME,))
        result = cursor.fetchone()
        cursor.close()
        if not (result and result[0] == 1):
            conn.close()
            logger.info("Another worker owns the scheduler lock — this worker handles API requests only")
            return
        _scheduler_lock_conn = conn  # keep connection alive to hold the lock
    except Exception as e:
        logger.warning("Could not acquire scheduler lock: %s — skipping scheduler", e)
        return

    _cleanup_stale_scrape_logs()

    # Register signal handlers so cloud container SIGTERM completes the current job
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        run_scrape_job,
        'interval',
        hours=SCRAPE_INTERVAL_HOURS,
        id='scrape_job',
    )
    _scheduler.start()
    logger.info(f"Scheduler started: scraping every {SCRAPE_INTERVAL_HOURS} hours")

    if SCRAPE_ON_STARTUP:
        logger.info("SCRAPE_ON_STARTUP is true, triggering immediate scrape in background")
        threading.Thread(target=run_scrape_job, name="startup-scrape").start()

    if ENRICHMENT_WORKER_ENABLED:
        start_enrichment_worker()


def shutdown_scheduler() -> None:
    """Shut down the scheduler gracefully, waiting for any running job to complete."""
    global _scheduler, _scheduler_lock_conn
    stop_enrichment_worker()
    if _scheduler is not None:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("Scheduler shut down")
    if _scheduler_lock_conn is not None:
        try:
            cursor = _scheduler_lock_conn.cursor()
            cursor.execute("SELECT RELEASE_LOCK(%s)", (_SCHEDULER_LOCK_NAME,))
            cursor.fetchone()
            cursor.close()
            _scheduler_lock_conn.close()
        except Exception:
            pass
        _scheduler_lock_conn = None
