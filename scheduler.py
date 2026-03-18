import logging
import os
import time
from datetime import datetime, timezone

import mysql.connector
from apscheduler.schedulers.background import BackgroundScheduler

from database import Database
from db_config import db_config
from researcher import Researcher
from html_fetcher import HTMLFetcher
from publication import Publication

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_LOCK_NAME = 'econ_newsfeed_scrape'
_lock_conn = None  # connection holding the advisory lock for the duration of a scrape
_scheduler = None


def _acquire_db_lock():
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


def _release_db_lock(conn):
    """Release the MySQL advisory lock and close the connection."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
        cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to release DB advisory lock: {e}")


def is_scrape_running():
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


def create_scrape_log():
    """Create a new scrape_log entry and return its ID."""
    query = """
        INSERT INTO scrape_log (started_at, status)
        VALUES (%s, 'running')
    """
    return Database.execute_query(query, (datetime.now(timezone.utc),))


def update_scrape_log(log_id, status, urls_checked=0, urls_changed=0, pubs_extracted=0, error_message=None):
    """Update an existing scrape_log entry with results."""
    query = """
        UPDATE scrape_log
        SET finished_at = %s, status = %s, urls_checked = %s,
            urls_changed = %s, pubs_extracted = %s, error_message = %s
        WHERE id = %s
    """
    Database.execute_query(query, (
        datetime.now(timezone.utc), status, urls_checked,
        urls_changed, pubs_extracted, error_message, log_id
    ))


_DRAFT_VALIDATION_BUDGET_SECONDS = 300  # 5-minute time budget
_DRAFT_VALIDATION_DELAY = 0.1  # 100ms between requests


def _validate_draft_urls():
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


def run_scrape_job():
    """Orchestrates a full scraping cycle. Skips if another scrape is running."""
    global _lock_conn
    lock_conn = _acquire_db_lock()
    if lock_conn is None:
        logger.warning("Scrape already in progress, skipping")
        return
    _lock_conn = lock_conn

    log_id = None
    try:
        log_id = create_scrape_log()
        urls = Researcher.get_all_researcher_urls()
        urls_checked = 0
        urls_changed = 0
        pubs_extracted = 0

        scrape_start = time.time()

        for url_id, researcher_id, url, page_type in urls:
            urls_checked += 1
            url_start = time.time()

            try:
                # Get old text before fetch overwrites it (upsert)
                old_text = HTMLFetcher.get_previous_text(url_id)

                t0 = time.time()
                changed = HTMLFetcher.fetch_and_save_if_changed(url_id, url, researcher_id)
                fetch_ms = (time.time() - t0) * 1000
                logger.info(f"[{urls_checked}/{len(urls)}] fetch {url} — {fetch_ms:.0f}ms (changed={changed})")

                if changed and page_type in ("PUB", "WP"):
                    urls_changed += 1
                    new_text = HTMLFetcher.get_latest_text(url_id)

                    # Use diff if old content exists, otherwise full text
                    extraction_text = HTMLFetcher.compute_diff(old_text, new_text) if old_text else new_text

                    if extraction_text:
                        t0 = time.time()
                        pubs = Publication.extract_publications(extraction_text, url)
                        extract_ms = (time.time() - t0) * 1000
                        logger.info(f"  LLM extract — {extract_ms:.0f}ms, {len(pubs)} pubs")

                        if pubs:
                            t0 = time.time()
                            Publication.save_publications(url, pubs)
                            save_ms = (time.time() - t0) * 1000
                            logger.info(f"  save_publications — {save_ms:.0f}ms")
                            pubs_extracted += len(pubs)

                            # Append paper snapshots for versioning
                            t0 = time.time()
                            for pub in pubs:
                                title_hash = Database.compute_title_hash(pub['title'])
                                paper_row = Database.fetch_one(
                                    "SELECT id FROM papers WHERE title_hash = %s", (title_hash,)
                                )
                                if paper_row:
                                    Database.append_paper_snapshot(
                                        paper_id=paper_row[0],
                                        status=pub.get('status'),
                                        venue=pub.get('venue'),
                                        abstract=pub.get('abstract'),
                                        draft_url=pub.get('draft_url'),
                                        year=pub.get('year'),
                                        source_url=url,
                                    )
                            snapshot_ms = (time.time() - t0) * 1000
                            logger.info(f"  paper snapshots — {snapshot_ms:.0f}ms")

                # Extract description from HOME pages using append-only versioning
                # Only re-extract when content actually changed to avoid unnecessary LLM calls
                if page_type == "HOME" and changed:
                    page_text = HTMLFetcher.get_latest_text(url_id)
                    if page_text:
                        t0 = time.time()
                        description = HTMLFetcher.extract_description(page_text, url)
                        desc_ms = (time.time() - t0) * 1000
                        logger.info(f"  description extract — {desc_ms:.0f}ms (found={description is not None})")
                        if description:
                            r_row = Database.fetch_one(
                                "SELECT position, affiliation FROM researchers WHERE id = %s",
                                (researcher_id,),
                            )
                            position = r_row[0] if r_row else None
                            affiliation = r_row[1] if r_row else None
                            Database.append_researcher_snapshot(
                                researcher_id, position, affiliation, description, source_url=url
                            )

                url_ms = (time.time() - url_start) * 1000
                logger.info(f"  total — {url_ms:.0f}ms")

            except Exception as e:
                logger.error("Error processing URL %s (id=%s): %s", url, url_id, e)
                continue

        fetch_phase_s = time.time() - scrape_start
        logger.info(f"Fetch phase done: {fetch_phase_s:.1f}s for {urls_checked} URLs")

        # Validate draft URLs after extraction phase
        t0 = time.time()
        _validate_draft_urls()
        validate_s = time.time() - t0
        logger.info(f"Draft URL validation: {validate_s:.1f}s")

        total_s = time.time() - scrape_start
        update_scrape_log(log_id, "completed", urls_checked, urls_changed, pubs_extracted)
        logger.info(f"Scrape completed: {urls_checked} checked, {urls_changed} changed, {pubs_extracted} extracted — {total_s:.1f}s total")

    except Exception as e:
        logger.error("Scrape job failed: %s", type(e).__name__)
        if log_id:
            update_scrape_log(log_id, "failed", error_message=type(e).__name__)
    finally:
        _release_db_lock(lock_conn)
        _lock_conn = None


def start_scheduler():
    """Start the APScheduler BackgroundScheduler with the configured interval."""
    global _scheduler
    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return

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
        logger.info("SCRAPE_ON_STARTUP is true, triggering immediate scrape")
        run_scrape_job()


def shutdown_scheduler():
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")
