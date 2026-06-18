"""Tests for compact diff prompt and retry-after handling."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("LLM_MODEL", "gemma-4-31b-it")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import MagicMock

import pytest

from backend.llm.client import StructuredResponse, _parse_retry_after
from backend.pipeline.publication import Publication


class TestComputeCompactDiff:
    """Tests for Publication._compute_compact_diff."""

    # Use large enough texts so the diff overhead (headers, context) doesn't
    # exceed the new text size — matching real-world researcher pages.
    @staticmethod
    def _make_page(num_lines=200, prefix="Paper"):
        return "\n".join(f"{prefix} entry {i} — Some long title about economics research" for i in range(num_lines))

    def test_identical_texts_returns_empty_string(self):
        text = self._make_page()
        result = Publication._compute_compact_diff(text, text)
        assert result == ""

    def test_small_change_returns_compact_diff(self):
        old_lines = [f"Paper entry {i} — Some long title about economics research" for i in range(200)]
        new_lines = old_lines.copy()
        new_lines[100] = "Paper entry 100 — MODIFIED title about economics research"
        old = "\n".join(old_lines)
        new = "\n".join(new_lines)
        result = Publication._compute_compact_diff(old, new)
        assert result is not None
        assert result != ""
        assert "-Paper entry 100 — Some long title" in result
        assert "+Paper entry 100 — MODIFIED title" in result

    def test_large_rewrite_returns_none(self):
        # Create texts where every line is different, so the diff is larger
        # than the new text
        old = "\n".join(f"old line {i}" for i in range(100))
        new = "\n".join(f"completely new content {i}" for i in range(100))
        result = Publication._compute_compact_diff(old, new)
        # When the diff is larger than the new text, returns None
        assert result is None

    def test_context_lines_parameter(self):
        old_lines = [f"Paper entry {i} — Some long title about economics research" for i in range(200)]
        new_lines = old_lines.copy()
        new_lines[100] = "Modified Paper entry 100"

        result_small_ctx = Publication._compute_compact_diff(
            "\n".join(old_lines), "\n".join(new_lines), context_lines=2
        )
        result_large_ctx = Publication._compute_compact_diff(
            "\n".join(old_lines), "\n".join(new_lines), context_lines=20
        )
        assert result_small_ctx is not None
        assert result_large_ctx is not None
        # Larger context should produce a longer diff
        assert len(result_large_ctx) > len(result_small_ctx)

    def test_addition_only(self):
        old = self._make_page(200)
        new = old + "\nNew paper entry — A brand new publication about monetary policy\n"
        result = Publication._compute_compact_diff(old, new)
        assert result is not None
        assert "+New paper entry" in result

    def test_deletion_only(self):
        lines = [f"Paper entry {i} — Some long title about economics research" for i in range(200)]
        old = "\n".join(lines)
        new = "\n".join(lines[:199])  # Remove last line
        result = Publication._compute_compact_diff(old, new)
        assert result is not None
        assert "-Paper entry 199" in result


class TestSegmentText:
    """Tests for Publication._segment_text — splitting single-line pages."""

    def test_single_line_text_is_segmented(self):
        text = "Working Papers Some Paper Title. Abstract This is the abstract. Published in AER."
        segs = Publication._segment_text(text)
        assert len(segs) > 1

    def test_sentence_boundaries_split(self):
        text = "First sentence about economics. Second sentence about policy. Third one here."
        segs = Publication._segment_text(text)
        assert len(segs) == 3

    def test_section_headers_split(self):
        text = "Some intro text. Publications Paper A. Research interests include macro."
        segs = Publication._segment_text(text)
        assert any("Publications" in s for s in segs)
        assert any("Research" in s for s in segs)

    def test_segmentation_is_consistent(self):
        """Both old and new text are segmented the same way, so diffs are meaningful."""
        text = "Title of paper. Abstract goes here. Working Paper draft."
        segs = Publication._segment_text(text)
        # All original words appear somewhere in the segments
        for word in ["Title", "paper", "Abstract", "Working", "Paper", "draft"]:
            assert any(word in s for s in segs)

    def test_single_line_page_produces_compact_diff(self):
        """Real-world scenario: entire page as one line with a small change."""
        base = ". ".join(f"Paper {i} about topic {i}" for i in range(50))
        old = base + ". Status Revise and Resubmit at AER."
        new = base + ". Status Conditionally Accepted at AER."
        result = Publication._compute_compact_diff(old, new)
        assert result is not None
        assert result != ""
        assert len(result) < len(new)


class TestBuildDiffExtractionPrompt:
    """Tests for Publication.build_diff_extraction_prompt."""

    @staticmethod
    def _make_page(num_lines=200, prefix="Paper"):
        return "\n".join(f"{prefix} entry {i} — Some long title about economics research" for i in range(num_lines))

    def test_small_change_produces_compact_prompt(self):
        old_lines = [f"Paper entry {i} — Some long title about economics research" for i in range(200)]
        new_lines = old_lines.copy()
        new_lines.append("Paper C — A new publication about fiscal policy")
        old = "\n".join(old_lines)
        new = "\n".join(new_lines)
        prompt = Publication.build_diff_extraction_prompt(old, new, "http://example.com")
        # Should use compact diff prompt (mentions "unified diff")
        assert "unified diff" in prompt
        assert "+Paper C" in prompt
        # Should NOT contain the full old/new versions
        assert "OLD VERSION:" not in prompt
        assert "NEW VERSION:" not in prompt

    def test_large_rewrite_produces_full_prompt(self):
        old = "\n".join(f"old content {i}" for i in range(100))
        new = "\n".join(f"new content {i}" for i in range(100))
        prompt = Publication.build_diff_extraction_prompt(old, new, "http://example.com")
        # Should fall back to full prompt
        assert "OLD VERSION:" in prompt
        assert "NEW VERSION:" in prompt

    def test_identical_texts_produces_empty_changes_prompt(self):
        text = self._make_page()
        prompt = Publication.build_diff_extraction_prompt(text, text, "http://example.com")
        assert "not changed" in prompt or "identical" in prompt
        assert "empty changes list" in prompt

    def test_url_appears_in_prompt(self):
        old = self._make_page(200)
        new = old + "\nPaper B — New entry\n"
        url = "http://example.com/research"
        prompt = Publication.build_diff_extraction_prompt(old, new, url)
        assert url in prompt


class TestParseRetryAfter:
    """Tests for _parse_retry_after."""

    def _make_rate_limit_error(self, body):
        """Create a mock RateLimitError with the given body."""
        error = MagicMock()
        error.body = body
        return error

    def test_parse_google_api_format(self):
        error = self._make_rate_limit_error({
            "error": {
                "code": 429,
                "message": "Rate limit exceeded",
                "details": [
                    {"retryDelay": "57s"}
                ]
            }
        })
        assert _parse_retry_after(error) == 57.0

    def test_parse_fractional_seconds(self):
        error = self._make_rate_limit_error({
            "error": {
                "details": [
                    {"retryDelay": "30.5s"}
                ]
            }
        })
        assert _parse_retry_after(error) == 30.5

    def test_fallback_on_missing_details(self):
        error = self._make_rate_limit_error({
            "error": {"code": 429, "message": "Rate limit"}
        })
        assert _parse_retry_after(error) == 60.0

    def test_fallback_on_empty_body(self):
        error = self._make_rate_limit_error(None)
        assert _parse_retry_after(error) == 60.0

    def test_fallback_on_no_retry_delay_key(self):
        error = self._make_rate_limit_error({
            "error": {
                "details": [
                    {"someOtherKey": "value"}
                ]
            }
        })
        assert _parse_retry_after(error) == 60.0

    def test_multiple_details_finds_retry_delay(self):
        error = self._make_rate_limit_error({
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.ErrorInfo"},
                    {"retryDelay": "42s", "@type": "type.googleapis.com/google.rpc.RetryInfo"}
                ]
            }
        })
        assert _parse_retry_after(error) == 42.0


class TestStructuredResponseRetryAfter:
    """Tests for StructuredResponse.retry_after field."""

    def test_defaults_to_none(self):
        resp = StructuredResponse(parsed=None, usage=None)
        assert resp.retry_after is None

    def test_can_set_retry_after(self):
        resp = StructuredResponse(parsed=None, usage=None, retry_after=57.0)
        assert resp.retry_after == 57.0

    def test_existing_callers_unaffected(self):
        # Constructing with only parsed and usage should still work
        resp = StructuredResponse(parsed="test", usage={"tokens": 100})
        assert resp.parsed == "test"
        assert resp.usage == {"tokens": 100}
        assert resp.retry_after is None
