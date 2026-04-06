"""Centralized OpenAI client — lazy-initialized so importing this module
does not require OPENAI_API_KEY to be set."""

import os
from openai import OpenAI

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Return a shared OpenAI client, creating it on first call."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
    return _client


def get_model() -> str:
    """Return the configured model name."""
    return os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
