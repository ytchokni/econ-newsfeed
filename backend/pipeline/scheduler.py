import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone

import mysql.connector
import mysql.connector.errors
from apscheduler.schedulers.background import BackgroundScheduler

from backend.database import (
    execute_query,
    fetch_one,
    get_unchecked_draft_urls,
    get_urls_needing_extraction,
    update_draft_url_status,
)
from backend.config import db_config
from backend.researcher import Researcher
from backend.pipeline.html_fetcher import HTMLFetcher
logger = logging.getLogger(__name__)

_LOCK_NAME = 'econ_newsfeed_scrape'
_SCHEDULER_LOCK_NAME = 'econ_newsfeed_scheduler'
_lock_conn = None  # connection holding the advisory lock for the duration of a scrape
_scheduler = None
_scheduler_lock_conn = None  # connection holding the advisory lock for the scheduler singleton

# Lock connections sit idle while the lock is held (nothing ever queries on
# them), so MySQL's default wait_timeout (8h) would kill them mid-scrape — the
# lock silently drops, the zombie cleanup falsely fails the live scrape row,
# and concurrent-scrape protection vanishes. A full scrape takes ~12h.
_LOCK_CONN_WAIT_TIMEOUT = 7 * 24 * 3600  # 7 days
_LOCK_KEEPALIVE_SECONDS = 1800  # ping idle lock connections every 30 min
_MAX_SCRAPE_DURATION_HOURS = 18  # force-release lock if scrape exceeds this

_keepalive_stop = threading.Event()
_keepalive_thread = None


def _acquire_db_lock() -> "mysql.connector.connection.MySQLConnection | None":
    """Try to acquire a MySQL advisory lock. Returns the connection if acquired, None otherwise."""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SET SESSION wait_timeout = %s", (_LOCK_CONN_WAIT_TIMEOUT,))
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


def _lock_keepalive_loop() -> None:
    """Ping idle lock connections on a fixed wall-clock interval.

    Both _lock_conn (scrape) and _scheduler_lock_conn sit idle for hours/days.
    Despite SET SESSION wait_timeout = 7 days, connections die at ~8h in
    production. A periodic conn.ping() resets the idle timer and keeps TCP alive.
    Runs as a daemon thread started alongside the scheduler.
    """
    while not _keepalive_stop.wait(_LOCK_KEEPALIVE_SECONDS):
        for name, conn in [("scrape", _lock_conn), ("scheduler", _scheduler_lock_conn)]:
            if conn is None:
                continue
            try:
                conn.ping(reconnect=False)
            except Exception as e:
                logger.warning("Lock keepalive ping failed (%s): %s", name, e)


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

EXTRACTION_WORKER_ENABLED = os.environ.get('EXTRACTION_WORKER_ENABLED', 'false').lower() == 'true'
DIGEST_ENABLED = os.environ.get('RESEND_API_KEY', '') != ''
_EXTRACTION_DELAY_SECONDS = float(os.environ.get('EXTRACTION_DELAY_SECONDS', '2'))
_EXTRACTION_IDLE_SECONDS = 300       # queue empty → re-poll every 5 min
_EXTRACTION_BACKOFF_THRESHOLD = 10   # consecutive failures before backing off
_EXTRACTION_BACKOFF_SECONDS = 600    # 10 min (rides out free-tier quota exhaustion)
_EXTRACTION_MAX_URL_FAILURES = 3     # poison-pill guard: skip URL until restart

_extraction_thread = None
_extraction_stop_event = threading.Event()


def create_scrape_log() -> int:
    """Create a new scrape_log entry and return its ID."""
    query = """
        INSERT INTO scrape_log (started_at, status)
        VALUES (%s, 'running')
    """
    return execute_query(query, (datetime.now(timezone.utc),))


def _update_progress(log_id: int, **counters) -> None:
    """Incrementally flush counter values to scrape_log so the dashboard updates live."""
    sets = ", ".join(f"{col} = %s" for col in counters)
    execute_query(
        f"UPDATE scrape_log SET {sets} WHERE id = %s",
        (*counters.values(), log_id),
    )


def update_scrape_log(log_id: int, status: str, urls_checked: int = 0, urls_changed: int = 0, pubs_extracted: int = 0, extraction_errors: int = 0, error_message: str | None = None) -> None:
    """Update an existing scrape_log entry with results."""
    # Aggregate token totals from llm_usage for this scrape run
    token_row = fetch_one(
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
    execute_query(query, (
        datetime.now(timezone.utc), status, urls_checked,
        urls_changed, pubs_extracted, extraction_errors, error_message,
        prompt_tokens_total, completion_tokens_total, log_id
    ))


def _cleanup_stale_scrape_logs() -> None:
    """Mark zombie 'running' scrape_log entries as 'failed'.

    The scrape advisory lock is the source of truth: if no connection holds
    it, every 'running' row is a leftover from a killed process (e.g. a
    deploy restarting the container mid-scrape). While the lock is held, the
    holder's row is the newest 'running' row — anything older is a zombie.
    The 5-minute grace window avoids racing a scrape that acquired the lock
    just after we checked it.

    Additionally, if ANY running entry exceeds _MAX_SCRAPE_DURATION_HOURS,
    the scrape thread likely hung (e.g. DNS resolution blocking). Force-
    release the advisory lock so the next scheduled run can proceed.
    """
    global _lock_conn

    _force_release_stale_lock()

    if is_scrape_running():
        where = """status = 'running'
             AND id < (SELECT mx FROM (SELECT MAX(id) AS mx FROM scrape_log
                                       WHERE status = 'running') AS newest)"""
    else:
        where = """status = 'running'
             AND started_at < DATE_SUB(NOW(), INTERVAL 5 MINUTE)"""
    affected = execute_query(
        f"""UPDATE scrape_log
           SET finished_at = NOW(), status = 'failed',
               error_message = 'Zombie running entry — process died mid-scrape'
           WHERE {where}"""
    )
    if affected:
        logger.info("Cleaned up %d zombie scrape_log entries", affected)


def _force_release_stale_lock() -> None:
    """Force-release the scrape advisory lock if the current scrape has exceeded
    _MAX_SCRAPE_DURATION_HOURS. Covers the case where the scrape thread hangs
    (e.g. blocking DNS) but the lock connection survives."""
    global _lock_conn
    if _lock_conn is None:
        return
    row = fetch_one(
        """SELECT id FROM scrape_log
           WHERE status = 'running'
             AND started_at < DATE_SUB(NOW(), INTERVAL %s HOUR)
           ORDER BY id DESC LIMIT 1""",
        (_MAX_SCRAPE_DURATION_HOURS,),
    )
    if not row:
        return
    logger.warning(
        "Scrape #%d exceeded %dh — force-releasing advisory lock",
        row['id'], _MAX_SCRAPE_DURATION_HOURS,
    )
    try:
        _release_db_lock(_lock_conn)
    except Exception as e:
        logger.error("Error force-releasing lock: %s", e)
    _lock_conn = None
    execute_query(
        """UPDATE scrape_log
           SET finished_at = NOW(), status = 'failed',
               error_message = %s
           WHERE id = %s AND status = 'running'""",
        (f"Force-killed: scrape exceeded {_MAX_SCRAPE_DURATION_HOURS}h limit", row['id']),
    )


_DRAFT_VALIDATION_BUDGET_SECONDS = 300  # 5-minute time budget
_DRAFT_VALIDATION_DELAY = 0.1  # 100ms between requests


def _validate_draft_urls() -> None:
    """Validate papers with unchecked draft URLs (rate-limited, time-budgeted)."""
    unchecked = get_unchecked_draft_urls()
    if not unchecked:
        return
    logger.info(f"Validating {len(unchecked)} unchecked draft URLs")
    start = time.time()
    validated = 0
    for row in unchecked:
        paper_id = row['id']
        draft_url = row['draft_url']
        if time.time() - start > _DRAFT_VALIDATION_BUDGET_SECONDS:
            logger.info(f"Draft URL validation time budget exceeded after {validated} URLs")
            break
        try:
            status = HTMLFetcher.validate_draft_url(draft_url)
            update_draft_url_status(paper_id, status)
            logger.info(f"Draft URL for paper {paper_id}: {status}")
            validated += 1
            time.sleep(_DRAFT_VALIDATION_DELAY)
        except Exception as e:
            logger.error(f"Error validating draft URL for paper {paper_id}: {e}")


_TRANSIENT_MYSQL_ERRORS = (
    mysql.connector.errors.OperationalError,
    mysql.connector.errors.InterfaceError,
)


def _with_db_retry(fn, *args, max_retries: int = 3, **kwargs):
    """Call *fn* and retry on transient MySQL connection errors.

    Retries up to *max_retries* times with exponential backoff (2s, 4s, 8s).
    Only catches OperationalError and InterfaceError — all other exceptions
    propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except _TRANSIENT_MYSQL_ERRORS:
            if attempt == max_retries:
                raise
            delay = 2 ** (attempt + 1)  # 2, 4, 8
            logger.warning(
                "Transient MySQL error in %s (attempt %d/%d), retrying in %ds",
                fn.__name__ if hasattr(fn, '__name__') else str(fn),
                attempt + 1, max_retries, delay,
                exc_info=True,
            )
            time.sleep(delay)


def run_scrape_job() -> None:
    """Fetch HTML for all researcher URLs (change detection via content hash).

    Extraction is owned by the continuous extraction worker — this job only
    downloads pages and validates draft URLs.
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

        scrape_start = time.time()
        scrape_deadline = scrape_start + _MAX_SCRAPE_DURATION_HOURS * 3600
        for url_row in urls:
            if time.time() > scrape_deadline:
                logger.warning("Scrape deadline reached (%dh), stopping at %d/%d URLs",
                               _MAX_SCRAPE_DURATION_HOURS, urls_checked, len(urls))
                break

            url_id = url_row['id']
            researcher_id = url_row['researcher_id']
            url = url_row['url']
            urls_checked += 1

            try:
                t0 = time.time()
                changed = _with_db_retry(HTMLFetcher.fetch_and_save_if_changed, url_id, url, researcher_id)
                fetch_ms = (time.time() - t0) * 1000
                logger.info(f"[{urls_checked}/{len(urls)}] fetch {url} — {fetch_ms:.0f}ms (changed={changed})")

                if changed:
                    urls_changed += 1

                if urls_checked % 10 == 0:
                    _with_db_retry(_update_progress, log_id, urls_checked=urls_checked, urls_changed=urls_changed)

            except _TRANSIENT_MYSQL_ERRORS as e:
                logger.error("MySQL connection error fetching URL %s (id=%s) after retries: %s", url, url_id, e)
                continue
            except Exception as e:
                logger.error("Error fetching URL %s (id=%s): %s", url, url_id, e)
                continue

        _update_progress(log_id, urls_checked=urls_checked, urls_changed=urls_changed)
        fetch_phase_s = time.time() - scrape_start
        logger.info(f"Fetch phase done: {fetch_phase_s:.1f}s — {urls_checked} checked, {urls_changed} changed")

        t0 = time.time()
        _validate_draft_urls()
        validate_s = time.time() - t0
        logger.info(f"Draft URL validation: {validate_s:.1f}s")

        total_s = time.time() - scrape_start
        update_scrape_log(log_id, "completed", urls_checked, urls_changed)
        logger.info(f"Scrape completed: {urls_checked} checked, {urls_changed} changed — {total_s:.1f}s total")

    except Exception as e:
        logger.error("Scrape job failed: %s: %s", type(e).__name__, e)
        if log_id:
            update_scrape_log(log_id, "failed", error_message=f"{type(e).__name__}: {e}")
    finally:
        _release_db_lock(lock_conn)
        _lock_conn = None

    t0 = time.time()
    try:
        from backend.enrichment.paper_merge import merge_duplicate_papers
        merge_duplicate_papers()
    except Exception as e:
        logger.error("Paper merge failed: %s: %s", type(e).__name__, e)
    merge_s = time.time() - t0
    logger.info(f"Paper merge: {merge_s:.1f}s")


def _start_lock_keepalive() -> None:
    global _keepalive_thread
    if _keepalive_thread is not None and _keepalive_thread.is_alive():
        return
    _keepalive_stop.clear()
    _keepalive_thread = threading.Thread(
        target=_lock_keepalive_loop, name="lock-keepalive", daemon=True,
    )
    _keepalive_thread.start()
    logger.info("Lock keepalive thread started (interval=%ds)", _LOCK_KEEPALIVE_SECONDS)


def _stop_lock_keepalive() -> None:
    global _keepalive_thread
    if _keepalive_thread is None:
        return
    _keepalive_stop.set()
    _keepalive_thread.join(timeout=5)
    _keepalive_thread = None


def _handle_sigterm(signum: int, frame: object) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown in cloud environments.
    Waits for any running scrape job to complete before exiting."""
    logger.info("Received signal %s, shutting down scheduler gracefully...", signum)
    shutdown_scheduler()


def _enrichment_worker_loop() -> None:
    """Continuously enrich unenriched papers until stop event is set."""
    from backend.enrichment.openalex import enrich_new_publications

    logger.info("Enrichment worker started")
    consecutive_failures = 0
    enriched = 0

    while not _enrichment_stop_event.is_set():
        try:
            enriched = enrich_new_publications(limit=_ENRICHMENT_BATCH_SIZE)
            consecutive_failures = 0
            if enriched:
                logger.info("Enrichment cycle: %d papers enriched", enriched)
            else:
                logger.info("Enrichment cycle: no papers to enrich, sleeping %ds", _ENRICHMENT_IDLE_SECONDS)
        except Exception as e:
            enriched = 0
            consecutive_failures += 1
            logger.error("Enrichment cycle failed (%d consecutive): %s: %s",
                         consecutive_failures, type(e).__name__, e)

        if consecutive_failures >= _ENRICHMENT_BACKOFF_THRESHOLD:
            logger.warning("Enrichment backing off for %ds after %d consecutive failures",
                           _ENRICHMENT_BACKOFF_SECONDS, consecutive_failures)
            _enrichment_stop_event.wait(_ENRICHMENT_BACKOFF_SECONDS)
        elif enriched == 0:
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


def _extraction_worker_loop() -> None:
    """Continuously extract publications from changed pages until stopped.

    Free-tier Gemma pacing (measured 2026-06-09, see spec): calls take
    45-190s on real pages so the worker is latency-bound; the fixed delay
    only guards runs of tiny pages against the 30 RPM cap.
    """
    from backend.pipeline.extraction import extract_one_url

    logger.info("Extraction worker started")
    url_failures: dict[int, int] = {}
    consecutive_failures = 0
    processed = 0
    pubs_total = 0

    while not _extraction_stop_event.is_set():
        try:
            queue = get_urls_needing_extraction()
        except Exception as e:
            logger.error("Extraction worker queue query failed: %s: %s", type(e).__name__, e)
            _extraction_stop_event.wait(_EXTRACTION_IDLE_SECONDS)
            continue

        pending = [r for r in queue if url_failures.get(r['id'], 0) < _EXTRACTION_MAX_URL_FAILURES]
        if not pending:
            _extraction_stop_event.wait(_EXTRACTION_IDLE_SECONDS)
            continue

        logger.info("Extraction worker: %d URLs pending (%d skipped as poison pills)",
                    len(pending), len(queue) - len(pending))

        for row in pending:
            if _extraction_stop_event.is_set():
                break
            url_id = row['id']
            try:
                outcome = extract_one_url(row)
            except Exception as e:
                logger.error("Extraction worker error on %s (id=%s): %s: %s",
                             row['url'], url_id, type(e).__name__, e)
                outcome = None

            processed += 1
            if outcome is not None and outcome.ok:
                consecutive_failures = 0
                url_failures.pop(url_id, None)
                pubs_total += outcome.pubs_count
            elif outcome is not None and outcome.retry_after is not None:
                logger.warning("Extraction worker: rate limited, sleeping %.0fs (retry_after)",
                               outcome.retry_after)
                _extraction_stop_event.wait(outcome.retry_after)
                continue
            else:
                consecutive_failures += 1
                url_failures[url_id] = url_failures.get(url_id, 0) + 1
                if url_failures[url_id] >= _EXTRACTION_MAX_URL_FAILURES:
                    logger.warning("Extraction worker: skipping url_id=%s (%s) after %d failures "
                                   "until next restart", url_id, row['url'], url_failures[url_id])

            if processed % 25 == 0:
                logger.info("Extraction worker progress: %d processed, %d pubs total", processed, pubs_total)

            if consecutive_failures >= _EXTRACTION_BACKOFF_THRESHOLD:
                logger.warning("Extraction worker backing off %ds after %d consecutive failures "
                               "(likely quota exhausted)", _EXTRACTION_BACKOFF_SECONDS, consecutive_failures)
                _extraction_stop_event.wait(_EXTRACTION_BACKOFF_SECONDS)
            else:
                _extraction_stop_event.wait(_EXTRACTION_DELAY_SECONDS)

    logger.info("Extraction worker stopped (%d processed, %d pubs)", processed, pubs_total)


def start_extraction_worker() -> None:
    """Start the extraction background worker thread."""
    global _extraction_thread
    if _extraction_thread is not None and _extraction_thread.is_alive():
        logger.warning("Extraction worker already running")
        return

    _extraction_stop_event.clear()
    _extraction_thread = threading.Thread(
        target=_extraction_worker_loop,
        name="extraction-worker",
        daemon=True,
    )
    _extraction_thread.start()
    logger.info("Extraction worker thread started")


def stop_extraction_worker() -> None:
    """Stop the extraction worker gracefully."""
    global _extraction_thread
    if _extraction_thread is None:
        return
    _extraction_stop_event.set()
    _extraction_thread.join(timeout=30)
    _extraction_thread = None
    logger.info("Extraction worker thread stopped")


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
        cursor.execute("SET SESSION wait_timeout = %s", (_LOCK_CONN_WAIT_TIMEOUT,))
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
    _scheduler.add_job(
        _cleanup_stale_scrape_logs,
        'interval',
        hours=1,
        id='stale_scrape_cleanup',
    )
    _scheduler.start()
    logger.info(f"Scheduler started: scraping every {SCRAPE_INTERVAL_HOURS} hours")

    _start_lock_keepalive()

    if SCRAPE_ON_STARTUP:
        logger.info("SCRAPE_ON_STARTUP is true, triggering immediate scrape in background")
        threading.Thread(target=run_scrape_job, name="startup-scrape").start()

    if ENRICHMENT_WORKER_ENABLED:
        start_enrichment_worker()

    if EXTRACTION_WORKER_ENABLED:
        start_extraction_worker()

    if DIGEST_ENABLED:
        from backend.digest import run_weekly_digest
        _scheduler.add_job(
            run_weekly_digest,
            'cron',
            day_of_week='mon',
            hour=8,
            minute=0,
            id='weekly_digest',
        )
        logger.info("Weekly digest job scheduled for Mondays 8:00 UTC")


def shutdown_scheduler() -> None:
    """Shut down the scheduler gracefully, waiting for any running job to complete."""
    global _scheduler, _scheduler_lock_conn
    _stop_lock_keepalive()
    stop_enrichment_worker()
    stop_extraction_worker()
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
