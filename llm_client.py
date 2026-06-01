"""Centralized Google AI Studio LLM client — OpenAI-compatible, lazy-initialized.

Importing this module does not require GOOGLE_API_KEY to be set;
the key is read on first client access so test environments that stub
the client can import without crashing.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Generic, TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

_client: OpenAI | None = None
_client_lock = threading.Lock()

_genai_client = None
_genai_client_lock = threading.Lock()

GOOGLE_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemma-4-31b-it"


def get_client() -> OpenAI:
    """Return a shared OpenAI SDK instance pointed at Google AI Studio."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OpenAI(
                    base_url=GOOGLE_AI_STUDIO_BASE_URL,
                    api_key=os.environ.get("GOOGLE_API_KEY"),
                )
    return _client


def get_genai_client():
    """Return a shared google-genai Client for file upload/download (batch pipeline)."""
    global _genai_client
    if _genai_client is None:
        with _genai_client_lock:
            if _genai_client is None:
                from google import genai
                _genai_client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _genai_client


def get_model() -> str:
    """Return the configured LLM model. Override with LLM_MODEL env var."""
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


T = TypeVar("T", bound=BaseModel)


@dataclass
class StructuredResponse(Generic[T]):
    """Result of a schema-guided LLM call.

    `parsed` is None when the model output failed validation after all
    retries, or the API call raised. `usage` is the OpenAI-compat usage
    object from the final successful API response (or None if the API
    call itself raised).
    """
    parsed: T | None
    usage: object | None


_SCHEMA_METADATA_KEYS = {"$defs", "title", "description", "default"}


def _inline_refs(schema: dict) -> dict:
    """Resolve $ref and strip metadata keys that Gemini Batch API rejects."""
    defs = schema.pop("$defs", {})

    def resolve(node, inside_properties=False):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(dict(defs[ref_name]))
            result = {}
            for k, v in node.items():
                if not inside_properties and k in _SCHEMA_METADATA_KEYS:
                    continue
                result[k] = resolve(v, inside_properties=(k == "properties"))
            return result
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def build_json_schema_format(model_class: type[BaseModel]) -> dict:
    """Build a response_format dict for JSON-schema-guided decoding."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_class.__name__,
            "schema": _inline_refs(model_class.model_json_schema()),
            "strict": False,
        },
    }


def extract_json(
    prompt: str,
    model_class: type[T],
    *,
    max_tokens: int = 8000,
    retries: int = 1,
    temperature: float = 0.0,
) -> StructuredResponse[T]:
    """Call the LLM with JSON-schema-guided decoding and return a validated Pydantic instance.

    Uses `response_format={"type": "json_schema", ...}` so the provider
    constrains output at decode time. On Pydantic ValidationError or
    JSONDecodeError we retry up to `retries` times with a clarification
    appended to the prompt. Returns a StructuredResponse whose `parsed`
    is None on unrecoverable failure (callers treat this like an empty
    extraction).
    """
    client = get_client()
    model = get_model()
    response_format = build_json_schema_format(model_class)

    attempts = retries + 1
    last_usage: object | None = None

    clarified_prompt: str | None = None
    for attempt in range(attempts):
        if attempt > 0 and clarified_prompt is None:
            clarified_prompt = (
                f"{prompt}\n\n"
                f"Your previous response did not match the required schema. "
                f"Return ONLY a JSON object matching the schema exactly. "
                f"Do not include any prose, code fences, or commentary."
            )
        message_content = prompt if attempt == 0 else clarified_prompt
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": message_content}],
                response_format=response_format,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except OpenAIError as e:
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

    return StructuredResponse(parsed=None, usage=last_usage)
