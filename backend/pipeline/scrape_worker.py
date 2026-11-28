"""Run the fetch-only scrape in its own process (the `scraper` Compose service).

The API restarts on every deploy; a scrape inside it was cut off each time.
This worker runs the same pass (scheduler.run_scrape_job) outside the API:

- Schedule from the database: a pass is due SCRAPE_INTERVAL_HOURS after the
  last one started, or at once if an unfinished pass can be resumed.
- SIGTERM/SIGINT (docker stop, a scraper redeploy) stops it gracefully:
  in-flight fetches finish, progress is saved and the pass is marked
  'interrupted'; the next start resumes it where it stopped.
- The scrape advisory lock still guards every pass, so the API (manual
  /api/scrape, or SCRAPE_IN_API=true) and this worker never fetch at once.

Run with: python -m backend.pipeline.scrape_worker
"""
import logging
import signal
import threading

from backend.database import fetch_one
from backend.pipeline import scheduler

logger = logging.getLogger(__name__)

_POLL_SECONDS = 300          # re-check the schedule at least this often
_LOCK_BUSY_WAIT_SECONDS = 300  # another process is scraping
_SCHEMA_WAIT_SECONDS = 30


def schema_ready() -> bool:
    """True once the API has added the columns this worker writes."""
    try:
        fetch_one("SELECT is_fetch_run, resumed_count FROM scrape_log LIMIT 1")
        fetch_one("SELECT scrape_checked_at FROM researcher_urls LIMIT 1")
        return True
    except Exception as e:
        logger.warning("Waiting for the API's schema migrations: %s: %s", type(e).__name__, e)
        return False


def run_worker(stop: threading.Event, poll_seconds: float = _POLL_SECONDS) -> None:
    while not stop.is_set() and not schema_ready():
        stop.wait(_SCHEMA_WAIT_SECONDS)

    while not stop.is_set():
        try:
            due, wait = scheduler.scrape_due()
        except Exception as e:
            logger.error("Could not read the scrape schedule: %s: %s", type(e).__name__, e)
            due, wait = False, 60.0

        if due:
            status = scheduler.run_scrape_job(stop_event=stop)
            if status is None:
                wait = _LOCK_BUSY_WAIT_SECONDS
            elif status == 'failed':
                # A failed pass is resumable, so it would be due again at once.
                wait = scheduler._FAILED_RUN_RETRY_SECONDS
            else:
                continue  # completed: compute the next due time; interrupted: loop exits
        else:
            logger.info("Next fetch pass due in %.1f h", wait / 3600)

        stop.wait(min(max(wait, 1.0), poll_seconds))
    logger.info("Scrape worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop = threading.Event()

    def _stop(signum, _frame):
        logger.info("Received signal %s: finishing in-flight fetches, then saving progress", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)
    logger.info("Scrape worker started: %d fetch workers, interval %dh",
                scheduler.SCRAPE_CONCURRENCY, scheduler.SCRAPE_INTERVAL_HOURS)
    # Pings the scrape lock connection and re-acquires it if it drops.
    scheduler._start_lock_keepalive()
    try:
        run_worker(stop)
    finally:
        scheduler._stop_lock_keepalive()


if __name__ == "__main__":
    main()
