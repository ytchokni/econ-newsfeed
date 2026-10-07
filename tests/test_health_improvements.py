"""Regression coverage for quota recovery and publication metadata."""
import httpx
import pytest
from unittest.mock import patch
from openai import RateLimitError
from backend.llm.client import _parse_retry_after
from backend.pipeline.publication import PublicationExtraction


@pytest.mark.parametrize("body", [
    {"error": {"details": [{"retryDelay": "120.5s"}]}},
    [{"error": {"details": [{"retryDelay": "120.5s"}]}}],
    {"details": [{"retryDelay": "120.5s"}]},
])
def test_google_retry_after_handles_wrapped_and_unwrapped_errors(body):
    error = RateLimitError("quota", response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")), body=body)
    assert _parse_retry_after(error) == 120.5


def test_retry_after_honors_longer_header():
    error = RateLimitError("quota", response=httpx.Response(429, headers={"retry-after": "600"}, request=httpx.Request("POST", "https://example.invalid")), body={})
    assert _parse_retry_after(error) == 600


@pytest.mark.parametrize("value", ["forthcoming", "TBD", "null", "0001", "03/2"])
def test_extraction_does_not_save_garbage_years(value):
    assert PublicationExtraction(title="A paper", authors=[], year=value).year is None


def test_quota_backoff_does_not_permanently_skip_pages():
    from backend.pipeline import scheduler
    from backend.pipeline.extraction import ExtractionOutcome
    scheduler._extraction_stop_event.clear()
    calls, waits = [], []

    def extract(row):
        calls.append(row['id'])
        if len(calls) == 6:
            scheduler._extraction_stop_event.set()
            return ExtractionOutcome('empty')
        return ExtractionOutcome('failed', retry_after=30)

    try:
        with patch.object(scheduler, 'get_urls_needing_extraction', return_value=[{'id': 1, 'url': 'https://example.org'}]), \
             patch('backend.pipeline.extraction.extract_one_url', side_effect=extract), \
             patch.object(scheduler._extraction_stop_event, 'wait', side_effect=lambda delay: waits.append(delay)):
            scheduler._extraction_worker_loop()
        assert calls == [1] * 6
        assert waits[:5] == [60, 120, 240, 480, 600]
    finally:
        scheduler._extraction_stop_event.clear()


def test_failed_page_recovers_after_cooldown_without_restart():
    from backend.pipeline import scheduler
    from backend.pipeline.extraction import ExtractionOutcome
    scheduler._extraction_stop_event.clear()
    clock, calls = [0.0], []

    def wait(delay):
        clock[0] += max(delay, 1)
        assert clock[0] < 5000, 'worker never retried the page'

    def extract(row):
        calls.append(clock[0])
        if len(calls) == 4:
            scheduler._extraction_stop_event.set()
            return ExtractionOutcome('empty')
        return ExtractionOutcome('failed')

    def queue(exclude_url_ids=()):
        return [] if exclude_url_ids else [{'id': 1, 'url': 'https://example.org', 'content_hash': 'same'}]

    try:
        with patch.object(scheduler, 'get_urls_needing_extraction', side_effect=queue), \
             patch('backend.pipeline.extraction.extract_one_url', side_effect=extract), \
             patch.object(scheduler.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(scheduler._extraction_stop_event, 'wait', side_effect=wait):
            scheduler._extraction_worker_loop()
        assert len(calls) == 4
        assert calls[3] - calls[2] >= scheduler._EXTRACTION_URL_COOLDOWN_SECONDS
    finally:
        scheduler._extraction_stop_event.clear()


def test_deferred_pages_do_not_hide_later_queue_batches():
    from backend.pipeline import scheduler
    from backend.pipeline.extraction import ExtractionOutcome
    scheduler._extraction_stop_event.clear()
    calls = []

    def queue(exclude_url_ids=()):
        return [{'id': 2 if exclude_url_ids else 1, 'url': 'https://example.org'}]

    def extract(row):
        calls.append(row['id'])
        if row['id'] == 2:
            scheduler._extraction_stop_event.set()
            return ExtractionOutcome('empty')
        return ExtractionOutcome('failed')

    try:
        with patch.object(scheduler, 'get_urls_needing_extraction', side_effect=queue), \
             patch('backend.pipeline.extraction.extract_one_url', side_effect=extract), \
             patch.object(scheduler, '_EXTRACTION_MAX_URL_FAILURES', 1), \
             patch.object(scheduler._extraction_stop_event, 'wait'):
            scheduler._extraction_worker_loop()
        assert calls == [1, 2]
    finally:
        scheduler._extraction_stop_event.clear()


def test_cooldown_expires_for_failed_pages_beyond_first_batch():
    from backend.pipeline import scheduler
    from backend.pipeline.extraction import ExtractionOutcome
    scheduler._extraction_stop_event.clear()
    clock, calls = [0.0], []
    rows = [{'id': i, 'url': f'https://example.org/{i}', 'content_hash': 'same'} for i in (1, 2)]

    def queue(exclude_url_ids=()):
        return [r for r in rows if r['id'] not in exclude_url_ids][:1]

    def wait(delay):
        clock[0] += max(delay, 1)
        assert clock[0] < 30, 'second page remained excluded after its cooldown expired'

    def extract(row):
        calls.append((row['id'], clock[0]))
        if sum(url_id == 2 for url_id, _ in calls) == 2:
            scheduler._extraction_stop_event.set()
        return ExtractionOutcome('failed')

    try:
        with patch.object(scheduler, 'get_urls_needing_extraction', side_effect=queue), \
             patch('backend.pipeline.extraction.extract_one_url', side_effect=extract), \
             patch.object(scheduler.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(scheduler, '_EXTRACTION_MAX_URL_FAILURES', 1), \
             patch.object(scheduler, '_EXTRACTION_URL_COOLDOWN_SECONDS', 10), \
             patch.object(scheduler, '_EXTRACTION_DELAY_SECONDS', 1), \
             patch.object(scheduler, '_EXTRACTION_IDLE_SECONDS', 1), \
             patch.object(scheduler._extraction_stop_event, 'wait', side_effect=wait):
            scheduler._extraction_worker_loop()
        second_page_calls = [at for url_id, at in calls if url_id == 2]
        assert len(second_page_calls) == 2
        assert second_page_calls[1] - second_page_calls[0] >= 10
    finally:
        scheduler._extraction_stop_event.clear()
