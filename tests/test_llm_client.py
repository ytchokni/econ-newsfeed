"""Unit tests for llm_client — Parasail-backed OpenAI-compatible client."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from unittest.mock import MagicMock, patch

import pytest
from openai import OpenAIError
from pydantic import BaseModel


class TestGetClient:
    def test_returns_openai_client_pointed_at_parasail(self):
        import llm_client
        llm_client._client = None  # reset module cache
        client = llm_client.get_client()
        assert str(client.base_url).rstrip("/") == "https://api.parasail.io/v1"

    def test_client_is_cached(self):
        import llm_client
        llm_client._client = None
        a = llm_client.get_client()
        b = llm_client.get_client()
        assert a is b


class TestGetModel:
    def test_default_model_is_gemma_4_31b(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL", raising=False)
        import llm_client
        assert llm_client.get_model() == "google/gemma-4-31b-it"

    def test_model_overridable_by_env(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "google/gemma-4-12b-it")
        import llm_client
        assert llm_client.get_model() == "google/gemma-4-12b-it"


class _Item(BaseModel):
    name: str
    count: int


class _ItemList(BaseModel):
    items: list[_Item]


def _mock_completion(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message = MagicMock()
    completion.choices[0].message.content = content
    completion.usage = MagicMock()
    completion.usage.prompt_tokens = prompt_tokens
    completion.usage.completion_tokens = completion_tokens
    completion.usage.total_tokens = prompt_tokens + completion_tokens
    return completion


class TestExtractJson:
    @patch("llm_client.get_client")
    def test_happy_path_returns_validated_instance(self, mock_get_client):
        import llm_client
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _mock_completion(
            '{"items":[{"name":"a","count":1},{"name":"b","count":2}]}'
        )

        result = llm_client.extract_json("prompt", _ItemList)

        assert result.parsed is not None
        assert len(result.parsed.items) == 2
        assert result.parsed.items[0].name == "a"
        assert result.usage.total_tokens == 15

    @patch("llm_client.get_client")
    def test_uses_json_schema_response_format(self, mock_get_client):
        import llm_client
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _mock_completion('{"items":[]}')

        llm_client.extract_json("prompt", _ItemList)

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["name"] == "_ItemList"
        assert "schema" in kwargs["response_format"]["json_schema"]
        assert kwargs["model"] == "google/gemma-4-31b-it"

    @patch("llm_client.get_client")
    def test_malformed_json_retries_then_returns_none(self, mock_get_client):
        import llm_client
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_completion("this is not json at all"),
            _mock_completion("still garbage"),
        ]

        result = llm_client.extract_json("prompt", _ItemList, retries=1)

        assert result.parsed is None
        assert result.usage is not None
        assert mock_client.chat.completions.create.call_count == 2

    @patch("llm_client.get_client")
    def test_validation_error_retries_then_succeeds(self, mock_get_client):
        import llm_client
        mock_client = mock_get_client.return_value
        # First response: missing required "count" field
        # Second response: valid
        mock_client.chat.completions.create.side_effect = [
            _mock_completion('{"items":[{"name":"a"}]}'),
            _mock_completion('{"items":[{"name":"a","count":1}]}'),
        ]

        result = llm_client.extract_json("prompt", _ItemList, retries=1)

        assert result.parsed is not None
        assert result.parsed.items[0].count == 1
        assert mock_client.chat.completions.create.call_count == 2

    @patch("llm_client.get_client")
    def test_api_exception_returns_none_parsed_and_empty_usage(self, mock_get_client):
        import llm_client
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = OpenAIError("API down")

        result = llm_client.extract_json("prompt", _ItemList)

        assert result.parsed is None
        assert result.usage is None

    @patch("llm_client.get_client")
    def test_handles_none_content_gracefully(self, mock_get_client):
        """SDK may return content=None; treat as empty string → retry path."""
        import llm_client
        mock_client = mock_get_client.return_value
        none_completion = _mock_completion('{"items":[]}')
        none_completion.choices[0].message.content = None
        mock_client.chat.completions.create.side_effect = [
            none_completion,
            _mock_completion('{"items":[{"name":"a","count":1}]}'),
        ]

        result = llm_client.extract_json("prompt", _ItemList, retries=1)

        assert result.parsed is not None
        assert result.parsed.items[0].name == "a"

    @patch("llm_client.get_client")
    def test_retries_zero_means_single_attempt(self, mock_get_client):
        """retries=0 → exactly one API call, no retry on failure."""
        import llm_client
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = _mock_completion("garbage")

        result = llm_client.extract_json("prompt", _ItemList, retries=0)

        assert result.parsed is None
        assert mock_client.chat.completions.create.call_count == 1

    @patch("llm_client.get_client")
    def test_retry_prompt_contains_clarification(self, mock_get_client):
        """Second attempt's prompt must include the schema-clarification text."""
        import llm_client
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = [
            _mock_completion("bad"),
            _mock_completion('{"items":[]}'),
        ]

        llm_client.extract_json("original prompt text", _ItemList, retries=1)

        first_call = mock_client.chat.completions.create.call_args_list[0]
        second_call = mock_client.chat.completions.create.call_args_list[1]
        assert first_call.kwargs["messages"][0]["content"] == "original prompt text"
        assert "did not match the required schema" in second_call.kwargs["messages"][0]["content"]
        assert "original prompt text" in second_call.kwargs["messages"][0]["content"]
