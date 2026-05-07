# Migrate LLM Provider: OpenAI → Google AI Studio (Gemini 2.5 Flash) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace OpenAI with Google AI Studio's Gemini 2.5 Flash as the LLM backing all parsing, classification, and disambiguation calls — including the Batch API flow — by adapting the existing `feature/migrate-llm-parasail-gemma` branch.

**Architecture:** Google AI Studio exposes an OpenAI-compatible HTTPS endpoint at `https://generativelanguage.googleapis.com/v1beta/openai/` including `/chat/completions`. We continue using the `openai` Python SDK but point it at Google via `base_url` and an `api_key` from `GOOGLE_API_KEY`. The existing branch already migrated all call sites from `openai_client.py` to `llm_client.py` with `extract_json()` + `StructuredResponse` — we rebase that branch onto `main`, then pivot all Parasail references (env vars, base URL, model ID, pricing) to Google AI Studio. Google's Batch API also supports OpenAI SDK compatibility.

**Tech Stack:** Python 3, `openai` SDK (repointed at Google AI Studio), Pydantic v2, MySQL, pytest.

---

## Context and Constraints

### What the existing branch already did

Branch `feature/migrate-llm-parasail-gemma` (21 commits) completed Tasks 1–11 and 13 of the original plan:
- Created `llm_client.py` with `get_client()`, `get_model()`, `extract_json()` (OpenAI SDK repointed at Parasail)
- Migrated all 5 call sites: `publication.py`, `jel_classifier.py`, `html_fetcher.py`, `database/researchers.py`, `main.py` (batch)
- Renamed env vars from `OPENAI_API_KEY`/`OPENAI_MODEL` to `PARASAIL_API_KEY`/`LLM_MODEL`
- Updated all ~20 test files' env defaults
- Deleted `openai_client.py` and scratch scripts
- Updated CLAUDE.md, README.md, CI, docker-compose

### What needs to change for Google AI Studio

| Area | Parasail (current branch) | Google AI Studio (target) |
|------|--------------------------|--------------------------|
| Base URL | `https://api.parasail.io/v1` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| API key env var | `PARASAIL_API_KEY` | `GOOGLE_API_KEY` |
| Default model | `google/gemma-4-31b-it` | `gemini-2.5-flash` |
| Pricing (prompt/1M) | $0.14 | $0.30 |
| Pricing (completion/1M) | $0.40 | $2.50 |
| Batch discount | 1.0× (no discount) | 0.5× (50% off) |
| `response_format` | `json_schema` with `strict: False` | `json_schema` with `strict: False` (same — OpenAI compat) |

### Merge conflicts with main

The branch diverged before 6 commits landed on main. `git merge-tree` shows 5 conflicting files:
1. **`CLAUDE.md`** — main added batch pipeline docs; branch rewrote OpenAI → Parasail references. Resolution: take both (new batch docs + updated LLM references).
2. **`main.py`** — main added `batch-submit`/`batch-check` CLI subparsers and `append_snapshots_for_pubs` import. Branch removed `append_snapshots_for_pubs` import and has no CLI subparsers. Resolution: keep main's CLI subparsers + `append_snapshots_for_pubs` import, keep branch's LLM migration in `batch_submit`/`batch_check` bodies.
3. **`publication.py`** — main added `append_snapshots_for_pubs` helper; branch rewrote `extract_publications`. Both changes are additive. Resolution: keep both.
4. **`database/researchers.py`** — trivial: branch added `max_tokens=256` and `or ""` guard. Take branch's version.
5. **`pyproject.toml`** — main added `[tool.pytest.ini_options]` section. Branch removed `openai` dependency changes. Take both.

### Batch API note

Google AI Studio's Batch API is OpenAI-SDK compatible. The batch request JSONL format (`/v1/chat/completions` endpoint) is identical. The batch functions in `main.py` should work without structural changes — only the client base URL, API key, and model ID change (already handled by `llm_client.get_client()`/`get_model()`).

### Key compatibility note

Google AI Studio's OpenAI-compat endpoint uses `response_format` with `json_schema` type. The existing `extract_json()` helper already builds this format. The `strict: False` flag is fine — Google ignores it and applies its own schema guidance.

---

## File Structure

### Modified files (from branch state)

- `llm_client.py` — Change `PARASAIL_BASE_URL` → Google AI Studio URL, `DEFAULT_MODEL` → `gemini-2.5-flash`, env var `PARASAIL_API_KEY` → `GOOGLE_API_KEY`
- `database/llm.py` — Replace pricing entry, restore 0.5× batch discount
- `db_config.py` — `PARASAIL_API_KEY` → `GOOGLE_API_KEY` in `REQUIRED_ENV_VARS`
- `scripts/check_env.py` — Same env var swap
- `.env.example` — Rename env var section + defaults
- `docker-compose.yml` — Env var passthrough rename
- `.github/workflows/ci.yml` — CI env var rename
- `CLAUDE.md` — Update provider references (Parasail → Google AI Studio)
- `README.md` — Update provider references
- `tests/test_llm_client.py` — Update base URL assertion, model name assertions, env var name
- `tests/test_llm_pricing.py` — Update model name, pricing assertions, batch discount test
- All other test files — `PARASAIL_API_KEY` → `GOOGLE_API_KEY` in `os.environ.setdefault` lines
- `tests/conftest.py` — Same env var swap

### No new files

All files already exist on the branch. This plan only modifies them.

---

## Phase 1: Rebase and Resolve Conflicts

### Task 1: Rebase the migration branch onto main

**Files:** All files on branch (conflict resolution in 5 files)

- [ ] **Step 1: Fetch latest and check out the branch**

```bash
git fetch origin
git checkout feature/migrate-llm-parasail-gemma
```

- [ ] **Step 2: Start the rebase**

```bash
git rebase main
```

Expected: rebase stops with conflicts in up to 5 files. The conflicts occur across multiple commits as they replay. Handle each conflict commit by commit as the rebase progresses.

- [ ] **Step 3: Resolve conflicts commit by commit**

For each conflicting commit during rebase, resolve as follows:

**`CLAUDE.md`** — Accept the branch's Parasail/Gemma wording changes (we'll update to Google AI Studio in a later task). For sections that main added (batch pipeline docs, granular stages), keep main's additions. The key merge is in the "Commands" section where main added `make batch-submit` / `make batch-check` — keep those lines.

**`main.py`** — The branch removed `append_snapshots_for_pubs` from the import and doesn't have `batch-submit`/`batch-check` CLI subparsers. Resolve by:
- Keeping main's import line: `from publication import Publication, reconcile_title_renames, validate_publication, append_snapshots_for_pubs`
- Keeping main's CLI subparsers (`batch-submit`, `batch-check`) and their dispatch in `main()`
- Keeping the branch's `batch_submit()` and `batch_check()` function bodies (which use `llm_client`)

**`publication.py`** — Keep main's `append_snapshots_for_pubs` function. Keep the branch's rewritten `extract_publications` (uses `extract_json`). Both changes are in different parts of the file.

**`database/researchers.py`** — Take the branch's version (adds `max_tokens=256` and `or ""` content guard). These are small additive changes.

**`pyproject.toml`** — Keep main's `[tool.pytest.ini_options]` section. Keep the branch's dependency changes.

After resolving each commit's conflicts:

```bash
git add -A
git rebase --continue
```

- [ ] **Step 4: Verify the rebase completed**

```bash
git log --oneline main..HEAD | wc -l
```

Expected: 21 commits (same count as before rebase).

- [ ] **Step 5: Run the test suite to verify rebase didn't break anything**

```bash
poetry run pytest
```

Expected: all tests pass (tests still reference `PARASAIL_API_KEY` — that's fine, we'll update in Phase 2).

---

## Phase 2: Pivot from Parasail to Google AI Studio

### Task 2: Update `llm_client.py` — base URL, model, env var

**Files:**
- Modify: `llm_client.py`
- Modify: `tests/test_llm_client.py`

- [ ] **Step 1: Update the test assertions**

In `tests/test_llm_client.py`, make these changes:

1. Replace the env var default at the top of the file:
```python
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
```
(Replace the line `os.environ.setdefault("PARASAIL_API_KEY", "ps-test-key")`)

2. In `TestGetClient.test_returns_openai_client_pointed_at_parasail`, rename the method and update the assertion:
```python
def test_returns_openai_client_pointed_at_google_ai_studio(self):
    import llm_client
    llm_client._client = None  # reset module cache
    client = llm_client.get_client()
    assert str(client.base_url).rstrip("/") == "https://generativelanguage.googleapis.com/v1beta/openai"
```

3. In `TestGetModel.test_default_model_is_gemma_4_31b`, rename and update:
```python
def test_default_model_is_gemini_flash(self, monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    import llm_client
    assert llm_client.get_model() == "gemini-2.5-flash"
```

4. In `TestExtractJson.test_uses_json_schema_response_format`, update the model assertion:
```python
assert kwargs["model"] == "gemini-2.5-flash"
```

5. Update the module docstring:
```python
"""Unit tests for llm_client — Google AI Studio-backed OpenAI-compatible client."""
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/test_llm_client.py -v
```

Expected: 3+ failures (wrong base URL, wrong default model).

- [ ] **Step 3: Update `llm_client.py`**

Replace the module docstring and constants:

```python
"""Centralized Google AI Studio LLM client — OpenAI-compatible, lazy-initialized.

Importing this module does not require GOOGLE_API_KEY to be set;
the key is read on first client access so test environments that stub
the client can import without crashing.
"""
```

Replace `PARASAIL_BASE_URL` and `DEFAULT_MODEL`:

```python
GOOGLE_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-2.5-flash"
```

In `get_client()`, update the constructor:

```python
def get_client() -> OpenAI:
    """Return a shared OpenAI SDK instance pointed at Google AI Studio."""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=GOOGLE_AI_STUDIO_BASE_URL,
            api_key=os.environ.get("GOOGLE_API_KEY"),
        )
    return _client
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/test_llm_client.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add llm_client.py tests/test_llm_client.py
git commit -m "feat(llm): pivot llm_client from Parasail to Google AI Studio"
```

---

### Task 3: Update pricing table and batch discount in `database/llm.py`

**Files:**
- Modify: `database/llm.py`
- Modify: `tests/test_llm_pricing.py`

- [ ] **Step 1: Update the pricing test**

In `tests/test_llm_pricing.py`:

1. Replace the env var default:
```python
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
```
(Replace `os.environ.setdefault("PARASAIL_API_KEY", "ps-test")`)

2. Replace `TestLlmPricing.test_gemma_4_31b_cost_computed`:
```python
@patch("database.llm.execute_query")
def test_gemini_flash_cost_computed(self, mock_exec):
    from database.llm import log_llm_usage
    usage = MagicMock()
    usage.prompt_tokens = 1_000_000
    usage.completion_tokens = 1_000_000
    usage.total_tokens = 2_000_000

    log_llm_usage("publication_extraction", "gemini-2.5-flash", usage)

    assert mock_exec.called
    row = mock_exec.call_args[0][1]
    cost = row[6]
    assert cost is not None
    # $0.30/M prompt + $2.50/M completion = $2.80 total
    assert abs(float(cost) - 2.80) < 1e-6
```

3. Replace `test_batch_multiplier_is_one_on_parasail`:
```python
@patch("database.llm.execute_query")
def test_batch_multiplier_is_half(self, mock_exec):
    from database.llm import log_llm_usage
    usage = MagicMock()
    usage.prompt_tokens = 1_000_000
    usage.completion_tokens = 0
    usage.total_tokens = 1_000_000

    log_llm_usage("publication_extraction", "gemini-2.5-flash", usage, is_batch=True)

    row = mock_exec.call_args[0][1]
    cost = row[6]
    # $0.30/M prompt × 0.5 batch discount = $0.15
    assert abs(float(cost) - 0.15) < 1e-6
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/test_llm_pricing.py -v
```

Expected: failures (wrong model name, wrong pricing).

- [ ] **Step 3: Update `database/llm.py`**

Replace the pricing dict and batch multiplier comment:

```python
# (prompt, completion) cost per 1M tokens — Google AI Studio Gemini 2.5 Flash.
# Source: https://ai.google.dev/gemini-api/docs/pricing (May 2026).
_LLM_PRICING = {
    "gemini-2.5-flash": (0.30, 2.50),
}
```

Replace the batch multiplier block (around line 27-30 on the branch):

```python
            multiplier = 0.5 if is_batch else 1.0
```

Remove the comment about Parasail not offering a batch discount.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/test_llm_pricing.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add database/llm.py tests/test_llm_pricing.py
git commit -m "feat(llm): update pricing for Gemini 2.5 Flash, restore batch discount"
```

---

### Task 4: Rename env var in config, CI, Docker, and .env.example

**Files:**
- Modify: `db_config.py`
- Modify: `scripts/check_env.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Update `db_config.py`**

Replace `'PARASAIL_API_KEY'` with `'GOOGLE_API_KEY'` in the `REQUIRED_ENV_VARS` list:

```python
REQUIRED_ENV_VARS = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'GOOGLE_API_KEY']
```

- [ ] **Step 2: Update `scripts/check_env.py`**

Replace `"PARASAIL_API_KEY"` with `"GOOGLE_API_KEY"` in the `REQUIRED_VARS` list:

```python
REQUIRED_VARS = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "GOOGLE_API_KEY", "SCRAPE_API_KEY"]
```

- [ ] **Step 3: Update `.env.example`**

Replace the LLM provider section:

```
# LLM provider: Google AI Studio (required)
# Get an API key at https://aistudio.google.com/apikey
GOOGLE_API_KEY=your-google-api-key-here
# Gemini model ID. Default: Gemini 2.5 Flash.
LLM_MODEL=gemini-2.5-flash
```

- [ ] **Step 4: Update `docker-compose.yml`**

Replace the `PARASAIL_API_KEY` line in the `api` service `environment` block:

```yaml
      GOOGLE_API_KEY: ${GOOGLE_API_KEY:?GOOGLE_API_KEY is required}
      LLM_MODEL: ${LLM_MODEL:-gemini-2.5-flash}
```

- [ ] **Step 5: Update `.github/workflows/ci.yml`**

Replace the env vars in the `backend` job:

```yaml
      LLM_MODEL: "gemini-2.5-flash"
      GOOGLE_API_KEY: "test-not-real"
```

- [ ] **Step 6: Run the full test suite**

```bash
poetry run pytest
```

Expected: failures in tests that still `setdefault("PARASAIL_API_KEY", ...)` since `db_config.py` now requires `GOOGLE_API_KEY`. This is expected and will be fixed in Task 5.

- [ ] **Step 7: Commit**

```bash
git add db_config.py scripts/check_env.py .env.example docker-compose.yml .github/workflows/ci.yml
git commit -m "chore(env): rename PARASAIL_API_KEY to GOOGLE_API_KEY"
```

---

### Task 5: Update env var defaults in all test files

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_publication_extraction.py`
- Modify: `tests/test_jel_classifier.py`
- Modify: `tests/test_db_config.py`
- Modify: `tests/test_db_config_charset.py`
- Modify: `tests/test_doi_resolver.py`
- Modify: `tests/test_jel_enrichment.py`
- Modify: `tests/test_researcher_fields.py`
- Modify: `tests/test_encoding_researchers.py`
- Modify: `tests/test_encoding_openalex.py`
- Modify: `tests/test_encoding_integration.py`
- Modify: `tests/test_admin_dashboard.py`
- Modify: `tests/test_audit_encoding.py`
- Modify: `tests/test_papers.py`
- Modify: `tests/test_openalex.py`
- Modify: `tests/test_researcher_disambiguation.py`
- Modify: `tests/test_topic_jel_map.py`
- Modify: `tests/test_imports.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Bulk replace in all test files**

In every file listed above, apply this exact replacement:

- `os.environ.setdefault("PARASAIL_API_KEY", "ps-test-key")` → `os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")`
- `os.environ.setdefault("PARASAIL_API_KEY", "ps-test")` → `os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")`

Also in `tests/conftest.py`:
- `os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")` → `os.environ.setdefault("LLM_MODEL", "gemini-2.5-flash")`

In `tests/test_db_config.py`, find the `_reload_db_config` helper and its references:
- Replace `"PARASAIL_API_KEY"` with `"GOOGLE_API_KEY"` in all `env_overrides` dicts and `os.environ` cleanup loops
- In the docstring: replace "Clears all DB/PARASAIL env vars" with "Clears all DB/GOOGLE env vars"
- In test assertions: replace `"PARASAIL_API_KEY"` with `"GOOGLE_API_KEY"` in the expected `REQUIRED_ENV_VARS` list

In `tests/test_publication_extraction.py`:
- `os.environ.setdefault("LLM_MODEL", "google/gemma-4-31b-it")` → `os.environ.setdefault("LLM_MODEL", "gemini-2.5-flash")`

- [ ] **Step 2: Run the full pytest suite**

```bash
poetry run pytest
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test: swap PARASAIL_API_KEY env defaults to GOOGLE_API_KEY across all tests"
```

---

## Phase 3: Update Documentation

### Task 6: Update CLAUDE.md, README.md, and plan doc references

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Apply these replacements throughout CLAUDE.md:
- `Parasail/Gemma` → `Google AI Studio/Gemini`
- `Parasail` → `Google AI Studio` (in prose, not code blocks)
- `PARASAIL_API_KEY` → `GOOGLE_API_KEY`
- `google/gemma-4-31b-it` → `gemini-2.5-flash`
- `Parasail LLM client` → `Google AI Studio LLM client` (in the architecture table for `llm_client.py`)

In the Configuration section, ensure it reads:
```
Required env vars: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `GOOGLE_API_KEY`, `SCRAPE_API_KEY`. LLM model selection via `LLM_MODEL` (default: `gemini-2.5-flash`). See `.env.example` for all options with defaults.
```

- [ ] **Step 2: Update README.md**

Apply these replacements:
- `Parasail (Gemma 4 31B)` → `Google AI Studio (Gemini 2.5 Flash)`
- `PARASAIL_API_KEY` → `GOOGLE_API_KEY`
- `Parasail API key for LLM inference (required)` → `Google AI Studio API key (required)`
- `LLM model ID (default: \`google/gemma-4-31b-it\`)` → `LLM model ID (default: \`gemini-2.5-flash\`)`
- `set PARASAIL_API_KEY` → `set GOOGLE_API_KEY`

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: update all references from Parasail/Gemma to Google AI Studio/Gemini"
```

---

## Phase 4: Smoke Test and Ship

### Task 7: Full-stack smoke test against Google AI Studio

**Files:** none (runtime validation)

- [ ] **Step 1: Verify the Google API key is set**

```bash
grep GOOGLE_API_KEY .env | head -1 | sed 's/=.*/=***/'
```

Expected: `GOOGLE_API_KEY=***`

- [ ] **Step 2: Run the full test suite**

```bash
poetry run pytest && cd app && npx jest && cd ..
```

Expected: all pass.

- [ ] **Step 3: Reset and seed the local DB**

```bash
make reset-db
make seed
```

- [ ] **Step 4: Run `make fetch` to download researcher HTML**

```bash
make fetch
```

Expected: HTML downloaded for researcher URLs. Some URLs should show as "changed" in logs.

- [ ] **Step 5: Run `make scrape` to test the full extraction pipeline**

```bash
make scrape
```

Expected:
- No `ModuleNotFoundError` or `KeyError('PARASAIL_API_KEY')` or `KeyError('OPENAI_API_KEY')`
- Logs show extraction using LLM with `gemini-2.5-flash`
- Papers appear in the `papers` table
- `llm_usage` rows show model = `gemini-2.5-flash` and non-NULL `estimated_cost_usd`

Verify:

```bash
poetry run python -c "
from database import Database
rows = Database.fetch_all('SELECT model, call_type, COUNT(*) as n, SUM(estimated_cost_usd) as cost FROM llm_usage GROUP BY model, call_type')
for r in rows: print(r)
"
```

- [ ] **Step 6: Test Batch API flow**

```bash
poetry run python main.py batch-submit
```

Expected: a batch is submitted and logged. Check `batch_jobs` table for a new row.

Then (may need to wait for batch completion):

```bash
poetry run python main.py batch-check
```

If the batch hasn't completed yet, note the batch ID and re-run `batch-check` later. This is non-blocking.

- [ ] **Step 7: Commit any fixups**

Only if steps above revealed bugs. Otherwise skip.

---

### Task 8: Open the PR

**Files:** none

- [ ] **Step 1: Push and create the PR**

```bash
git push -u origin feature/migrate-llm-parasail-gemma
```

Create the PR with body including:

```markdown
## Summary
- Migrates all LLM calls from OpenAI to Google AI Studio (Gemini 2.5 Flash)
- Uses OpenAI SDK pointed at Google's OpenAI-compatible endpoint
- All structured output via `llm_client.extract_json()` with JSON schema guidance + Pydantic validation
- Batch API flow preserved via Google's OpenAI-compatible batch endpoint
- 50% batch discount restored (Google offers this, Parasail did not)

## Production cutover (manual on Lightsail)

Before merging:
1. SSH to Lightsail: `ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem ubuntu@18.195.185.188`
2. Edit `/opt/econ-newsfeed/.env`:
   - Remove `OPENAI_API_KEY=...` and `OPENAI_MODEL=...`
   - Add `GOOGLE_API_KEY=...`
   - (Optional) Add `LLM_MODEL=gemini-2.5-flash`
3. Verify: `grep -E 'GOOGLE_API_KEY|LLM_MODEL' /opt/econ-newsfeed/.env`

After merging:
4. Deploy: `cd /opt/econ-newsfeed && ./scripts/deploy.sh`
5. Verify: `docker compose logs api | grep -i "using LLM"`

Also update GitHub Actions secret:
1. Repo → Settings → Secrets → Actions
2. Add `GOOGLE_API_KEY` secret
3. Delete `OPENAI_API_KEY` secret after CI passes on main

## Test plan
- [ ] `poetry run pytest` — all pass
- [ ] `cd app && npx jest` — all pass
- [ ] `make scrape` against live Google AI Studio — extracts papers with `gemini-2.5-flash`
- [ ] `make batch-submit` + `make batch-check` — batch flow works
- [ ] Verify `llm_usage` rows show correct model and cost
```

---

## Self-Review Checklist

- **Spec coverage:**
  - [x] Rebase onto main to pick up 6 missing commits (Task 1)
  - [x] Base URL pivoted to Google AI Studio (Task 2)
  - [x] Default model changed to `gemini-2.5-flash` (Task 2)
  - [x] Env var renamed from `PARASAIL_API_KEY` → `GOOGLE_API_KEY` (Tasks 2, 4, 5)
  - [x] Pricing updated for Gemini 2.5 Flash (Task 3)
  - [x] Batch discount restored to 0.5× (Task 3)
  - [x] All test files updated (Tasks 2, 3, 5)
  - [x] Docs updated (Task 6)
  - [x] Smoke test against live API (Task 7)
  - [x] PR with production cutover checklist (Task 8)

- **Placeholder scan:** no "TBD", "implement later", "add error handling". Every step has concrete code or commands.

- **Type consistency:**
  - `GOOGLE_AI_STUDIO_BASE_URL` used in `llm_client.py` and tested in `test_llm_client.py` — consistent.
  - `gemini-2.5-flash` used as default model in `llm_client.py`, `.env.example`, `docker-compose.yml`, CI, all test files — consistent.
  - `GOOGLE_API_KEY` used in `db_config.py`, `check_env.py`, `.env.example`, `docker-compose.yml`, CI, all test files — consistent.

- **Known risks:**
  1. **Google's OpenAI-compat endpoint may have quirks** with `response_format.json_schema`. Mitigation: `extract_json` retries once with clarification prompt. If failure rate is high, fall back to `response_format={"type": "json_object"}` + prompt-only schema.
  2. **Batch API availability** — Google's batch API is newer and may have occasional failures. The plan doesn't block on batch-check succeeding during smoke test.
  3. **Pricing may change.** The `_LLM_PRICING` dict is the single source of truth and should be reviewed periodically.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-07-migrate-llm-google-ai-studio.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
