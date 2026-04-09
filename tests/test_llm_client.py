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

from unittest.mock import patch

import pytest


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
