# Migrate LLM Provider: OpenAI → Parasail (Gemma 4 31B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace OpenAI with Parasail-hosted Gemma 4 31B Instruct (`google/gemma-4-31b-it`) as the LLM backing all parsing, classification, and disambiguation calls — including the Batch API flow.

**Architecture:** Parasail exposes an OpenAI-compatible HTTPS endpoint at `https://api.parasail.io/v1` including `/chat/completions`, `/files`, and `/batches`. We continue using the `openai` Python SDK but point it at Parasail via `base_url` and an `api_key` from `PARASAIL_API_KEY`. Structured outputs move from OpenAI's beta `client.beta.chat.completions.parse(response_format=PydanticModel)` to standard `client.chat.completions.create(response_format={"type": "json_schema", "json_schema": {...}})` with Pydantic-side validation and a one-shot retry on `ValidationError`. A new `llm_client.py` replaces `openai_client.py`; OpenAI-specific env vars are removed.

**Tech Stack:** Python 3, `openai` SDK (kept, repointed), Pydantic v2 (`model_json_schema`, `model_validate_json`), MySQL, pytest.

---

## Context and Constraints

### Call sites migrating

| Call site | Flow | Structured? |
|-----------|------|-------------|
| [publication.py:519-557](publication.py#L519-L557) `Publication.extract_publications` | Sync, per URL (scheduler) | Yes (`PublicationExtractionList`) |
| [main.py:67-144](main.py#L67-L144) `batch_submit` | Writes JSONL, uploads, creates batch | No (free-form JSON, manual validate) |
| [main.py:155-289](main.py#L155-L289) `batch_check` | Polls + parses JSONL results | No (manual validate) |
| [jel_classifier.py:70-127](jel_classifier.py#L70-L127) `classify_researcher` | Sync, one researcher | Yes (`JelClassificationResult`) |
| [html_fetcher.py:572-611](html_fetcher.py#L572-L611) `HTMLFetcher.extract_description` | Sync, text completion | No (plain string) |
| [database/researchers.py:70-108](database/researchers.py#L70-L108) `_llm_disambiguate` | Sync, regex-parses JSON | No (plain string + regex) |

### Non-goals

- **Eval pipeline** ([eval/configs/*.yaml](eval/configs/)) — already routed through OpenRouter, not `openai_client`. Out of scope.
- **OpenAlex** — separate HTTP client, unrelated.
- **Frontend** — no LLM calls.
- **`test_exclusion_prompt.py`** (repo root, one-off scratch script) — will be deleted, not migrated.

### Key compatibility notes

1. **Parasail does support Batch API** (per user-supplied Parasail docs): same OpenAI-compatible endpoints (`client.files.create`, `client.batches.create`, `client.batches.retrieve`, `client.files.content`). Max 1M requests per batch, 1 GB input file. No 50% batch discount like OpenAI — we set the batch cost multiplier to `1.0`.
2. **No `beta.chat.completions.parse`.** vLLM (Parasail's backend) exposes `response_format={"type": "json_schema", "json_schema": {...}}` instead. Our helper generates the schema from the Pydantic class via `model_class.model_json_schema()`.
3. **No OpenAI refusals.** Gemma does not emit the `.message.refusal` field; treat as `None`. Drop refusal-checking branches during migration (keep the safety net of Pydantic validation returning empty lists on failure).
4. **`max_completion_tokens` → `max_tokens`.** vLLM expects the legacy field name. The one caller ([html_fetcher.py:596](html_fetcher.py#L596)) must be updated.
5. **`openai` Python package stays installed.** It's our HTTP client. Only `OPENAI_API_KEY`/`OPENAI_MODEL` env vars and the `openai_client.py` module go away.
6. **Cost tracking.** Add `google/gemma-4-31b-it` to `_LLM_PRICING` at `$0.14/M` prompt, `$0.40/M` completion. These are **placeholder rates** — no authoritative source is committed to this repo. Operators MUST confirm against Parasail's current published pricing or an actual invoice before shipping. The code comment in `database/llm.py` makes this placeholder nature explicit. If Parasail rates differ, update the tuple and the comment in a follow-up commit.

### Baseline check (prerequisite — not a code task)

Run the existing eval suite against Gemma 4 31B via OpenRouter **before** starting Task 1, to confirm the model meets quality bar on your test cases:

```bash
cd eval && promptfoo eval --config configs/publication_extraction.yaml
```

If Gemma 4 31B scores materially below `gpt-4o-mini` on the rubric, pause and reconsider (switch to Qwen 3.6 Plus or DeepSeek V3.2 — already in the eval config). Document the baseline scores in the PR description.

---

## File Structure

### New files

- `llm_client.py` — Replaces `openai_client.py`. Exposes `get_client()` (OpenAI SDK pointed at Parasail), `get_model()`, and `extract_json()` helper for schema-guided structured output with Pydantic validation + retry.
- `tests/test_llm_client.py` — Unit tests for `llm_client`.

### Deleted files

- `openai_client.py`
- `test_exclusion_prompt.py` (root-level scratch script)

### Modified files (production)

- `publication.py` — swap imports + `extract_publications` body
- `jel_classifier.py` — swap imports + `classify_researcher` body
- `html_fetcher.py` — swap imports + `extract_description` body, rename `max_completion_tokens` → `max_tokens`
- `database/researchers.py` — swap imports + `_llm_disambiguate` body
- `database/llm.py` — replace `_LLM_PRICING` entries, set batch multiplier to 1.0
- `main.py` — swap imports in `batch_submit` / `batch_check`, update model name / batch cost accounting
- `db_config.py` — `OPENAI_API_KEY` → `PARASAIL_API_KEY` in `REQUIRED_ENV_VARS`
- `scripts/check_env.py` — same env var swap
- `.env.example` — rename env vars, update defaults, update the "OpenAI (required)" section header
- `docker-compose.yml` — env var passthrough rename
- `.dockerignore` — `!openai_client.py` → `!llm_client.py`
- `.github/workflows/ci.yml` — CI secret rename
- `CLAUDE.md` — required env vars, model references, Batch API note
- `README.md` — env var references, model mentions

### Modified files (tests)

One pass at the end of Phase 3 renames env-var defaults across all test files. These only need `OPENAI_API_KEY` → `PARASAIL_API_KEY` and (where present) `OPENAI_MODEL` → `LLM_MODEL`:

- `tests/conftest.py`
- `tests/test_publication_extraction.py` (also updates mock structure)
- `tests/test_jel_classifier.py` (also updates mock structure)
- `tests/test_db_config.py`
- `tests/test_db_config_charset.py`
- `tests/test_doi_resolver.py`
- `tests/test_jel_enrichment.py`
- `tests/test_researcher_fields.py`
- `tests/test_encoding_researchers.py`
- `tests/test_encoding_openalex.py`
- `tests/test_encoding_integration.py`
- `tests/test_admin_dashboard.py`
- `tests/test_audit_encoding.py`
- `tests/test_papers.py`
- `tests/test_openalex.py`
- `tests/test_researcher_disambiguation.py`
- `tests/test_topic_jel_map.py`

---

## Phase 1: Build Parasail client (alongside existing OpenAI client)

### Task 1: Create `llm_client.py` with `get_client()` and `get_model()`

**Files:**
- Create: `llm_client.py`
- Create: `tests/test_llm_client.py`
- Modify: `.dockerignore` (add `!llm_client.py`)

- [ ] **Step 1: Write the failing test file**

Create `tests/test_llm_client.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_llm_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_client'`

- [ ] **Step 3: Implement `llm_client.py` (client + model only)**

Create `llm_client.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_llm_client.py -v`
Expected: 4 passed

- [ ] **Step 5: Add `!llm_client.py` to `.dockerignore`**

Edit `.dockerignore`, add a new line after `!openai_client.py`:

```
!openai_client.py
!llm_client.py
```

(The `openai_client.py` whitelist entry stays until Phase 3 Task 11, so the existing build keeps working.)

- [ ] **Step 6: Commit**

```bash
git add llm_client.py tests/test_llm_client.py .dockerignore
git commit -m "feat(llm): add Parasail-backed llm_client alongside openai_client"
```

---

### Task 2: Add `extract_json()` helper to `llm_client.py`

**Files:**
- Modify: `llm_client.py`
- Modify: `tests/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_client.py`:

```python
from unittest.mock import MagicMock, patch

from pydantic import BaseModel


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
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")

        result = llm_client.extract_json("prompt", _ItemList)

        assert result.parsed is None
        assert result.usage is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_llm_client.py::TestExtractJson -v`
Expected: FAIL with `AttributeError: module 'llm_client' has no attribute 'extract_json'`

- [ ] **Step 3: Implement `extract_json` in `llm_client.py`**

Append to `llm_client.py`:

```python
import json
import logging
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_llm_client.py -v`
Expected: 9 passed (4 from Task 1 + 5 new)

- [ ] **Step 5: Commit**

```bash
git add llm_client.py tests/test_llm_client.py
git commit -m "feat(llm): add extract_json helper with schema-guided decoding and retry"
```

---

### Task 3: Update pricing table in `database/llm.py`

**Files:**
- Modify: `database/llm.py:9-13`
- Modify: `database/llm.py:27` (batch multiplier)
- Create: `tests/test_llm_pricing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_pricing.py`:

```python
"""Tests for llm_usage cost estimation."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from unittest.mock import MagicMock, patch


class TestLlmPricing:
    @patch("database.llm.execute_query")
    def test_gemma_4_31b_cost_computed(self, mock_exec):
        from database.llm import log_llm_usage
        usage = MagicMock()
        usage.prompt_tokens = 1_000_000
        usage.completion_tokens = 1_000_000
        usage.total_tokens = 2_000_000

        log_llm_usage("publication_extraction", "google/gemma-4-31b-it", usage)

        assert mock_exec.called
        row = mock_exec.call_args[0][1]
        # row order: (called_at, call_type, model, prompt, completion, total, cost, is_batch, ...)
        cost = row[6]
        assert cost is not None
        # $0.14/M prompt + $0.40/M completion = $0.54 total
        assert abs(float(cost) - 0.54) < 1e-6

    @patch("database.llm.execute_query")
    def test_batch_multiplier_is_one_on_parasail(self, mock_exec):
        from database.llm import log_llm_usage
        usage = MagicMock()
        usage.prompt_tokens = 1_000_000
        usage.completion_tokens = 0
        usage.total_tokens = 1_000_000

        log_llm_usage("publication_extraction", "google/gemma-4-31b-it", usage, is_batch=True)

        row = mock_exec.call_args[0][1]
        cost = row[6]
        # $0.14/M prompt, no 50% discount on Parasail
        assert abs(float(cost) - 0.14) < 1e-6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_llm_pricing.py -v`
Expected: FAIL — `assert cost is not None` fails because `google/gemma-4-31b-it` is not in `_LLM_PRICING`.

- [ ] **Step 3: Update `_LLM_PRICING` and batch multiplier**

Edit `database/llm.py`. Replace lines 9-13:

```python
_LLM_PRICING = {  # (prompt, completion) cost per 1M tokens
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
}
```

with:

```python
# (prompt, completion) cost per 1M tokens. Gemma 4 31B rates are placeholders
# pending Parasail invoice confirmation. OpenAI entries are retained here
# until Task 11 (openai_client.py deletion) to avoid NULL cost rows during
# the intermediate migration commits (Tasks 4-10).
_LLM_PRICING = {
    "google/gemma-4-31b-it": (0.14, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
}
```

Then replace line 27:

```python
            multiplier = 0.5 if is_batch else 1.0
```

with:

```python
            # Parasail does not offer a batch discount — cost multiplier is 1.0
            # regardless of is_batch. The is_batch flag still distinguishes
            # sync vs batch calls in the llm_usage table for reporting.
            multiplier = 1.0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_llm_pricing.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add database/llm.py tests/test_llm_pricing.py
git commit -m "feat(llm): price Gemma 4 31B, drop 50% batch discount for Parasail"
```

---

## Phase 2: Migrate LLM call sites

### Task 4: Migrate `publication.py::extract_publications` to `llm_client`

**Files:**
- Modify: `publication.py:5` (import)
- Modify: `publication.py:519-557` (function body)
- Modify: `tests/test_publication_extraction.py:15-16,93-113,142-248` (env vars, mock helper, mock targets)

- [ ] **Step 1: Update the test env vars and mock helper**

Edit `tests/test_publication_extraction.py`. Replace lines 15-16:

```python
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
```

with:

```python
os.environ.setdefault("PARASAIL_API_KEY", "ps-test-key")
os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")
```

Then replace the `_make_openai_response` helper (lines 93-113) with `_make_llm_completion`:

```python
def _make_llm_completion(publications: list[dict]):
    """Build a mock OpenAI-compat chat completion with JSON content for Parasail/Gemma."""
    import json as _json
    payload = {"publications": publications}
    message = MagicMock()
    message.content = _json.dumps(payload)
    # refusal/parsed are no longer used after migration, but leave as None for safety
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
```

Then, in `TestExtractPublications`, change every reference to the old helper and mock:
- `_make_openai_response` → `_make_llm_completion`
- `mock_client.beta.chat.completions.parse` → `mock_client.chat.completions.create`
- `@patch("publication.get_client")` stays the same (the name `get_client` is re-exported from `llm_client` after the migration)

For the `test_refusal_returns_empty` test, repurpose it as `test_malformed_json_returns_empty`:

```python
@patch("publication.Database.log_llm_usage")
@patch("publication.get_client")
def test_malformed_json_returns_empty(self, mock_get_client, mock_log_usage):
    """Model returns text that fails JSON validation -> empty list after retry."""
    mock_client = mock_get_client.return_value
    bad = MagicMock()
    bad.choices = [MagicMock()]
    bad.choices[0].message = MagicMock()
    bad.choices[0].message.content = "not json"
    bad.usage = MagicMock()
    bad.usage.prompt_tokens = 10
    bad.usage.completion_tokens = 5
    bad.usage.total_tokens = 15
    mock_client.chat.completions.create.return_value = bad

    result = Publication.extract_publications("text", "https://example.com")

    assert result == []
```

Delete `test_parsed_none_returns_empty` (Parasail has no `.parsed` attribute).

For `test_api_error_returns_empty_and_logs`, change the side_effect target:

```python
mock_client.chat.completions.create.side_effect = RuntimeError("API down")
```

For `test_llm_usage_logged`, change assertion `args[2] is response.usage` to still reference the usage object on the mock completion (same path).

For `test_llm_usage_not_logged_on_api_error`:

```python
mock_client.chat.completions.create.side_effect = RuntimeError("boom")
```

- [ ] **Step 2: Run the updated tests to verify they fail**

Run: `poetry run pytest tests/test_publication_extraction.py::TestExtractPublications -v`
Expected: FAIL — tests still target `publication.get_client` returning a client whose `beta.chat.completions.parse` branch is taken by production code; production hasn't migrated yet, so mocks won't fire correctly.

- [ ] **Step 3: Swap the import in `publication.py`**

Edit `publication.py:5`:

```python
from openai_client import get_client, get_model
```

becomes

```python
from llm_client import get_client, get_model, extract_json
```

- [ ] **Step 4: Rewrite `Publication.extract_publications`**

Replace the body of `extract_publications` (lines 519-557) with:

```python
    @staticmethod
    def extract_publications(text_content: str, url: str, scrape_log_id: int | None = None) -> list[dict]:
        """Use the configured LLM to extract publication details from text content."""
        prompt = Publication.build_extraction_prompt(text_content, url)
        model = get_model()
        logging.info(f"Extracting publications from {url} using LLM ({model})")

        result = extract_json(prompt, PublicationExtractionList)

        if result.usage is not None:
            Database.log_llm_usage(
                "publication_extraction", model, result.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )

        if result.parsed is None:
            logging.warning(f"Publication extraction returned no parsed result for {url}")
            return []

        validated = []
        for pub in result.parsed.publications:
            d = pub.model_dump()
            if validate_publication(d):
                validated.append(d)
            else:
                logging.info(f"Validation dropped: {d.get('title', '<no title>')}")
        return validated
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_publication_extraction.py -v`
Expected: all tests in `TestExtractPublications` pass (minus deleted `test_parsed_none_returns_empty`). The `TestCleanTitle` / `TestSavePublications` suites should be unaffected.

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_publication_extraction.py
git commit -m "feat(llm): migrate publication extraction to Parasail via llm_client"
```

---

### Task 5: Migrate `jel_classifier.py` to `llm_client`

**Files:**
- Modify: `jel_classifier.py:7` (import)
- Modify: `jel_classifier.py:70-127` (function body)
- Modify: `tests/test_jel_classifier.py:11-12,108-150` (env, mocks)

- [ ] **Step 1: Update test env vars and mock targets**

Edit `tests/test_jel_classifier.py:11-12`:

```python
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
```

becomes

```python
os.environ.setdefault("PARASAIL_API_KEY", "ps-test-key")
os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")
```

Find the local `_make_openai_response` helper (likely near line 80-100) and rewrite it the same way as in Task 4 — a plain completion mock whose `.choices[0].message.content` is a JSON-encoded `{"jel_codes": [...]}` string.

Then, in each `@patch("jel_classifier.Database.log_llm_usage")` / `@patch("jel_classifier.get_client")` test:
- Change `mock_client.beta.chat.completions.parse.return_value = ...` to `mock_client.chat.completions.create.return_value = ...`
- Change `mock_client.beta.chat.completions.parse.side_effect` to `mock_client.chat.completions.create.side_effect`
- For the test at line 146-150 that calls `get_model()` from `openai_client`, change the import to `from llm_client import get_model`.

- [ ] **Step 2: Run the updated tests to verify they fail**

Run: `poetry run pytest tests/test_jel_classifier.py -v`
Expected: FAIL — production still targets `beta.chat.completions.parse`.

- [ ] **Step 3: Rewrite `classify_researcher`**

Edit `jel_classifier.py:7`:

```python
from openai_client import get_client, get_model
```

becomes

```python
from llm_client import extract_json, get_model
```

Then replace the body of `classify_researcher` (lines 70-127) with:

```python
def classify_researcher(
    researcher_id: int,
    first_name: str,
    last_name: str,
    description: str,
) -> list[str]:
    """Use the configured LLM to classify a researcher into JEL codes.

    Returns a list of JEL code strings (e.g. ["J", "F"]).
    """
    prompt = build_classification_prompt(first_name, last_name, description)
    model = get_model()
    logging.info(
        "Classifying %s %s (id=%d) into JEL codes using LLM (%s)",
        first_name, last_name, researcher_id, model,
    )

    result = extract_json(prompt, JelClassificationResult)

    if result.usage is not None:
        Database.log_llm_usage(
            "jel_classification", model, result.usage,
            researcher_id=researcher_id,
        )

    if result.parsed is None:
        logging.warning(
            "JEL classification returned no parsed result for %s %s",
            first_name, last_name,
        )
        return []

    codes = [c.code for c in result.parsed.jel_codes]
    logging.info(
        "Classified %s %s → %s",
        first_name, last_name, ", ".join(codes) or "(none)",
    )
    return codes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_jel_classifier.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jel_classifier.py tests/test_jel_classifier.py
git commit -m "feat(llm): migrate JEL classification to Parasail via llm_client"
```

---

### Task 6: Migrate `html_fetcher.py::extract_description` to `llm_client`

**Files:**
- Modify: `html_fetcher.py:580-611`

- [ ] **Step 1: Search for existing test coverage**

Run: `poetry run pytest tests/ -k extract_description -v --collect-only`
Expected: either a pre-existing test file is listed, or no tests collected. Record which.

- [ ] **Step 2: Update the function**

In `html_fetcher.py`, replace the `extract_description` method (lines 572-611) with:

```python
    @staticmethod
    def extract_description(text_content: str, url: str, scrape_log_id=None) -> str | None:
        """Extract a researcher description (up to 200 words) from plain text.

        Single LLM call on text content, output capped with max_tokens and
        truncated to 200 words application-side. Returns the description
        string, or None if nothing could be extracted.
        """
        from llm_client import get_client, get_model

        model = get_model()
        prompt = (
            f"From the following text from a researcher's homepage at {url}, "
            "extract a professional description (up to 200 words) describing who this person is, "
            "their research interests, and their current position/affiliation. "
            "Return only the description text, nothing else. "
            "If no clear description can be extracted, reply with exactly: null\n\n"
            f"Content:\n{text_content[:CONTENT_MAX_CHARS]}"
        )
        try:
            from database import Database
            response = get_client().chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=1024,
            )
            Database.log_llm_usage(
                "description_extraction", model, response.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )
            desc = (response.choices[0].message.content or "").strip()
            if desc.lower() in ("null", "none", ""):
                return None
            words = desc.split()
            if len(words) > 200:
                desc = ' '.join(words[:200])
            return desc
        except Exception as e:
            logging.error(f"Error extracting description from {url}: {e}")
            return None
```

Key changes vs. the old version:
1. `from openai_client import` → `from llm_client import`
2. `max_completion_tokens=1024` → `max_tokens=1024` (vLLM expects legacy param name)
3. `response.choices[0].message.content.strip()` → `(response.choices[0].message.content or "").strip()` (Parasail may return None content; guard against AttributeError)
4. Log message prefix no longer mentions "OpenAI"

- [ ] **Step 3: Run existing html_fetcher tests if any**

Run: `poetry run pytest tests/ -k "html_fetcher or description" -v`
Expected: pass (or no tests collected — acceptable; this function is exercised via `make scrape` smoke test in Phase 4).

- [ ] **Step 4: Commit**

```bash
git add html_fetcher.py
git commit -m "feat(llm): migrate description extraction to Parasail via llm_client"
```

---

### Task 7: Migrate `database/researchers.py::_llm_disambiguate` to `llm_client`

**Files:**
- Modify: `database/researchers.py:85-108`
- Modify: `tests/test_researcher_disambiguation.py` (env var + mock target if needed)

- [ ] **Step 1: Read the current disambiguation test to understand the mock pattern**

Run: `poetry run pytest tests/test_researcher_disambiguation.py -v --collect-only`

Inspect that file to determine which symbol is patched for the LLM call. The pre-migration code imports `from openai_client import get_client, get_model` *inside* the function at runtime (line 85), so tests probably patch `database.researchers.get_client` via a mock module insertion, or stub the whole function. Adapt the test updates in Step 4 below accordingly.

- [ ] **Step 2: Update the production function**

In `database/researchers.py`, find the block starting around line 84:

```python
    try:
        from openai_client import get_client, get_model
        client = get_client()
        model = get_model()
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model,
        )
```

Replace with:

```python
    try:
        from llm_client import get_client, get_model
        client = get_client()
        model = get_model()
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=256,
        )
```

(Adding an explicit `max_tokens=256` — disambiguation only needs `{"match_id": <int>}` which is ~20 tokens, and vLLM otherwise defaults to the model's max context window which wastes budget.)

Update the env var setdefault at the top of `tests/test_researcher_disambiguation.py:7`:

```python
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
```

becomes

```python
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")
```

- [ ] **Step 3: Update test mocks if the test patches `openai_client`**

If the test patches `openai_client.get_client` or `openai_client.get_model`, change the patch target to `llm_client.get_client` / `llm_client.get_model`. If it patches via `database.researchers.<symbol>`, no change needed.

- [ ] **Step 4: Run the test**

Run: `poetry run pytest tests/test_researcher_disambiguation.py -v`
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add database/researchers.py tests/test_researcher_disambiguation.py
git commit -m "feat(llm): migrate researcher disambiguation to Parasail via llm_client"
```

---

### Task 8: Migrate `main.py::batch_submit` and `batch_check` to Parasail Batch API

**Files:**
- Modify: `main.py:67-144` (batch_submit)
- Modify: `main.py:155-289` (batch_check)

- [ ] **Step 1: Search for existing batch tests**

Run: `poetry run pytest tests/ -k batch -v --collect-only`

Record which test file(s) cover the batch flow. If there are existing tests, update the env setdefaults and mock targets in parallel with the production changes below.

- [ ] **Step 2: Update `batch_submit` imports and client source**

In `main.py:69`:

```python
    from openai_client import get_client, get_model
```

becomes

```python
    from llm_client import get_client, get_model
```

No other change to `batch_submit` logic is required — the JSONL request bodies use the same `/v1/chat/completions` endpoint which Parasail exposes. The `model` field in each request body is now the Parasail model name because `get_model()` returns it.

One tweak: the `request["body"]` currently omits `response_format`. For the batch flow to benefit from schema-guided decoding on Parasail, add it. Replace:

```python
        request = {
            "custom_id": f"url_{url_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
        }
```

with:

```python
        from publication import PublicationExtractionList
        request = {
            "custom_id": f"url_{url_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "PublicationExtractionList",
                        "schema": PublicationExtractionList.model_json_schema(),
                        "strict": False,
                    },
                },
                "max_tokens": 8000,
            },
        }
```

(Hoist the `PublicationExtractionList` import above the `for` loop so it's only done once.)

- [ ] **Step 3: Update `batch_check` imports**

In `main.py:157`:

```python
    from openai_client import get_client, get_model
```

becomes

```python
    from llm_client import get_client, get_model
```

The rest of `batch_check` already parses raw JSON from response content and strips markdown fences — this continues to work unchanged against Parasail responses. The `Database.log_llm_usage(..., is_batch=True, ...)` call no longer applies the 50% discount (that was removed in Task 3).

- [ ] **Step 4: Update the batch_jobs DB column name references if needed**

Check [database/schema.py:273](database/schema.py#L273) — if the `batch_jobs` table has an `openai_batch_id` column, rename it in a follow-up plan only if the user asks. For now, the column name is implementation-detail and doesn't need to change; the Parasail batch ID fits in the same `VARCHAR` field.

```bash
poetry run pytest tests/ -k "batch" -v
```

Expected: pass (or no tests collected).

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(llm): point Batch API at Parasail, add JSON schema to batch requests"
```

---

## Phase 3: Rip out OpenAI-specific config

### Task 9: Rename required env vars

**Files:**
- Modify: `db_config.py:7`
- Modify: `scripts/check_env.py:10`
- Modify: `.env.example:14-16`
- Modify: `docker-compose.yml` (env passthrough section — find `OPENAI_API_KEY`)
- Modify: `.github/workflows/ci.yml` (CI secret references)

- [ ] **Step 1: Write the failing config test**

Edit `tests/test_db_config.py`. Find every line that lists `OPENAI_API_KEY` (lines 26, 44, 52 per the earlier grep) and replace with `PARASAIL_API_KEY`. Example for line 44 (the env dict used to satisfy imports):

```python
    "OPENAI_API_KEY": "sk-test-key",
```

becomes

```python
    "PARASAIL_API_KEY": "ps-test-key",
```

And for the tuple at line 52:

```python
        "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "OPENAI_API_KEY",
```

becomes

```python
        "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "PARASAIL_API_KEY",
```

At line 26 (the `set_env_except_required` helper), the tuple listing exceptions should also swap `OPENAI_API_KEY` → `PARASAIL_API_KEY`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_db_config.py -v`
Expected: FAIL — production code still requires `OPENAI_API_KEY`.

- [ ] **Step 3: Update `db_config.py`**

Edit [db_config.py:7](db_config.py#L7):

```python
REQUIRED_ENV_VARS = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'OPENAI_API_KEY']
```

becomes

```python
REQUIRED_ENV_VARS = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'PARASAIL_API_KEY']
```

- [ ] **Step 4: Update `scripts/check_env.py`**

Edit [scripts/check_env.py:10](scripts/check_env.py#L10):

```python
REQUIRED_VARS = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "OPENAI_API_KEY", "SCRAPE_API_KEY"]
```

becomes

```python
REQUIRED_VARS = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "PARASAIL_API_KEY", "SCRAPE_API_KEY"]
```

- [ ] **Step 5: Update `.env.example`**

Replace lines 14-16:

```
# OpenAI (required)
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini
```

with:

```
# LLM provider: Parasail (required)
# Sign up at https://parasail.io and generate an API key.
PARASAIL_API_KEY=ps-your-key-here
# HuggingFace-style model ID. Default: Gemma 4 31B Instruct.
LLM_MODEL=google/gemma-4-31b-it
```

- [ ] **Step 6: Update `docker-compose.yml`**

Search for `OPENAI_API_KEY` in the file:

```bash
poetry run python -c "import subprocess; print(subprocess.run(['grep','-n','OPENAI','docker-compose.yml'], capture_output=True, text=True).stdout)"
```

(Or use the Grep tool.) For every env passthrough line referencing `OPENAI_API_KEY` / `OPENAI_MODEL`, replace with `PARASAIL_API_KEY` / `LLM_MODEL`.

- [ ] **Step 7: Update `.github/workflows/ci.yml`**

Same search-and-replace: any `OPENAI_API_KEY` env definition becomes `PARASAIL_API_KEY: ${{ secrets.PARASAIL_API_KEY }}`. Any `OPENAI_MODEL` default becomes `LLM_MODEL: google/gemma-4-31b-it`.

**Heads-up for the operator (not a code step):** add a `PARASAIL_API_KEY` repository secret in GitHub **before** merging. The old `OPENAI_API_KEY` secret can be deleted after the CI run passes.

- [ ] **Step 8: Run the full test suite**

Run: `poetry run pytest tests/test_db_config.py tests/test_llm_client.py tests/test_llm_pricing.py -v`
Expected: pass

- [ ] **Step 9: Commit**

```bash
git add db_config.py scripts/check_env.py .env.example docker-compose.yml .github/workflows/ci.yml tests/test_db_config.py
git commit -m "chore(env): rename OPENAI_API_KEY/OPENAI_MODEL to PARASAIL_API_KEY/LLM_MODEL"
```

---

### Task 10: Update env setdefaults in all remaining test files

**Files:** (all under `tests/`)
- `tests/conftest.py:10-11`
- `tests/test_db_config_charset.py:8`
- `tests/test_doi_resolver.py:7`
- `tests/test_jel_enrichment.py:9`
- `tests/test_researcher_fields.py:8`
- `tests/test_encoding_researchers.py:5-6`
- `tests/test_encoding_openalex.py:5-6`
- `tests/test_encoding_integration.py:5-6`
- `tests/test_admin_dashboard.py:5-6`
- `tests/test_audit_encoding.py:9`
- `tests/test_papers.py:6`
- `tests/test_openalex.py:7`
- `tests/test_topic_jel_map.py:9`

- [ ] **Step 1: In every file listed above, apply these exact replacements**

Replace:
```python
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
```
with:
```python
os.environ.setdefault("PARASAIL_API_KEY", "ps-test")
```

Replace:
```python
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
```
with:
```python
os.environ.setdefault("PARASAIL_API_KEY", "ps-test-key")
```

Replace:
```python
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
```
with:
```python
os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")
```

Apply to each file exactly once (or twice for files that have both OPENAI_API_KEY and OPENAI_MODEL).

- [ ] **Step 2: Run the full pytest suite**

Run: `poetry run pytest -q`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test: swap OPENAI_API_KEY/OPENAI_MODEL env defaults for PARASAIL_API_KEY/LLM_MODEL"
```

---

### Task 11: Delete `openai_client.py` and scratch scripts

**Files:**
- Delete: `openai_client.py`
- Delete: `test_exclusion_prompt.py` (repo root)
- Modify: `.dockerignore` (remove `!openai_client.py`)

- [ ] **Step 1: Verify nothing still imports `openai_client`**

Run via Grep tool: pattern `from openai_client|import openai_client` across `**/*.py`.
Expected: zero matches (all call sites migrated in Phase 2).

If matches remain, go back and migrate them before proceeding.

- [ ] **Step 2: Delete `openai_client.py`**

```bash
rm openai_client.py
```

- [ ] **Step 3: Delete `test_exclusion_prompt.py`**

```bash
rm test_exclusion_prompt.py
```

- [ ] **Step 4: Remove `!openai_client.py` from `.dockerignore`**

Edit `.dockerignore` and delete the line:

```
!openai_client.py
```

- [ ] **Step 5: Run `make check`**

Run: `make check`
Expected: env check passes, pytest passes, tsc passes, jest passes.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: remove openai_client.py and dead test_exclusion_prompt.py"
```

---

## Phase 4: Validation and documentation

### Task 12: Full-stack smoke test against Parasail

**Files:** none (runtime validation)

- [ ] **Step 1: Export a Parasail API key for local dev**

```bash
export PARASAIL_API_KEY=ps-...your-real-key...
```

- [ ] **Step 2: Reset and seed the local DB**

```bash
make reset-db
make seed
```

- [ ] **Step 3: Import a small fixture (1-2 researchers)**

If there's a small fixture CSV available, use it; otherwise manually insert one researcher via the admin UI or SQL. Record the `researcher_urls.id` for verification.

- [ ] **Step 4: Run the scrape pipeline end-to-end**

```bash
make scrape
```

Expected:
- No `ModuleNotFoundError` or `KeyError('OPENAI_API_KEY')`
- Logs show `Extracting publications from <url> using LLM (google/gemma-4-31b-it)`
- At least one row appears in `papers` for the seeded researcher
- `llm_usage` rows show model = `google/gemma-4-31b-it` and non-NULL `estimated_cost_usd`

Verify:

```bash
poetry run python -c "
from database import Database
rows = Database.fetch_all('SELECT model, call_type, COUNT(*) as n, SUM(estimated_cost_usd) as cost FROM llm_usage GROUP BY model, call_type')
for r in rows: print(r)
"
```

- [ ] **Step 5: Smoke-test the Batch API flow**

```bash
poetry run python main.py  # or however batch_submit is invoked — check the CLI subparsers
```

If there is no CLI subparser for `batch` (there isn't in `main.py:323-354` per the code read), invoke the function directly:

```bash
poetry run python -c "from main import batch_submit; batch_submit()"
```

Expected: a batch is submitted and logged. Check `batch_jobs` table for a new row with non-NULL `openai_batch_id` (the column name is legacy; the value is a Parasail batch ID).

Then:

```bash
poetry run python -c "from main import batch_check; batch_check()"
```

(Parasail batches take time — may need to re-run later. Document the batch ID and mark verification as "pending" if needed.)

- [ ] **Step 6: Run the full test suite one final time**

```bash
make check
```

Expected: all green.

- [ ] **Step 7: Commit (if any fixups were required)**

Only if Steps 1-6 revealed bugs and you fixed them. Otherwise skip.

---

### Task 13: Update `CLAUDE.md` and `README.md`

**Files:**
- Modify: `CLAUDE.md` (Configuration section)
- Modify: `README.md`

- [ ] **Step 1: Update `CLAUDE.md` Configuration section**

Find the line listing required env vars (the current text is `Required env vars: \`DB_HOST\`, \`DB_USER\`, \`DB_PASSWORD\`, \`DB_NAME\`, \`OPENAI_API_KEY\`, \`SCRAPE_API_KEY\`.`) and replace with:

```markdown
Required env vars: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `PARASAIL_API_KEY`, `SCRAPE_API_KEY`. LLM model selection via `LLM_MODEL` (default: `google/gemma-4-31b-it`). See `.env.example` for all options with defaults.
```

Also update the Architecture table [publication.py](publication.py) row description — change "OpenAI extraction" to "LLM extraction (Parasail/Gemma)".

- [ ] **Step 2: Update `README.md`**

Search README.md for any references to `OpenAI` / `OPENAI_API_KEY` / `gpt-4o-mini`. Replace with the Parasail / `PARASAIL_API_KEY` / `google/gemma-4-31b-it` equivalents where they appear in configuration or setup sections. Leave any historical context or eval-pipeline mentions (eval still uses OpenRouter) alone.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: update env var and model references for Parasail/Gemma migration"
```

---

### Task 14: Production deployment note

**Files:** none (operator checklist)

- [ ] **Step 1: Document the production cutover steps**

This is a step for the PR description, not a code change. Include this block in the PR body:

```markdown
## Production cutover (manual on Lightsail)

Before merging:
1. SSH to Lightsail: `ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188`
2. Edit `/opt/econ-newsfeed/.env`:
   - Remove `OPENAI_API_KEY=...` and `OPENAI_MODEL=...`
   - Add `PARASAIL_API_KEY=ps-...`
   - (Optional) Add `LLM_MODEL=google/gemma-4-31b-it`
3. Verify the env file: `grep -E 'PARASAIL|LLM_MODEL' /opt/econ-newsfeed/.env`

After merging:
4. Deploy: `cd /opt/econ-newsfeed && ./scripts/deploy.sh`
5. Verify: `docker compose logs api | grep -i "using LLM"`
6. Confirm a fresh entry in `llm_usage` with `model = 'google/gemma-4-31b-it'` via the admin dashboard or SQL
```

Also add/update the GitHub Actions secret:
1. GitHub repo → Settings → Secrets and variables → Actions
2. Add new secret `PARASAIL_API_KEY` (used by CI)
3. Delete `OPENAI_API_KEY` secret after CI passes on `main`

- [ ] **Step 2: Open the PR**

Use the project's standard PR process. Link this plan file in the PR body.

---

## Self-Review Checklist

Completed before handoff:

- **Spec coverage:**
  - [x] Publication extraction migrated (Task 4)
  - [x] JEL classification migrated (Task 5)
  - [x] Description extraction migrated (Task 6)
  - [x] Researcher disambiguation migrated (Task 7)
  - [x] Batch submit/check migrated (Task 8)
  - [x] OpenAI client deleted (Task 11)
  - [x] Env vars renamed throughout (Tasks 9-10)
  - [x] Pricing table updated (Task 3)
  - [x] Docs updated (Task 13)
  - [x] Deployment steps documented (Task 14)

- **Placeholder scan:** no "TBD", "implement later", "add error handling"; every step has concrete code or a concrete command.

- **Type consistency:**
  - `extract_json` returns `StructuredResponse` with fields `parsed`, `usage` — used consistently in Tasks 4 and 5.
  - Callers reference `result.parsed` and `result.usage` everywhere — no drift.
  - `get_client` and `get_model` signatures match across `openai_client.py` (old) and `llm_client.py` (new) so call sites only need to change the import path.
  - `StructuredResponse.usage is None` → API error; `StructuredResponse.parsed is None` → validation failed. Callers handle both by returning `[]`.

- **Known risks:**
  1. **Gemma 4 31B may not honor complex nested JSON schemas as tightly as gpt-4o-mini.** Mitigation: `extract_json` retries once with an intensified prompt. If failure rate is unacceptable after Task 12's smoke test, fall back to `response_format={"type": "json_object"}` + prompt-only schema, or switch model (the eval config already has alternatives).
  2. **Parasail batch latency is unknown.** Plan does not block on batch_check succeeding during smoke test — documented as "pending" re-run.
  3. **Parasail pricing is taken from the OpenRouter eval config.** Real Parasail invoices may differ. Task 3 pricing constants are the single source of truth and should be reviewed against the first real invoice.
  4. **No dev-env Parasail key yet.** Operator must obtain one before Task 12.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-09-migrate-llm-to-parasail-gemma.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for this plan because tasks are cleanly separable and each touches 1-3 files.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Note:** Before starting either, create a git worktree via `superpowers:using-git-worktrees` so the migration happens on an isolated branch.

Which approach?
