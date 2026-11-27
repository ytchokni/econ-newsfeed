"""Process-wide pacing of LLM calls, and the extraction workers' database gate.

The AI Studio key is limited per model per minute: 16,000 input tokens on
gemma-4-31b (Tier 1, measured 2026-11-27 from 429 bodies) and 30 requests.
Calls are latency-bound (45-190 s), so the extraction worker runs several
at once and this limiter keeps their sum, plus every other LLM caller in the
process (description, disambiguation, JEL), under the minute caps instead
of discovering them through 429s.

Callers marked with ``budget_share`` get a lane: the extraction workers may
use 80% of each minute and the background jobs (JEL, bio backfill) 20%,
whatever the other is doing, and
all calls together stay under the budget.

The limiter is per process. The API process runs the scheduler and every
worker, so it sees all their calls; a CLI run (``backend.main extract`` or
``classify-jel``) has its own limiter and, next to a running worker, can push
the key over its cap. Don't run those CLIs while EXTRACTION_WORKER_ENABLED.

The gate: extraction workers hold ``extraction_gate`` while they process a
page and give it up only while waiting for the model in ``extract_json``.
Saving papers, feed events and snapshots therefore stays one page at a time,
as with a single worker, while the model calls overlap.
"""
from __future__ import annotations

import contextlib
import logging
import math
import os
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0
# A prompt larger than its limit could otherwise wait for an empty window
# forever while other callers keep reserving: it goes out once usage is down
# to a quarter of the limit, and after MAX_WAIT_SECONDS regardless (a 429
# then pauses everyone briefly).
_OVERSIZED_ADMIT_FRACTION = 0.25
MAX_WAIT_SECONDS = 120.0
# Prompt tokens per UTF-8 byte, rounded up (English ~0.25, CJK ~0.33);
# reservations are corrected to the reported usage after each call.
_TOKENS_PER_BYTE = 0.3
_PROMPT_OVERHEAD_TOKENS = 50


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        logger.warning("Ignoring non-numeric %s", name)
        return default


def estimate_prompt_tokens(messages) -> int:
    total = 0
    for message in messages or ():
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            total += len(content.encode("utf-8"))
        elif content is not None:
            total += len(str(content).encode("utf-8"))
    return int(math.ceil(total * _TOKENS_PER_BYTE)) + _PROMPT_OVERHEAD_TOKENS


class _Reservation:
    __slots__ = ("at", "tokens", "lane")

    def __init__(self, at: float, tokens: int, lane: str):
        self.at = at
        self.tokens = tokens
        self.lane = lane


class TokenRateLimiter:
    """Sliding one-minute window over input tokens and request count.

    Each call belongs to a lane with a share of the budget (budget_share):
    a lane's own reservations stay under its share and all reservations
    together under the whole budget. So extraction at 0.8 and JEL at 0.2
    each keep their part however busy the other is.
    """

    def __init__(self, tokens_per_minute: float, requests_per_minute: float,
                 clock=time.monotonic, sleep=time.sleep):
        self.tokens_per_minute = tokens_per_minute
        self.requests_per_minute = requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._cond = threading.Condition()
        self._window: deque[_Reservation] = deque()
        self._paused_until = 0.0

    @property
    def enabled(self) -> bool:
        return self.tokens_per_minute > 0 or self.requests_per_minute > 0

    def _expire(self, now: float) -> None:
        while self._window and now - self._window[0].at >= WINDOW_SECONDS:
            self._window.popleft()

    @staticmethod
    def _wait_for(reservations: list[_Reservation], used: float, need: float,
                  limit: float, now: float) -> float:
        """Seconds until `need` more fits under `limit`, given what is in the window."""
        need = min(need, limit * (1 - _OVERSIZED_ADMIT_FRACTION))
        if used + need <= limit:
            return 0.0
        excess = used + need - limit
        for r in reservations:  # oldest first
            excess -= r.tokens
            if excess <= 0:
                return r.at + WINDOW_SECONDS - now
        return reservations[-1].at + WINDOW_SECONDS - now if reservations else 0.0

    def _wait_seconds(self, tokens: int, share: float, lane: str, now: float) -> float:
        if now < self._paused_until:
            return self._paused_until - now
        self._expire(now)
        if not self._window:
            return 0.0  # A single oversized prompt still goes out alone.
        everyone = list(self._window)
        mine = [r for r in everyone if r.lane == lane]
        waits = [0.0]
        if self.requests_per_minute > 0:
            for group, limit in ((everyone, self.requests_per_minute),
                                 (mine, self.requests_per_minute * share)):
                if group and len(group) + 1 > limit:
                    waits.append(group[0].at + WINDOW_SECONDS - now)
        if self.tokens_per_minute > 0:
            for group, limit in ((everyone, self.tokens_per_minute),
                                 (mine, self.tokens_per_minute * share)):
                if group:
                    waits.append(self._wait_for(group, sum(r.tokens for r in group),
                                                tokens, limit, now))
        return max(waits)

    def acquire(self, tokens: int, share: float = 1.0, lane: str = "default") -> _Reservation | None:
        """Block until the call fits, then reserve it. Returns None when disabled."""
        if not self.enabled:
            return None
        share = min(max(share, 0.05), 1.0)
        waited = 0.0
        with self._cond:
            while True:
                now = self._clock()
                delay = self._wait_seconds(tokens, share, lane, now)
                if delay > 0 and waited >= MAX_WAIT_SECONDS and now >= self._paused_until:
                    logger.warning("LLM throttle: %s sending %d input tokens after waiting %.0fs",
                                   lane, tokens, waited)
                    delay = 0
                if delay <= 0:
                    reservation = _Reservation(now, tokens, lane)
                    self._window.append(reservation)
                    break
                delay = min(delay + 0.05, WINDOW_SECONDS)
                waited += delay
                self._cond.wait(delay) if self._sleep is time.sleep else self._sleep(delay)
        if waited >= 5:
            logger.info("LLM throttle: %s waited %.0fs for %d input tokens", lane, waited, tokens)
        return reservation

    def settle(self, reservation: _Reservation | None, actual_tokens: int | None) -> None:
        """Replace the estimate with the provider's prompt token count."""
        if reservation is None or actual_tokens is None:
            return
        with self._cond:
            reservation.tokens = int(actual_tokens)
            self._cond.notify_all()

    def pause(self, seconds: float) -> None:
        """Hold every caller back after a 429 (the provider's retry delay)."""
        with self._cond:
            self._paused_until = max(self._paused_until, self._clock() + max(seconds, 0.0))
            self._cond.notify_all()

    def usage(self) -> dict:
        with self._cond:
            now = self._clock()
            self._expire(now)
            return {
                "requests_last_minute": len(self._window),
                "input_tokens_last_minute": sum(r.tokens for r in self._window),
                "paused_for_seconds": max(self._paused_until - now, 0.0),
            }


limiter = TokenRateLimiter(
    tokens_per_minute=_env_number("LLM_INPUT_TOKENS_PER_MINUTE", 14000),
    requests_per_minute=_env_number("LLM_REQUESTS_PER_MINUTE", 25),
)

_local = threading.local()


def current_share() -> tuple[float, str]:
    """(share, lane) of this thread's LLM calls."""
    return getattr(_local, "share", (1.0, "default"))


@contextlib.contextmanager
def budget_share(share: float, lane: str):
    """Limit this thread's LLM calls, as `lane`, to a fraction of the per-minute budget."""
    previous = current_share()
    _local.share = (share, lane)
    try:
        yield
    finally:
        _local.share = previous


class ExtractionGate:
    """A lock that its holder gives up while waiting for the model."""

    def __init__(self):
        self._lock = threading.Lock()
        self._local = threading.local()

    def held(self) -> bool:
        return getattr(self._local, "held", False)

    @contextlib.contextmanager
    def hold(self):
        self._lock.acquire()
        self._local.held = True
        try:
            yield
        finally:
            self._local.held = False
            self._lock.release()

    @contextlib.contextmanager
    def released(self):
        """Give the gate to other workers for the duration of a model call."""
        if not self.held():
            yield
            return
        self._local.held = False
        self._lock.release()
        try:
            yield
        finally:
            self._lock.acquire()
            self._local.held = True


extraction_gate = ExtractionGate()
