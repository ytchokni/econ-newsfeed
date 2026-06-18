"""Tests for extraction prompt content — ensures key instructions are present."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

from backend.pipeline.publication import Publication


class TestFullExtractionPromptInstructions:
    """The full extraction prompt must contain anti-hallucination instructions."""

    def test_contains_exact_title_instruction(self):
        prompt = Publication.build_extraction_prompt("some text", "http://example.com")
        assert "exactly as" in prompt.lower() or "verbatim" in prompt.lower()

    def test_contains_no_parametric_knowledge_instruction(self):
        prompt = Publication.build_extraction_prompt("some text", "http://example.com")
        assert "do not use your" in prompt.lower() or "only from the content" in prompt.lower()

    def test_contains_paper_level_status_instruction(self):
        prompt = Publication.build_extraction_prompt("some text", "http://example.com")
        assert "forthcoming" in prompt.lower()

    def test_contains_reject_and_resubmit_guidance(self):
        prompt = Publication.build_extraction_prompt("some text", "http://example.com")
        lower = prompt.lower()
        assert "reject_and_resubmit" in lower or "reject" in lower


class TestDiffExtractionPromptInstructions:
    """The diff extraction prompt must contain the same anti-hallucination instructions."""

    def test_contains_exact_title_instruction(self):
        prompt = Publication.build_diff_extraction_prompt("old", "new", "http://example.com")
        assert "exactly as" in prompt.lower() or "verbatim" in prompt.lower()

    def test_contains_no_parametric_knowledge_instruction(self):
        prompt = Publication.build_diff_extraction_prompt("old", "new", "http://example.com")
        assert "do not use your" in prompt.lower() or "only from the content" in prompt.lower()
