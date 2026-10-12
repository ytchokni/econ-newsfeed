"""Run bounded mining hourly in a small worker separate from the live API."""
import logging
import signal
import threading
import time

from backend.discovery.mining import run_site_mining_batch

logger = logging.getLogger(__name__)


def run_worker(stop=None, interval_seconds=3600):
    stop = stop or threading.Event()
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    while not stop.is_set():
        started = time.monotonic()
        try:
            run_site_mining_batch(apply=True)
        except Exception:
            logger.exception("Site mining batch failed; will retry next hour")
        stop.wait(max(1, interval_seconds - (time.monotonic() - started)))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run_worker(stop)


if __name__ == "__main__":
    main()
