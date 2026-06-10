"""Tests for extraction.extract_one_url — shared per-URL extraction logic."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

from unittest.mock import patch

import pytest


def _row(url_id=1, researcher_id=10, url="https://example.com/pubs", page_type="PUBLICATIONS"):
    return {"id": url_id, "researcher_id": researcher_id, "url": url, "page_type": page_type}


def _patches(payload=None, pubs=None, is_seed=False):
    """Common patch set. pubs=None simulates LLM failure."""
    if payload is None:
        payload = {
            "content": "page text", "raw_html": "<html>", "content_hash": "h1",
            "timestamp": None, "extracted_at": None if is_seed else "2026-01-01",
        }
    return {
        "payload": patch("extraction.HTMLFetcher.get_extraction_payload", return_value=payload),
        "fetch_ts": patch("extraction.HTMLFetcher.get_fetch_timestamp", return_value=None),
        "mark": patch("extraction.HTMLFetcher.mark_extracted"),
        "extract_text": patch("extraction.HTMLFetcher.extract_text_content", return_value="from raw html"),
        "extract_desc": patch("extraction.HTMLFetcher.extract_description", return_value=None),
        "try_extract": patch("extraction.Publication.try_extract_publications", return_value=pubs),
        "save": patch("extraction.Publication.save_publications"),
        "reconcile": patch("extraction.reconcile_title_renames"),
        "links": patch("extraction.match_and_save_paper_links"),
        "snapshots": patch("extraction.append_snapshots_for_pubs"),
        "fetch_one": patch("extraction.Database.fetch_one", return_value=None),
        "researcher_snap": patch("extraction.Database.append_researcher_snapshot"),
    }


class TestExtractOneUrl:
    def test_happy_path_saves_and_marks_with_start_hash(self):
        from extraction import extract_one_url
        pubs = [{"title": "Paper A"}, {"title": "Paper B"}]
        patches = _patches(pubs=pubs)
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "extracted"
        assert outcome.pubs_count == 2
        assert outcome.ok
        mocks["save"].assert_called_once_with("https://example.com/pubs", pubs, is_seed=False, event_date=None)
        mocks["reconcile"].assert_called_once_with("https://example.com/pubs", pubs, event_date=None)
        mocks["links"].assert_called_once_with(1, pubs)
        mocks["snapshots"].assert_called_once_with(pubs, "https://example.com/pubs", event_date=None)
        mocks["mark"].assert_called_once_with(1, "h1")

    def test_llm_failure_returns_failed_and_does_not_mark(self):
        from extraction import extract_one_url
        patches = _patches(pubs=None)  # try_extract returns None = LLM failure
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "failed"
        assert not outcome.ok
        mocks["mark"].assert_not_called()
        mocks["save"].assert_not_called()

    def test_genuinely_empty_page_marks_extracted(self):
        from extraction import extract_one_url
        patches = _patches(pubs=[])
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "empty"
        assert outcome.ok
        mocks["mark"].assert_called_once_with(1, "h1")
        mocks["save"].assert_not_called()

    def test_no_stored_html_returns_no_content(self):
        from extraction import extract_one_url
        patches = _patches(pubs=[])
        patches["payload"] = patch("extraction.HTMLFetcher.get_extraction_payload", return_value=None)
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "no_content"
        assert not outcome.ok
        mocks["mark"].assert_not_called()

    def test_null_content_falls_back_to_raw_html(self):
        from extraction import extract_one_url
        payload = {"content": None, "raw_html": "<html>x</html>", "content_hash": "h2",
                   "timestamp": None, "extracted_at": "2026-01-01"}
        patches = _patches(payload=payload, pubs=[])
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.ok
        mocks["extract_text"].assert_called_once_with("<html>x</html>")
        mocks["try_extract"].assert_called_once()
        assert mocks["try_extract"].call_args[0][0] == "from raw html"

    def test_seed_flag_passed_through(self):
        from extraction import extract_one_url
        payload = {
            "content": "text", "raw_html": "<html>", "content_hash": "h1",
            "timestamp": None, "extracted_at": None,
        }
        patches = _patches(payload=payload, pubs=[{"title": "P"}])
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert mocks["save"].call_args[1]["is_seed"] is True
        assert mocks["save"].call_args[1]["event_date"] is None

    def test_home_page_updates_description(self):
        from extraction import extract_one_url
        patches = _patches(pubs=[])
        patches["extract_desc"] = patch(
            "extraction.HTMLFetcher.extract_description", return_value="An economist.")
        patches["fetch_one"] = patch(
            "extraction.Database.fetch_one",
            return_value={"position": "Prof", "affiliation": "MIT"})
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url(_row(page_type="HOME"))
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.ok
        mocks["researcher_snap"].assert_called_once_with(
            10, "Prof", "MIT", "An economist.", source_url="https://example.com/pubs")

    def test_description_failure_does_not_fail_extraction(self):
        from extraction import extract_one_url
        patches = _patches(pubs=[])
        patches["extract_desc"] = patch(
            "extraction.HTMLFetcher.extract_description", side_effect=RuntimeError("boom"))
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url(_row(page_type="HOME"))
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.ok
        mocks["mark"].assert_called_once()

    def test_non_home_page_skips_description(self):
        from extraction import extract_one_url
        patches = _patches(pubs=[])
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            extract_one_url(_row(page_type="PUBLICATIONS"))
        finally:
            for p in patches.values():
                p.stop()
        mocks["extract_desc"].assert_not_called()

    def test_save_exception_propagates_and_does_not_mark(self):
        """Mid-sequence persistence errors propagate (caller counts a failure);
        the URL stays unmarked so it is retried. Retry is safe: saves dedup
        via INSERT IGNORE + title_hash."""
        from extraction import extract_one_url
        patches = _patches(pubs=[{"title": "P"}])
        patches["save"] = patch(
            "extraction.Publication.save_publications", side_effect=RuntimeError("db down"))
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            with pytest.raises(RuntimeError):
                extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        mocks["mark"].assert_not_called()
