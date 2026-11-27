"""Researcher bio extraction must never store the model's reasoning (issue #258)."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

from unittest.mock import MagicMock, patch

import pytest

from backend.llm.client import StructuredResponse
from backend.pipeline.description import (
    MAX_DESCRIPTION_WORDS,
    ResearcherDescription,
    clean_description,
    extract_description,
    looks_like_reasoning,
)

# Prod text from researcher 3 (Thomas Rieger) and 1785 (Esther Duflo), 2026-11-27.
RIEGER_PROD = (
    "<thought>* Source: Text from a researcher's homepage. * Task: Extract a "
    "professional description (max 200 words). * Requirements: Who is this person, "
    "research interests, current position/affiliation."
)
DUFLO_PROD = (
    "<thought>* Input: Text from Esther Duflo's MIT homepage. * Task: Extract a "
    "professional description (max 200 words) describing who she is"
)
GOOD_BIO = (
    "Jane Doe is an Associate Professor of Economics at the University of Mannheim. "
    "Her research focuses on behavioral finance and household portfolio choice."
)


class TestLooksLikeReasoning:
    @pytest.mark.parametrize("text", [RIEGER_PROD, DUFLO_PROD])
    def test_prod_examples_are_flagged(self, text):
        assert looks_like_reasoning(text)

    @pytest.mark.parametrize("text", [
        "Draft: Jane Doe is an economist.",
        "The user wants a bio. Jane Doe is an economist.",
        "* Goal: describe the researcher\n* Output: plain text",
        "Let me look at the homepage text. Jane Doe is an economist.",
        "1. Position: Professor\n2. Affiliation: MIT",
    ])
    def test_other_reasoning_shapes_are_flagged(self, text):
        assert looks_like_reasoning(text)

    @pytest.mark.parametrize("text", [
        GOOD_BIO,
        "I am an assistant professor of economics at Yale. My research is on labor markets.",
        "John Smith studies credit constraints, monetary policy and the task-based approach "
        "to automation at the London School of Economics.",
        None,
        "",
    ])
    def test_real_bios_are_not_flagged(self, text):
        assert not looks_like_reasoning(text)


class TestCleanDescription:
    def test_closed_thought_block_is_stripped(self):
        raw = f"<thought>* Task: Extract a bio. Draft: ...</thought>\n{GOOD_BIO}"
        assert clean_description(raw) == GOOD_BIO

    def test_think_tags_are_stripped(self):
        assert clean_description(f"<think>planning</think> {GOOD_BIO}") == GOOD_BIO

    @pytest.mark.parametrize("raw", [RIEGER_PROD, DUFLO_PROD])
    def test_unclosed_thought_is_rejected(self, raw):
        assert clean_description(raw) is None

    def test_leftover_answer_after_unclosed_tag_is_rejected(self):
        assert clean_description(f"{GOOD_BIO} <thought>more notes") is None

    @pytest.mark.parametrize("raw", [None, "", "null", "None", "N/A", '"null"', "unknown."])
    def test_null_answers(self, raw):
        assert clean_description(raw) is None

    def test_too_short_is_rejected(self):
        assert clean_description("Economist.") is None

    def test_json_leftover_is_rejected(self):
        assert clean_description('{"description": "Jane Doe is an economist at MIT."}') is None

    def test_long_bio_truncated_at_sentence(self):
        sentence = "Jane Doe studies labor economics and public finance at a large university. "
        raw = sentence * 30  # 330 words
        out = clean_description(raw)
        assert len(out.split()) <= MAX_DESCRIPTION_WORDS
        assert out.endswith(".")

    def test_clean_bio_passes_unchanged(self):
        assert clean_description(f"  {GOOD_BIO}  ") == GOOD_BIO


def _structured(desc, usage=None):
    parsed = None if desc is ... else ResearcherDescription(description=desc)
    return StructuredResponse(parsed=parsed, usage=usage or MagicMock())


class TestExtractDescription:
    @patch("backend.pipeline.description.log_llm_usage")
    @patch("backend.llm.client.extract_json")
    def test_uses_structured_output(self, mock_extract, _log):
        mock_extract.return_value = _structured(GOOD_BIO)
        assert extract_description("page", "https://x.org") == GOOD_BIO
        args, kwargs = mock_extract.call_args
        assert args[1] is ResearcherDescription
        assert "https://x.org" in args[0]

    @patch("backend.pipeline.description.log_llm_usage")
    @patch("backend.llm.client.extract_json")
    def test_reasoning_in_json_field_is_rejected(self, mock_extract, _log):
        mock_extract.return_value = _structured(RIEGER_PROD)
        assert extract_description("page", "https://x.org") is None

    @patch("backend.pipeline.description.log_llm_usage")
    @patch("backend.llm.client.extract_json")
    def test_null_and_failed_calls_return_none(self, mock_extract, _log):
        mock_extract.return_value = _structured(None)
        assert extract_description("page", "https://x.org") is None
        mock_extract.return_value = _structured(...)
        assert extract_description("page", "https://x.org") is None

    @patch("backend.pipeline.description.log_llm_usage")
    @patch("backend.llm.client.extract_json", side_effect=RuntimeError("boom"))
    def test_exception_returns_none(self, _extract, _log):
        assert extract_description("page", "https://x.org") is None

    @patch("backend.pipeline.description.log_llm_usage")
    @patch("backend.llm.client.extract_json")
    def test_usage_is_logged(self, mock_extract, mock_log):
        mock_extract.return_value = _structured(GOOD_BIO)
        extract_description("page", "https://x.org", scrape_log_id=7)
        assert mock_log.call_args.args[0] == "description_extraction"
        assert mock_log.call_args.kwargs["scrape_log_id"] == 7

    @patch("backend.pipeline.description.log_llm_usage")
    @patch("backend.llm.client.get_client")
    def test_thought_prefixed_json_end_to_end(self, mock_get_client, _log):
        """Raw provider output with a reasoning block still yields the bio."""
        completion = MagicMock()
        completion.choices = [MagicMock()]
        completion.choices[0].message.content = (
            f'<thought>* Task: Extract a professional description.</thought>'
            f'{{"description": "{GOOD_BIO}"}}'
        )
        mock_get_client.return_value.chat.completions.create.return_value = completion
        assert extract_description("page", "https://x.org") == GOOD_BIO


class TestHomeDescriptionNeverOverwritesWithReasoning:
    @patch("backend.pipeline.extraction.append_researcher_snapshot")
    @patch("backend.pipeline.description.log_llm_usage")
    @patch("backend.llm.client.extract_json")
    def test_rejected_bio_keeps_stored_one(self, mock_extract, _log, mock_snapshot):
        from backend.pipeline.extraction import _update_home_description
        mock_extract.return_value = _structured(DUFLO_PROD)
        _update_home_description(1785, "page", "https://economics.mit.edu/people/faculty/esther-duflo")
        mock_snapshot.assert_not_called()
