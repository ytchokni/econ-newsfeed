"""Tests for diff-based extraction path in extraction.py and publication.py."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

from unittest.mock import MagicMock, patch, call

import pytest

from backend.llm.client import StructuredResponse
from backend.pipeline.publication import (
    ExtractionLLMResult, Publication, PublicationChange,
    PublicationChangeList, validate_publication,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(url_id=1, researcher_id=10, url="https://example.com/pubs", page_type="PUBLICATIONS"):
    return {"id": url_id, "researcher_id": researcher_id, "url": url, "page_type": page_type}


def _payload(content="page text", extracted_at="2026-01-01"):
    return {
        "content": content, "content_hash": "h1",
        "timestamp": None, "extracted_at": extracted_at,
    }


def _new_paper_change(**overrides):
    base = {
        "change_type": "new_paper",
        "title": "A New Discovery",
        "authors": [["Jane", "Doe"]],
        "year": "2025",
        "venue": "AER",
        "status": "working_paper",
        "draft_url": None,
        "abstract": None,
        "old_status": None,
        "old_title": None,
    }
    base.update(overrides)
    return base


def _status_change(**overrides):
    base = {
        "change_type": "status_change",
        "title": "Existing Paper",
        "authors": [["John", "Smith"]],
        "year": "2024",
        "venue": "JPE",
        "status": "published",
        "draft_url": None,
        "abstract": None,
        "old_status": "working_paper",
        "old_title": None,
    }
    base.update(overrides)
    return base


def _title_change(**overrides):
    base = {
        "change_type": "title_change",
        "title": "Improved Title for My Paper",
        "authors": [["Jane", "Doe"]],
        "year": "2024",
        "venue": None,
        "status": "working_paper",
        "draft_url": None,
        "abstract": None,
        "old_status": None,
        "old_title": "Old Title for My Paper",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# PublicationChange model tests
# ---------------------------------------------------------------------------

class TestPublicationChangeModel:
    def test_valid_new_paper(self):
        c = PublicationChange(**_new_paper_change())
        assert c.change_type == "new_paper"
        assert c.title == "A New Discovery"

    def test_valid_status_change(self):
        c = PublicationChange(**_status_change())
        assert c.change_type == "status_change"
        assert c.old_status == "working_paper"

    def test_valid_title_change(self):
        c = PublicationChange(**_title_change())
        assert c.change_type == "title_change"
        assert c.old_title == "Old Title for My Paper"

    def test_year_coerced_from_int(self):
        c = PublicationChange(**_new_paper_change(year=2025))
        assert c.year == "2025"

    def test_invalid_draft_url_scheme_rejected(self):
        c = PublicationChange(**_new_paper_change(draft_url="ftp://bad.com"))
        assert c.draft_url is None

    def test_valid_draft_url_preserved(self):
        c = PublicationChange(**_new_paper_change(draft_url="https://ssrn.com/123"))
        assert c.draft_url == "https://ssrn.com/123"

    def test_invalid_change_type_raises(self):
        with pytest.raises(Exception):
            PublicationChange(**_new_paper_change(change_type="invalid"))


class TestPublicationChangeList:
    def test_empty_changes(self):
        cl = PublicationChangeList(changes=[])
        assert cl.changes == []

    def test_mixed_changes(self):
        cl = PublicationChangeList(changes=[
            PublicationChange(**_new_paper_change()),
            PublicationChange(**_status_change()),
        ])
        assert len(cl.changes) == 2
        assert cl.changes[0].change_type == "new_paper"
        assert cl.changes[1].change_type == "status_change"


# ---------------------------------------------------------------------------
# Publication.build_diff_extraction_prompt tests
# ---------------------------------------------------------------------------

class TestBuildDiffPrompt:
    @pytest.fixture(autouse=True)
    def _patch_content_max(self):
        with patch("backend.pipeline.publication.CONTENT_MAX_CHARS", 20000):
            yield

    def test_contains_both_versions(self):
        prompt = Publication.build_diff_extraction_prompt("old text", "new text", "https://x.com")
        assert "old text" in prompt
        assert "new text" in prompt

    def test_contains_url(self):
        prompt = Publication.build_diff_extraction_prompt("old", "new", "https://econ.example.com")
        assert "https://econ.example.com" in prompt

    def test_truncates_long_text(self):
        import backend.pipeline.publication as publication
        original = publication.CONTENT_MAX_CHARS
        try:
            publication.CONTENT_MAX_CHARS = 100
            long = "X" * 200
            prompt = Publication.build_diff_extraction_prompt(long, "new", "https://x.com")
            assert prompt.count("X") == 100
        finally:
            publication.CONTENT_MAX_CHARS = original

    def test_mentions_change_types(self):
        prompt = Publication.build_diff_extraction_prompt("old", "new", "https://x.com")
        assert "new_paper" in prompt
        assert "status_change" in prompt
        assert "title_change" in prompt


# ---------------------------------------------------------------------------
# Publication.try_extract_changes tests
# ---------------------------------------------------------------------------

class TestTryExtractChanges:
    @pytest.fixture(autouse=True)
    def _patch_content_max(self):
        with patch("backend.pipeline.publication.CONTENT_MAX_CHARS", 20000):
            yield

    def test_returns_none_on_llm_failure(self):
        failed = StructuredResponse(parsed=None, usage=None)
        with patch("backend.pipeline.publication.extract_json", return_value=failed), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_changes("old", "new", "https://x.com")
        assert result.pubs is None

    def test_returns_empty_list_when_no_changes(self):
        parsed = PublicationChangeList(changes=[])
        ok = StructuredResponse(parsed=parsed, usage=MagicMock())
        with patch("backend.pipeline.publication.extract_json", return_value=ok), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_changes("old", "new", "https://x.com")
        assert result.pubs == []

    def test_returns_validated_changes(self):
        parsed = PublicationChangeList(changes=[
            PublicationChange(**_new_paper_change()),
        ])
        ok = StructuredResponse(parsed=parsed, usage=MagicMock())
        with patch("backend.pipeline.publication.extract_json", return_value=ok), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_changes("old", "new", "https://x.com")
        assert len(result.pubs) == 1
        assert result.pubs[0]["change_type"] == "new_paper"
        assert result.pubs[0]["title"] == "A New Discovery"

    def test_garbage_title_dropped(self):
        parsed = PublicationChangeList(changes=[
            PublicationChange(**_new_paper_change(title="cv")),
        ])
        ok = StructuredResponse(parsed=parsed, usage=MagicMock())
        with patch("backend.pipeline.publication.extract_json", return_value=ok), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_changes("old", "new", "https://x.com")
        assert result.pubs == []

    def test_removed_changes_not_validated(self):
        """Removed papers skip validate_publication (title may look like noise)."""
        parsed = PublicationChangeList(changes=[
            PublicationChange(**_new_paper_change(change_type="removed", title="cv")),
        ])
        ok = StructuredResponse(parsed=parsed, usage=MagicMock())
        with patch("backend.pipeline.publication.extract_json", return_value=ok), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_changes("old", "new", "https://x.com")
        assert len(result.pubs) == 1
        assert result.pubs[0]["change_type"] == "removed"

    def test_logs_usage_as_diff_extraction(self):
        parsed = PublicationChangeList(changes=[])
        usage = MagicMock()
        ok = StructuredResponse(parsed=parsed, usage=usage)
        with patch("backend.pipeline.publication.extract_json", return_value=ok) as mock_extract, \
             patch("backend.pipeline.publication.log_llm_usage") as mock_log:
            Publication.try_extract_changes("old", "new", "https://x.com", scrape_log_id=42)
        mock_log.assert_called_once()
        assert mock_log.call_args[0][0] == "diff_extraction"


# ---------------------------------------------------------------------------
# extract_one_url diff path integration tests
# ---------------------------------------------------------------------------

class TestExtractOneUrlDiffPath:
    def _base_patches(self):
        return {
            "payload": patch("backend.pipeline.extraction.HTMLFetcher.get_extraction_payload",
                             return_value=_payload()),
            "mark": patch("backend.pipeline.extraction.HTMLFetcher.mark_extracted"),
            "extract_text": patch("backend.pipeline.extraction.HTMLFetcher.extract_text_content",
                                  return_value="from raw html"),
            "extract_desc": patch("backend.pipeline.extraction.HTMLFetcher.extract_description", return_value=None),
            "prev_text": patch("backend.pipeline.extraction.HTMLFetcher.get_previous_text",
                               return_value="old page text"),
            "try_changes": patch("backend.pipeline.extraction.Publication.try_extract_changes"),
            "try_extract": patch("backend.pipeline.extraction.Publication.try_extract_publications"),
            "persist": patch("backend.pipeline.extraction.persist_extraction"),
            "snapshots": patch("backend.pipeline.extraction._append_snapshots"),
            "fetch_one": patch("backend.pipeline.extraction.fetch_one", return_value=None),
            "compute_hash": patch("backend.pipeline.extraction.compute_title_hash", return_value="hash1"),
            "researcher_snap": patch("backend.pipeline.extraction.append_researcher_snapshot"),
        }

    def test_uses_diff_path_when_previous_text_available(self):
        """Non-seed URL with previous snapshot uses try_extract_changes, not try_extract_publications."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        mocks["try_changes"].return_value = ExtractionLLMResult(pubs=[])
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.ok
        mocks["try_changes"].assert_called_once()
        mocks["try_extract"].assert_not_called()
        mocks["mark"].assert_called_once_with(1, "h1")

    def test_falls_back_to_full_for_seed(self):
        """Seed URL (extracted_at is None) uses full extraction."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        patches["payload"] = patch("backend.pipeline.extraction.HTMLFetcher.get_extraction_payload",
                                   return_value=_payload(extracted_at=None))
        mocks = {k: p.start() for k, p in patches.items()}
        mocks["try_extract"].return_value = ExtractionLLMResult(pubs=[])
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.ok
        mocks["try_extract"].assert_called_once()
        mocks["try_changes"].assert_not_called()
        mocks["prev_text"].assert_not_called()

    def test_falls_back_to_full_when_no_snapshot(self):
        """Non-seed URL with no previous snapshot falls back to full extraction."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        patches["prev_text"] = patch("backend.pipeline.extraction.HTMLFetcher.get_previous_text",
                                     return_value=None)
        mocks = {k: p.start() for k, p in patches.items()}
        mocks["try_extract"].return_value = ExtractionLLMResult(pubs=[])
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.ok
        mocks["try_extract"].assert_called_once()
        mocks["try_changes"].assert_not_called()

    def test_diff_new_paper_saves_and_creates_events(self):
        """New paper from diff extraction is persisted via persist_extraction."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        new_pub = _new_paper_change()
        mocks["try_changes"].return_value = ExtractionLLMResult(pubs=[new_pub])
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "extracted"
        assert outcome.pubs_count == 1
        mocks["persist"].assert_called_once()
        args = mocks["persist"].call_args
        assert args[0][2] == [new_pub]
        assert args[1]["is_seed"] is False

    def test_diff_llm_failure_returns_failed(self):
        """LLM failure on diff path returns 'failed' and does not mark extracted."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        mocks["try_changes"].return_value = ExtractionLLMResult(pubs=None)
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "failed"
        assert not outcome.ok
        mocks["mark"].assert_not_called()

    def test_diff_empty_changes_marks_extracted(self):
        """No changes detected still marks the URL as extracted."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        mocks = {k: p.start() for k, p in patches.items()}
        mocks["try_changes"].return_value = ExtractionLLMResult(pubs=[])
        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "empty"
        assert outcome.ok
        mocks["mark"].assert_called_once()

    def test_diff_status_change_routes_through_append_snapshots(self):
        """Status change from diff path routes through append_snapshots_for_pubs."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        mocks = {k: p.start() for k, p in patches.items()}

        sc = _status_change()
        mocks["try_changes"].return_value = ExtractionLLMResult(pubs=[sc])

        try:
            outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()

        assert outcome.status == "extracted"
        assert outcome.pubs_count == 1
        mocks["snapshots"].assert_called()
        calls = mocks["snapshots"].call_args_list
        status_call = [c for c in calls if c[0][0] == [sc]]
        assert len(status_call) == 1

    def test_diff_title_change_updates_paper(self):
        """Title change from diff path calls apply_title_rename and emits event."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        patches["apply_rename"] = patch("backend.pipeline.paper_saver.PaperSaver.apply_title_rename")
        mocks = {k: p.start() for k, p in patches.items()}

        tc = _title_change()
        mocks["try_changes"].return_value = ExtractionLLMResult(pubs=[tc])
        mocks["fetch_one"].return_value = {"id": 99}

        try:
            with patch("backend.pipeline.feed_events.FeedEventEmitter.emit_title_change") as mock_emit:
                outcome = extract_one_url(_row())
        finally:
            for p in patches.values():
                p.stop()

        assert outcome.status == "extracted"
        mocks["apply_rename"].assert_called_once_with(
            99, "Old Title for My Paper", "Improved Title for My Paper", tc,
            "https://example.com/pubs",
        )
        mock_emit.assert_called_once()
        emit_args = mock_emit.call_args[0]
        assert emit_args[0] == 99
        assert emit_args[1] == "Old Title for My Paper"
        assert emit_args[2] == "Improved Title for My Paper"

    def test_home_page_description_still_works_on_diff_path(self):
        """HOME page description update runs after diff extraction."""
        from backend.pipeline.extraction import extract_one_url
        patches = self._base_patches()
        patches["extract_desc"] = patch(
            "backend.pipeline.extraction.HTMLFetcher.extract_description", return_value="Bio text.")
        patches["fetch_one"] = patch(
            "backend.pipeline.extraction.fetch_one",
            return_value={"position": "Prof", "affiliation": "MIT"})
        mocks = {k: p.start() for k, p in patches.items()}
        mocks["try_changes"].return_value = ExtractionLLMResult(pubs=[])

        try:
            outcome = extract_one_url(_row(page_type="HOME"))
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.ok
        mocks["researcher_snap"].assert_called_once()


# ---------------------------------------------------------------------------
# HTMLFetcher.get_previous_text tests
# ---------------------------------------------------------------------------

class TestGetPreviousText:
    def test_returns_none_when_no_snapshot(self):
        with patch("backend.pipeline.html_fetcher.fetch_one", return_value=None):
            from backend.pipeline.html_fetcher import HTMLFetcher
            result = HTMLFetcher.get_previous_text(1)
        assert result is None

    def test_returns_none_when_compressed_is_none(self):
        with patch("backend.pipeline.html_fetcher.fetch_one",
                   return_value={"raw_html_compressed": None}):
            from backend.pipeline.html_fetcher import HTMLFetcher
            result = HTMLFetcher.get_previous_text(1)
        assert result is None

    def test_decompresses_and_normalizes(self):
        import zlib
        html = "<html><body><p>Hello  World</p><script>bad</script></body></html>"
        compressed = zlib.compress(html.encode("utf-8"))
        with patch("backend.pipeline.html_fetcher.fetch_one",
                   return_value={"raw_html_compressed": compressed}):
            from backend.pipeline.html_fetcher import HTMLFetcher
            result = HTMLFetcher.get_previous_text(1)
        assert result is not None
        assert "Hello" in result
        assert "World" in result
        assert "bad" not in result

    def test_returns_none_on_decompression_error(self):
        with patch("backend.pipeline.html_fetcher.fetch_one",
                   return_value={"raw_html_compressed": b"not-compressed"}):
            from backend.pipeline.html_fetcher import HTMLFetcher
            result = HTMLFetcher.get_previous_text(1)
        assert result is None
