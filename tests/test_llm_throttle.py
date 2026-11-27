"""Per-minute LLM pacing and the extraction workers' database gate."""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from openai import RateLimitError

from backend.llm import throttle
from backend.llm.throttle import ExtractionGate, TokenRateLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _limiter(tokens=1000, requests=0):
    clock = FakeClock()
    return TokenRateLimiter(tokens, requests, clock=clock, sleep=clock.sleep), clock


def test_calls_within_budget_do_not_wait():
    limiter, clock = _limiter(tokens=1000)
    limiter.acquire(400)
    limiter.acquire(500)
    assert clock.slept == []


def test_call_over_token_budget_waits_for_window_to_free():
    limiter, clock = _limiter(tokens=1000)
    limiter.acquire(600)
    clock.now += 10
    limiter.acquire(600)
    # The first reservation leaves the window 60 s after it was made.
    assert 49 <= sum(clock.slept) <= 51


def test_settle_replaces_estimate_with_reported_usage():
    limiter, clock = _limiter(tokens=1000)
    reservation = limiter.acquire(900)
    limiter.settle(reservation, 100)
    limiter.acquire(800)
    assert clock.slept == []


def test_oversized_prompt_goes_out_alone():
    limiter, clock = _limiter(tokens=1000)
    limiter.acquire(5000)
    assert clock.slept == []
    limiter.acquire(10)
    assert sum(clock.slept) >= 59


def test_request_cap():
    limiter, clock = _limiter(tokens=0, requests=2)
    limiter.acquire(1)
    limiter.acquire(1)
    assert clock.slept == []
    limiter.acquire(1)
    assert sum(clock.slept) >= 59


def test_budget_share_leaves_room_for_other_callers():
    limiter, clock = _limiter(tokens=1000)
    limiter.acquire(700, share=0.8)
    limiter.acquire(200, share=1.0)  # full-budget caller still fits
    assert clock.slept == []
    limiter.acquire(150, share=0.8)  # 1050 > 800
    assert clock.slept


def test_pause_holds_every_caller():
    limiter, clock = _limiter(tokens=1000)
    limiter.pause(30)
    limiter.acquire(1)
    assert 30 <= sum(clock.slept) <= 31


def test_disabled_limiter_never_waits():
    limiter, clock = _limiter(tokens=0, requests=0)
    assert limiter.acquire(10 ** 9) is None
    assert clock.slept == []


def test_estimate_counts_utf8_bytes():
    ascii_tokens = throttle.estimate_prompt_tokens([{"role": "user", "content": "a" * 1000}])
    cjk_tokens = throttle.estimate_prompt_tokens([{"role": "user", "content": "経" * 1000}])
    assert 300 <= ascii_tokens <= 400
    assert cjk_tokens > 2 * ascii_tokens


def test_gate_is_released_only_by_its_holder():
    gate = ExtractionGate()
    assert not gate.held()
    with gate.released():  # no-op outside hold()
        pass
    with gate.hold():
        assert gate.held()
        with gate.released():
            assert not gate.held()
            # Another thread can take the gate while this one waits for the model.
            got = []
            t = threading.Thread(target=lambda: got.append(gate._lock.acquire(timeout=1)) or gate._lock.release())
            t.start()
            t.join()
            assert got == [True]
        assert gate.held()
    assert not gate.held()


def _rate_limit_error():
    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(429, request=request, headers={"retry-after": "42"})
    return RateLimitError("quota", response=response, body=None)


def test_client_wrapper_paces_settles_and_pauses(monkeypatch):
    from backend.llm import client as llm_client

    limiter, clock = _limiter(tokens=10_000)
    monkeypatch.setattr(throttle, "limiter", limiter)
    fake = MagicMock()
    fake.chat.completions.create.return_value = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=123))
    llm_client._throttle_completions(fake)

    fake.chat.completions.create(messages=[{"role": "user", "content": "x" * 4000}])
    assert limiter.usage()["input_tokens_last_minute"] == 123

    original = MagicMock(side_effect=_rate_limit_error())
    fake2 = MagicMock()
    fake2.chat.completions.create = original
    llm_client._throttle_completions(fake2)
    with pytest.raises(RateLimitError):
        fake2.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert limiter.usage()["paused_for_seconds"] >= 41


def test_extract_json_releases_gate_during_the_call(monkeypatch):
    from pydantic import BaseModel

    from backend.llm import client as llm_client

    class Out(BaseModel):
        ok: bool

    seen = []
    gate = ExtractionGate()
    monkeypatch.setattr(throttle, "extraction_gate", gate)

    def create(**kwargs):
        seen.append(gate.held())
        message = SimpleNamespace(content='{"ok": true}')
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=message, finish_reason="stop")])

    fake = MagicMock()
    fake.chat.completions.create.side_effect = create
    monkeypatch.setattr(llm_client, "get_client", lambda: fake)
    with gate.hold():
        result = llm_client.extract_json("prompt", Out)
        assert gate.held()
    assert result.parsed.ok is True
    assert seen == [False]
