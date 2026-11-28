"""Per-host work queue for the parallel fetch pass (see scheduler.run_scrape_job).

URLs are grouped into one lane per hostname. A worker takes the lane whose
next URL has the lowest researcher_urls.id, and a lane is busy until that URL
is done, so at most one request per host is in flight. HTMLFetcher._rate_limit
still spaces the requests of a lane (2 s, 0.5 s on FAST_DOMAINS), so parallel
workers never fetch a site faster than the serial scrape did; they only work
on different sites at the same time.

With 42% of URLs on sites.google.com, its lane sets the floor on a full pass:
~15.6k URLs at 0.5 s spacing is ~2.2 h however many workers run.
"""
from __future__ import annotations

import heapq
import threading
from collections import deque
from urllib.parse import urlparse


def lane_key(url: str) -> str:
    """The rate-limit key HTMLFetcher._rate_limit uses: the lower-cased hostname."""
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


class HostLanes:
    """Thread-safe dispatcher of URL rows, one in-flight row per host."""

    def __init__(self, rows: list[dict]):
        self._cond = threading.Condition()
        self._lanes: dict[str, deque] = {}
        for row in sorted(rows, key=lambda r: r['id']):
            self._lanes.setdefault(lane_key(row['url']), deque()).append(row)
        self._ready = [(lane[0]['id'], key) for key, lane in self._lanes.items()]
        heapq.heapify(self._ready)
        self._busy = 0
        self._remaining = len(rows)
        self._closed = False

    def take(self) -> dict | None:
        """Block until a row is free to fetch; None once all rows are taken and
        done, or after close()."""
        with self._cond:
            while True:
                if self._closed:
                    return None
                if self._ready:
                    _, key = heapq.heappop(self._ready)
                    self._busy += 1
                    self._remaining -= 1
                    return self._lanes[key].popleft()
                if self._busy == 0:
                    return None
                self._cond.wait()

    def done(self, row: dict) -> None:
        """Mark a taken row finished; its host's next row becomes available."""
        key = lane_key(row['url'])
        with self._cond:
            self._busy -= 1
            lane = self._lanes.get(key)
            if lane:
                heapq.heappush(self._ready, (lane[0]['id'], key))
            elif lane is not None:
                del self._lanes[key]
            self._cond.notify_all()

    def close(self) -> None:
        """Stop handing out rows (stop request, deadline, lost lock)."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def remaining(self) -> int:
        """Rows not yet handed out."""
        with self._cond:
            return self._remaining

    @property
    def busy(self) -> int:
        with self._cond:
            return self._busy
