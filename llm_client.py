"""Centralized Parasail LLM client — OpenAI-compatible, lazy-initialized.

Importing this module does not require PARASAIL_API_KEY to be set;
the key is read on first client access so test environments that stub
the client can import without crashing.
"""
from __future__ import annotations

import os

from openai import OpenAI

_client: OpenAI | None = None

PARASAIL_BASE_URL = "https://api.parasail.io/v1"
DEFAULT_MODEL = "google/gemma-4-31b-it"


def get_client() -> OpenAI:
    """Return a shared OpenAI SDK instance pointed at Parasail."""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=PARASAIL_BASE_URL,
            api_key=os.environ.get("PARASAIL_API_KEY"),
        )
    return _client


def get_model() -> str:
    """Return the configured LLM model. Override with LLM_MODEL env var."""
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)
