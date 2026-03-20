"""Tests for the JEL classification pipeline in jel_classifier.py.

All external dependencies (OpenAI client, Database) are mocked.
"""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("CONTENT_MAX_CHARS", "4000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import MagicMock, patch

import pytest

from jel_classifier import (
    JelClassification,
    JelClassificationResult,
    classify_researcher,
    build_classification_prompt,
)


# ---- Pydantic model tests ----

class TestJelClassificationModel:
    def test_valid_single_code(self):
        result = JelClassificationResult(
            jel_codes=[JelClassification(code="J", reasoning="labor economics focus")]
        )
        assert len(result.jel_codes) == 1
        assert result.jel_codes[0].code == "J"

    def test_valid_multiple_codes(self):
        result = JelClassificationResult(
            jel_codes=[
                JelClassification(code="J", reasoning="labor"),
                JelClassification(code="I", reasoning="education"),
            ]
        )
        assert len(result.jel_codes) == 2

    def test_empty_codes_allowed(self):
        result = JelClassificationResult(jel_codes=[])
        assert result.jel_codes == []

    def test_code_uppercased(self):
        c = JelClassification(code="j", reasoning="test")
        assert c.code == "J"


# ---- Prompt tests ----

class TestBuildClassificationPrompt:
    def test_includes_researcher_info(self):
        prompt = build_classification_prompt(
            first_name="Jane",
            last_name="Doe",
            description="I study labor markets and immigration.",
        )
        assert "Jane" in prompt
        assert "Doe" in prompt
        assert "labor markets" in prompt

    def test_includes_jel_categories(self):
        prompt = build_classification_prompt(
            first_name="Jane",
            last_name="Doe",
            description="Macro researcher.",
        )
        # Should mention at least some JEL categories
        assert "J" in prompt or "Labor" in prompt
        assert "jel_codes" in prompt.lower() or "JEL" in prompt


# ---- OpenAI extraction tests ----

def _make_openai_response(jel_codes: list[dict], refusal=None):
    """Build a mock OpenAI structured output response."""
    parsed_codes = [JelClassification(**c) for c in jel_codes]
    parsed_result = JelClassificationResult(jel_codes=parsed_codes)

    message = MagicMock()
    message.refusal = refusal
    message.parsed = parsed_result

    choice = MagicMock()
    choice.message = message

    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    usage.total_tokens = 150

    completion = MagicMock()
    completion.choices = [choice]
    completion.usage = usage
    return completion


class TestClassifyResearcher:
    @patch("jel_classifier.Database.log_llm_usage")
    @patch("jel_classifier._openai_client")
    def test_returns_codes(self, mock_client, mock_log):
        mock_client.beta.chat.completions.parse.return_value = _make_openai_response([
            {"code": "J", "reasoning": "labor"},
            {"code": "F", "reasoning": "international trade"},
        ])
        codes = classify_researcher(1, "Jane", "Doe", "I study labor and trade.")
        assert codes == ["J", "F"]
        mock_log.assert_called_once()

    @patch("jel_classifier.Database.log_llm_usage")
    @patch("jel_classifier._openai_client")
    def test_empty_on_refusal(self, mock_client, mock_log):
        mock_client.beta.chat.completions.parse.return_value = _make_openai_response(
            [], refusal="Cannot classify"
        )
        codes = classify_researcher(1, "Jane", "Doe", "No info.")
        assert codes == []

    @patch("jel_classifier.Database.log_llm_usage")
    @patch("jel_classifier._openai_client")
    def test_empty_on_api_error(self, mock_client, mock_log):
        mock_client.beta.chat.completions.parse.side_effect = Exception("API down")
        codes = classify_researcher(1, "Jane", "Doe", "I study economics.")
        assert codes == []

    @patch("jel_classifier.Database.log_llm_usage")
    @patch("jel_classifier._openai_client")
    def test_logs_llm_usage(self, mock_client, mock_log):
        mock_client.beta.chat.completions.parse.return_value = _make_openai_response([
            {"code": "E", "reasoning": "macro"},
        ])
        classify_researcher(42, "John", "Smith", "Macro researcher.")
        from jel_classifier import OPENAI_MODEL
        mock_log.assert_called_once_with(
            "jel_classification",
            OPENAI_MODEL,
            mock_client.beta.chat.completions.parse.return_value.usage,
            researcher_id=42,
        )
