"""Tests for the JEL classification pipeline in jel_classifier.py.

All external dependencies (LLM client, Database) are mocked.
"""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test-key")
os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import MagicMock, patch

import pytest
from openai import OpenAIError

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


# ---- LLM extraction tests ----

def _make_llm_completion(jel_codes: list[dict]):
    """Build a mock OpenAI-compatible chat completion returning JSON content (Parasail/Gemma shape)."""
    import json as _json
    payload = {"jel_codes": jel_codes}
    message = MagicMock()
    message.content = _json.dumps(payload)
    # refusal is no longer used after migration, but leave as None for safety
    message.refusal = None

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
    @patch("llm_client.get_client")
    def test_returns_codes(self, mock_get_client, mock_log):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _make_llm_completion([
            {"code": "J", "reasoning": "labor"},
            {"code": "F", "reasoning": "international trade"},
        ])
        codes = classify_researcher(1, "Jane", "Doe", "I study labor and trade.")
        assert codes == ["J", "F"]
        mock_log.assert_called_once()

    @patch("jel_classifier.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_empty_on_api_error(self, mock_get_client, mock_log):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = OpenAIError("API down")
        codes = classify_researcher(1, "Jane", "Doe", "I study economics.")
        assert codes == []

    @patch("jel_classifier.Database.log_llm_usage")
    @patch("llm_client.get_client")
    def test_logs_llm_usage(self, mock_get_client, mock_log):
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _make_llm_completion([
            {"code": "E", "reasoning": "macro"},
        ])
        classify_researcher(42, "John", "Smith", "Macro researcher.")
        from llm_client import get_model
        mock_log.assert_called_once_with(
            "jel_classification",
            get_model(),
            mock_client.chat.completions.create.return_value.usage,
            researcher_id=42,
        )
