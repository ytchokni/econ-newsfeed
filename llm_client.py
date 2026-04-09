"""Centralized Parasail LLM client — OpenAI-compatible, lazy-initialized.

Importing this module does not require PARASAIL_API_KEY to be set;
the key is read on first client access so test environments that stub
the client can import without crashing.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

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


T = TypeVar("T", bound=BaseModel)


@dataclass
class StructuredResponse:
    """Result of a schema-guided LLM call.

    `parsed` is None when the model output failed validation after all
    retries, or the API call raised. `usage` is the OpenAI-compat usage
    object from the final successful API response (or None if the API
    call itself raised).
    """
    parsed: BaseModel | None
    usage: object | None


def extract_json(
    prompt: str,
    model_class: type[T],
    *,
    max_tokens: int = 8000,
    retries: int = 1,
    temperature: float = 0.0,
) -> StructuredResponse:
    """Call the LLM with JSON-schema-guided decoding and return a validated Pydantic instance.

    Uses `response_format={"type": "json_schema", ...}` so vLLM constrains
    output at decode time. On Pydantic ValidationError or JSONDecodeError
    we retry up to `retries` times with a clarification appended to the
    prompt. Returns a StructuredResponse whose `parsed` is None on
    unrecoverable failure (callers treat this like an empty extraction).
    """
    client = get_client()
    model = get_model()
    schema = model_class.model_json_schema()
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": model_class.__name__,
            "schema": schema,
            "strict": False,  # vLLM honors schema via guided_json; strict is an OpenAI-only flag
        },
    }

    attempts = retries + 1
    last_usage: object | None = None
    current_prompt = prompt

    for attempt in range(attempts):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": current_prompt}],
                response_format=response_format,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            logging.error("LLM API call failed: %s: %s", type(e).__name__, e)
            return StructuredResponse(parsed=None, usage=None)

        last_usage = completion.usage
        content = completion.choices[0].message.content or ""

        try:
            parsed = model_class.model_validate_json(content)
            return StructuredResponse(parsed=parsed, usage=last_usage)
        except (ValidationError, json.JSONDecodeError) as e:
            logging.warning(
                "LLM JSON validation failed (attempt %d/%d): %s",
                attempt + 1, attempts, e,
            )
            if attempt + 1 < attempts:
                current_prompt = (
                    f"{prompt}\n\n"
                    f"Your previous response did not match the required schema. "
                    f"Return ONLY a JSON object matching the schema exactly. "
                    f"Do not include any prose, code fences, or commentary."
                )

    return StructuredResponse(parsed=None, usage=last_usage)
