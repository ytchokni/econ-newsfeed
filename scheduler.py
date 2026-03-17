import logging
import os
from datetime import datetime

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
    return Database.execute_query(query, (datetime.utcnow(),))


def update_scrape_log(log_id, status, urls_checked=0, urls_changed=0, pubs_extracted=0, error_message=None):
    """Update an existing scrape_log entry with results."""
    query = """
        UPDATE scrape_log
        SET finished_at = %s, status = %s, urls_checked = %s,
            urls_changed = %s, pubs_extracted = %s, error_message = %s
        WHERE id = %s
    """
    Database.execute_query(query, (
        datetime.utcnow(), status, urls_checked,
        urls_changed, pubs_extracted, error_message, log_id
    ))


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

        for url_id, researcher_id, url, page_type in urls:
            urls_checked += 1

            # Get old text before fetch overwrites it (upsert)
            old_text = HTMLFetcher.get_previous_text(url_id)

            changed = HTMLFetcher.fetch_and_save_if_changed(url_id, url, researcher_id)

            if changed and page_type in ("PUB", "WP"):
                urls_changed += 1
                new_text = HTMLFetcher.get_latest_text(url_id)

                # Use diff if old content exists, otherwise full text
                extraction_text = HTMLFetcher.compute_diff(old_text, new_text) if old_text else new_text

                if extraction_text:
                    pubs = Publication.extract_publications(extraction_text, url)
                    if pubs:
                        Publication.save_publications(url, pubs)
                        pubs_extracted += len(pubs)

        update_scrape_log(log_id, "completed", urls_checked, urls_changed, pubs_extracted)
        logger.info(f"Scrape completed: {urls_checked} checked, {urls_changed} changed, {pubs_extracted} extracted")

    except Exception as e:
        logger.error(f"Scrape job failed: {e}")
        if log_id:
            update_scrape_log(log_id, "failed", error_message=str(e))
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
