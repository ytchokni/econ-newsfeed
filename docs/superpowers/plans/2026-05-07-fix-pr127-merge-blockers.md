# Fix PR #127 Merge Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 4 blocking issues preventing the Google AI Studio migration PR (#127) from merging.

**Architecture:** The batch pipeline (`batch_submit`/`batch_check`) uses the OpenAI SDK for file upload and download, but Google AI Studio only supports those via the `google-genai` SDK. We add `google-genai` as a dependency and create a thin `get_genai_client()` helper in `llm_client.py`. The batch functions use genai for file upload/download and OpenAI SDK for batch create/retrieve/poll (which Google supports). Other fixes are cosmetic: commit the `/simplify` improvements already on disk, fix stale doc references, remove a duplicate line.

**Tech Stack:** Python, `google-genai` SDK, `openai` SDK, MySQL

---

## Context

PR #127 migrates all LLM calls from OpenAI to Google AI Studio. A code review found 4 blocking issues:

1. **Batch API file operations** — `client.files.create()` and `client.files.content()` are not supported via OpenAI SDK on Google AI Studio. Must use `google-genai` SDK for file upload/download. Batch create/retrieve/poll work fine via OpenAI SDK.
2. **Uncommitted `/simplify` fixes** — Thread-safety on `get_client()`, `build_json_schema_format()` extraction, and lazy `clarified_prompt` are on disk but not committed/pushed.
3. **Stale "OpenAI Batch API" references in CLAUDE.md** — Two lines still say "OpenAI Batch API".
4. **Duplicate line in `tests/conftest.py`** — `GOOGLE_API_KEY` is set twice.

Additionally, two non-blocking stale docstrings in test files reference "Parasail/Gemma".

---

### Task 1: Commit the uncommitted `/simplify` fixes

The `/simplify` review already applied 3 fixes to `llm_client.py` and `main.py` but they were never committed. They need to be staged and committed before any further changes.

**Files:**
- Already modified on disk: `llm_client.py`, `main.py`

- [ ] **Step 1: Verify the uncommitted changes are the expected ones**

Run: `git diff llm_client.py main.py`

Expected changes in `llm_client.py`:
- `import threading` added
- `_client_lock = threading.Lock()` added
- `get_client()` uses double-checked locking with `_client_lock`
- `build_json_schema_format()` function extracted
- `clarified_prompt` moved inside the retry loop (lazy-built)

Expected changes in `main.py`:
- `build_json_schema_format` imported from `llm_client`
- Inline `response_format` dict replaced with `build_json_schema_format(PublicationExtractionList)`

- [ ] **Step 2: Run tests to confirm nothing is broken**

Run: `poetry run python -m pytest tests/test_llm_client.py tests/test_batch_pipeline.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add llm_client.py main.py
git commit -m "refactor: thread-safe client init, extract build_json_schema_format helper

Add double-checked locking to get_client() for Gunicorn worker safety.
Extract build_json_schema_format() to deduplicate response_format
construction between extract_json() and batch_submit().
Lazy-build clarified_prompt only when a retry actually happens."
```

---

### Task 2: Add `google-genai` dependency and `get_genai_client()` helper

Google AI Studio's OpenAI-compatibility layer does not support `client.files.create()` or `client.files.content()`. The batch pipeline needs the `google-genai` SDK for file upload and download. Batch create/retrieve/poll continue to use the OpenAI SDK (supported by Google).

**Files:**
- Modify: `pyproject.toml` (add dependency)
- Modify: `llm_client.py` (add `get_genai_client()`)
- Test: `tests/test_llm_client.py` (add test)

- [ ] **Step 1: Write failing test for `get_genai_client()`**

Add to `tests/test_llm_client.py`:

```python
class TestGetGenaiClient(unittest.TestCase):
    def test_returns_genai_client(self):
        import llm_client
        llm_client._genai_client = None
        with unittest.mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key-123"}):
            client = llm_client.get_genai_client()
        from google import genai
        self.assertIsInstance(client, genai.Client)

    def test_client_is_cached(self):
        import llm_client
        llm_client._genai_client = None
        with unittest.mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key-123"}):
            c1 = llm_client.get_genai_client()
            c2 = llm_client.get_genai_client()
        self.assertIs(c1, c2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_llm_client.py::TestGetGenaiClient -v`
Expected: FAIL — `get_genai_client` doesn't exist yet, and `google-genai` isn't installed

- [ ] **Step 3: Add `google-genai` dependency**

Run: `poetry add google-genai`

This installs the `google-genai` package which provides `from google import genai`.

- [ ] **Step 4: Implement `get_genai_client()` in `llm_client.py`**

Add after the existing `_client_lock` declaration (around line 21):

```python
_genai_client = None
_genai_client_lock = threading.Lock()
```

Add after `get_client()` (around line 37):

```python
def get_genai_client():
    """Return a shared google-genai Client for file upload/download (batch pipeline)."""
    global _genai_client
    if _genai_client is None:
        with _genai_client_lock:
            if _genai_client is None:
                from google import genai
                _genai_client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _genai_client
```

- [ ] **Step 5: Run tests**

Run: `poetry run python -m pytest tests/test_llm_client.py -v`
Expected: All tests PASS (including the 2 new ones)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml poetry.lock llm_client.py tests/test_llm_client.py
git commit -m "feat: add google-genai SDK for batch file upload/download

Google AI Studio's OpenAI-compat layer doesn't support files.create()
or files.content(). The batch pipeline needs the native genai SDK for
file operations while using OpenAI SDK for batch create/retrieve."
```

---

### Task 3: Migrate `batch_submit()` file upload to genai SDK

Replace `client.files.create()` with `genai_client.files.upload()`. The batch creation via `client.batches.create()` stays on OpenAI SDK (supported by Google).

**Files:**
- Modify: `main.py:67-147` (`batch_submit` function)
- Test: `tests/test_batch_pipeline.py`

- [ ] **Step 1: Write test for batch_submit using genai file upload**

Add to `tests/test_batch_pipeline.py`:

```python
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import patch, MagicMock, ANY


class TestBatchSubmitFileUpload(unittest.TestCase):
    """batch_submit must use genai SDK for file upload, OpenAI SDK for batch create."""

    @patch("main.Database")
    @patch("main.Researcher")
    @patch("main.HTMLFetcher")
    @patch("main.Publication")
    def test_uses_genai_for_upload_and_openai_for_batch_create(
        self, mock_pub, mock_fetcher, mock_researcher, mock_db,
    ):
        mock_researcher.get_all_researcher_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "http://example.com", "page_type": "RESEARCH"},
        ]
        mock_fetcher.needs_extraction.return_value = True
        mock_fetcher.get_latest_text.return_value = "some text"
        mock_pub.build_extraction_prompt.return_value = "extract this"
        mock_db.fetch_all.return_value = []  # no pending batches

        mock_genai = MagicMock()
        mock_uploaded = MagicMock()
        mock_uploaded.name = "files/abc123"
        mock_genai.files.upload.return_value = mock_uploaded

        mock_openai = MagicMock()
        mock_batch = MagicMock()
        mock_batch.id = "batch_xyz"
        mock_openai.batches.create.return_value = mock_batch

        with patch("main.get_genai_client", return_value=mock_genai), \
             patch("main.get_client", return_value=mock_openai), \
             patch("main.get_model", return_value="gemini-2.5-flash"):
            from main import batch_submit
            batch_submit()

        # genai SDK used for file upload
        mock_genai.files.upload.assert_called_once()

        # OpenAI SDK used for batch create
        mock_openai.batches.create.assert_called_once_with(
            input_file_id="files/abc123",
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_batch_pipeline.py::TestBatchSubmitFileUpload -v`
Expected: FAIL — `batch_submit` still uses `client.files.create()`

- [ ] **Step 3: Rewrite `batch_submit()` file upload**

In `main.py`, change the import at the top of `batch_submit()`:

```python
def batch_submit() -> None:
    """Submit a batch job to the Gemini Batch API for all URLs needing extraction."""
    from llm_client import get_client, get_genai_client, get_model, build_json_schema_format
    from google.genai import types
    import json
    import tempfile
    from datetime import datetime, timezone
    from publication import PublicationExtractionList

    client = get_client()
    genai_client = get_genai_client()
    model = get_model()
```

Replace the file upload and batch creation section (the `try` block starting around line 128). Change from:

```python
    try:
        with open(tmp_path, "rb") as f:
            uploaded = client.files.create(file=f, purpose="batch")

        batch = client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
```

To:

```python
    try:
        uploaded = genai_client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(display_name="batch-extract", mime_type="jsonl"),
        )

        batch = client.batches.create(
            input_file_id=uploaded.name,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
```

Also update the DB insert to use `uploaded.name` instead of `uploaded.id`:

```python
        Database.execute_query(
            """INSERT INTO batch_jobs
               (openai_batch_id, input_file_id, status, url_count, created_at)
               VALUES (%s, %s, 'submitted', %s, %s)""",
            (batch.id, uploaded.name, len(lines), datetime.now(timezone.utc)),
        )
```

- [ ] **Step 4: Run tests**

Run: `poetry run python -m pytest tests/test_batch_pipeline.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_batch_pipeline.py
git commit -m "fix: use genai SDK for batch file upload

Google AI Studio's OpenAI-compat layer doesn't support files.create().
Use genai_client.files.upload() for file upload, keep OpenAI SDK for
batch create/retrieve which Google does support."
```

---

### Task 4: Migrate `batch_check()` file download to genai SDK

Replace `client.files.content(output_file_id).text` with `genai_client.files.download(file=output_file_id)`.

**Files:**
- Modify: `main.py:158-300` (`batch_check` function)
- Test: `tests/test_batch_pipeline.py`

- [ ] **Step 1: Write test for batch_check using genai file download**

Add to `tests/test_batch_pipeline.py`:

```python
import json


class TestBatchCheckFileDownload(unittest.TestCase):
    """batch_check must use genai SDK for file download."""

    @patch("main.match_and_save_paper_links")
    @patch("main.append_snapshots_for_pubs")
    @patch("main.reconcile_title_renames")
    @patch("main.HTMLFetcher")
    @patch("main.Database")
    def test_uses_genai_for_download(
        self, mock_db, mock_fetcher, mock_reconcile, mock_snapshots, mock_links,
    ):
        # One pending batch
        mock_db.fetch_all.return_value = [
            {"id": 1, "openai_batch_id": "batch_abc"},
        ]

        # OpenAI SDK: batch is completed with an output file
        mock_openai = MagicMock()
        mock_batch_obj = MagicMock()
        mock_batch_obj.status = "completed"
        mock_batch_obj.output_file_id = "files/output123"
        mock_openai.batches.retrieve.return_value = mock_batch_obj

        # genai SDK: file download returns JSONL with one valid result
        result_line = json.dumps({
            "custom_id": "url_1",
            "response": {
                "body": {
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                    "choices": [{
                        "message": {
                            "content": json.dumps({
                                "publications": [{
                                    "title": "Monetary Policy and Exchange Rates",
                                    "authors": [["John", "Smith"]],
                                    "year": "2024",
                                    "venue": "AER",
                                    "status": "published",
                                    "draft_url": None,
                                    "abstract": "We study monetary policy.",
                                }]
                            })
                        }
                    }],
                },
            },
        })
        mock_genai = MagicMock()
        mock_genai.files.download.return_value = result_line.encode("utf-8")

        mock_db.fetch_one.side_effect = [
            {"url": "http://example.com"},  # url lookup
            {"total_cost": 0.001},          # cost aggregation
        ]
        mock_fetcher.is_first_extraction.return_value = False

        with patch("main.get_genai_client", return_value=mock_genai), \
             patch("main.get_client", return_value=mock_openai), \
             patch("main.get_model", return_value="gemini-2.5-flash"):
            from main import batch_check
            batch_check()

        # genai SDK used for download
        mock_genai.files.download.assert_called_once_with(file="files/output123")

        # OpenAI SDK used for batch retrieve
        mock_openai.batches.retrieve.assert_called_once_with("batch_abc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run python -m pytest tests/test_batch_pipeline.py::TestBatchCheckFileDownload -v`
Expected: FAIL — `batch_check` still uses `client.files.content()`

- [ ] **Step 3: Rewrite `batch_check()` file download**

In `main.py`, change the import at the top of `batch_check()`:

```python
def batch_check() -> None:
    """Check pending batch jobs and process completed results."""
    from llm_client import get_client, get_genai_client, get_model
    from publication import PublicationExtraction, validate_publication
    from pydantic import ValidationError
    import json
    from datetime import datetime, timezone

    client = get_client()
    genai_client = get_genai_client()
    model = get_model()
```

Replace the file content download line (currently line 185):

From:
```python
            content = client.files.content(output_file_id).text
```

To:
```python
            content_bytes = genai_client.files.download(file=output_file_id)
            content = content_bytes.decode("utf-8") if isinstance(content_bytes, bytes) else content_bytes
```

- [ ] **Step 4: Run tests**

Run: `poetry run python -m pytest tests/test_batch_pipeline.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `poetry run python -m pytest --timeout=30 -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_batch_pipeline.py
git commit -m "fix: use genai SDK for batch result download

Google AI Studio's OpenAI-compat layer doesn't support
files.content(). Use genai_client.files.download() instead."
```

---

### Task 5: Fix stale references and duplicate conftest line

**Files:**
- Modify: `CLAUDE.md:26,67`
- Modify: `tests/conftest.py:12`
- Modify: `tests/test_jel_classifier.py:86`
- Modify: `tests/test_publication_extraction.py:95`

- [ ] **Step 1: Fix CLAUDE.md stale "OpenAI Batch API" references**

Line 26 — change:
```
make batch-submit         # Stage 2: Submit changed URLs to OpenAI Batch API for extraction
```
To:
```
make batch-submit         # Stage 2: Submit changed URLs to Gemini Batch API for extraction
```

Line 67 — change:
```
2. `make batch-submit` — Submit URLs where `content_hash ≠ extracted_hash` to OpenAI Batch API. Requires fetch first.
```
To:
```
2. `make batch-submit` — Submit URLs where `content_hash ≠ extracted_hash` to Gemini Batch API. Requires fetch first.
```

- [ ] **Step 2: Remove duplicate `GOOGLE_API_KEY` line in `tests/conftest.py`**

Remove line 12 (the duplicate):
```python
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")  # kept for db_config.py until it is migrated
```

Keep line 11:
```python
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
```

- [ ] **Step 3: Fix stale "Parasail/Gemma" docstrings in test files**

In `tests/test_jel_classifier.py` line 86, change:
```python
    """Build a mock OpenAI-compatible chat completion returning JSON content (Parasail/Gemma shape)."""
```
To:
```python
    """Build a mock OpenAI-compatible chat completion returning JSON content."""
```

In `tests/test_publication_extraction.py` line 95, change the identical docstring the same way.

- [ ] **Step 4: Run full test suite**

Run: `poetry run python -m pytest --timeout=30 -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md tests/conftest.py tests/test_jel_classifier.py tests/test_publication_extraction.py
git commit -m "fix: update stale OpenAI/Parasail references, remove duplicate conftest line

CLAUDE.md: 'OpenAI Batch API' → 'Gemini Batch API'
conftest.py: remove duplicate GOOGLE_API_KEY setdefault
test docstrings: remove stale 'Parasail/Gemma' references"
```

---

### Task 6: Push and verify CI

- [ ] **Step 1: Run full test suite one final time**

Run: `poetry run python -m pytest --timeout=30 -q`
Expected: All tests pass

- [ ] **Step 2: Push to remote**

Run: `git push origin feature/migrate-llm-parasail-gemma`

- [ ] **Step 3: Verify CI passes**

Run: `gh pr checks 127 --watch`
Expected: All checks pass

---

## Summary

| Task | What | Why |
|------|------|-----|
| 1 | Commit `/simplify` fixes already on disk | Thread-safety, deduplication, lazy prompt — already reviewed and tested |
| 2 | Add `google-genai` SDK + `get_genai_client()` | Required for batch file upload/download on Google AI Studio |
| 3 | Migrate `batch_submit()` file upload | `client.files.create()` not supported via OpenAI compat layer |
| 4 | Migrate `batch_check()` file download | `client.files.content()` not supported via OpenAI compat layer |
| 5 | Fix stale refs + duplicate conftest | Cosmetic cleanup for merge readiness |
| 6 | Push + verify CI | Final validation |
