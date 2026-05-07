# Granular Scraping Pipeline Stages

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow HTML fetching, batch LLM submission, and batch result parsing to run as independent CLI commands and Makefile targets without breaking feed events, snapshot counts, or paper dedup.

**Architecture:** Expose `batch-submit` and `batch-check` as CLI subcommands (like `download`/`enrich` already are). Fix two bugs in `batch_check()`: (1) missing `validate_publication()` calls that let garbage through, (2) missing `append_paper_snapshot()` calls that skip paper versioning and status-change feed events. Add Makefile targets for the new commands.

**Tech Stack:** Python, argparse CLI, existing `main.py` + `publication.py` + `html_fetcher.py`

---

## Analysis: Current State

**What already works independently:**
- `make fetch` — HTML fetching only, sets `content_hash` in `html_content`, archives snapshots. Safe standalone.
- `make enrich` — OpenAlex enrichment. Safe standalone.

**What exists but is not exposed:**
- `batch_submit()` in `main.py:67-144` — submits URLs needing extraction to OpenAI Batch API. Only callable via Python import.
- `batch_check()` in `main.py:155-289` — polls batch jobs, parses results, saves publications. Only callable via Python import.

**Bugs in the batch path that would cause data integrity issues:**
1. **Missing `validate_publication()`**: The scheduler path runs `validate_publication()` inside `extract_publications()` (publication.py:546-552) to filter garbage (software packages, website noise, hallucinations). The batch path in `batch_check()` only does Pydantic structural validation — no content quality filtering. This means batch results can include garbage publications that the real-time path would reject.
2. **Missing `append_paper_snapshot()`**: The scheduler calls `Database.append_paper_snapshot()` for every extracted paper (scheduler.py:218-236). The batch path in `batch_check()` skips this entirely. This means: (a) no paper version history, (b) no `status_change` feed events since those are created inside `append_paper_snapshot()`, (c) paper metadata (status, venue, etc.) doesn't get denormalized to the `papers` table from snapshots.

**Feed event safety (already handled):**
- `_title_in_previous_snapshot()` check is already wired into `save_publications()` — both new-paper and duplicate paths. This protects against false `new_paper` events regardless of which code path calls `save_publications()`.
- `_url_has_baseline()` requires ≥2 html_snapshots before allowing `new_paper` events.
- DB trigger `trg_feed_events_snapshot_guard` provides a safety net.

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `main.py` | Modify | Add `batch-submit` and `batch-check` CLI subcommands; fix `batch_check()` bugs |
| `Makefile` | Modify | Add `batch-submit` and `batch-check` targets |
| `tests/test_batch_pipeline.py` | Create | Tests for batch validation and snapshot gaps |

---

### Task 1: Add `validate_publication()` to batch result processing

**Files:**
- Modify: `main.py:237-252` (batch_check result processing loop)
- Test: `tests/test_batch_pipeline.py`

The batch path parses LLM results via Pydantic but skips `validate_publication()` — the content quality filter that catches software packages, website noise, and hallucinations. The real-time path runs this in `extract_publications()` (publication.py:546-552).

- [ ] **Step 1: Write failing test**

Create `tests/test_batch_pipeline.py`:

```python
"""Tests for batch pipeline data integrity — validation and snapshots."""
import os
import sys

# Env vars must be set before any app imports
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "econ_app")
os.environ.setdefault("DB_PASSWORD", "secret")
os.environ.setdefault("DB_NAME", "econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

import unittest
from unittest.mock import patch, MagicMock

from publication import validate_publication, PublicationExtraction


class TestBatchValidationGap(unittest.TestCase):
    """batch_check must run validate_publication() on each parsed result."""

    def test_garbage_publication_rejected_by_validate(self):
        """A software-package-like extraction should be rejected by validate_publication."""
        garbage = {
            "title": "react-dom",
            "authors": [["", ""]],
            "year": None,
            "venue": None,
            "status": None,
            "draft_url": None,
            "abstract": None,
        }
        self.assertFalse(validate_publication(garbage))

    def test_valid_publication_accepted_by_validate(self):
        """A real economics paper should pass validate_publication."""
        valid = {
            "title": "Monetary Policy Shocks and Exchange Rate Dynamics",
            "authors": [["John", "Smith"], ["Jane", "Doe"]],
            "year": "2024",
            "venue": "American Economic Review",
            "status": "published",
            "draft_url": None,
            "abstract": "We study the effect of monetary policy on exchange rates.",
        }
        self.assertTrue(validate_publication(valid))

    def test_pydantic_valid_but_content_invalid(self):
        """Pydantic accepts structurally valid garbage — validate_publication must catch it."""
        # This passes Pydantic (valid structure) but is garbage content
        item = {
            "title": "x",
            "authors": [["A", "B"]],
        }
        pub = PublicationExtraction(**item)
        dumped = pub.model_dump()
        # Pydantic accepts it
        self.assertIsNotNone(dumped)
        # But validate_publication should reject it (title too short / garbage)
        self.assertFalse(validate_publication(dumped))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it passes (these test existing functions)**

Run: `poetry run pytest tests/test_batch_pipeline.py -v`
Expected: All 3 tests PASS (they test existing `validate_publication` behavior)

- [ ] **Step 3: Add `validate_publication()` to batch_check result loop**

In `main.py`, add the import at the top (line 7):

Change:
```python
from publication import Publication, reconcile_title_renames
```
To:
```python
from publication import Publication, reconcile_title_renames, validate_publication
```

Then in `batch_check()`, after Pydantic validation (around line 237-246), add the content quality filter:

Change:
```python
                validated = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    try:
                        pub = PublicationExtraction(**item)
                        validated.append(pub.model_dump())
                    except (ValidationError, TypeError) as e:
                        logging.warning(f"Rejected malformed batch publication: {e}")
```

To:
```python
                validated = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    try:
                        pub = PublicationExtraction(**item)
                        d = pub.model_dump()
                        if validate_publication(d):
                            validated.append(d)
                        else:
                            logging.info("Batch validation dropped: %s", d.get("title", "<no title>"))
                    except (ValidationError, TypeError) as e:
                        logging.warning(f"Rejected malformed batch publication: {e}")
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/test_batch_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_batch_pipeline.py
git commit -m "fix: add validate_publication() to batch result processing

The batch path only did Pydantic structural validation but skipped the
content quality filter (validate_publication) that the real-time path
uses. This let garbage extractions (software packages, website noise)
through in batch mode."
```

---

### Task 2: Add `append_paper_snapshot()` to batch result processing

**Files:**
- Modify: `main.py:247-254` (batch_check, after save_publications)

The scheduler calls `append_paper_snapshot()` for every extracted paper (scheduler.py:218-236), creating paper version history and triggering `status_change` feed events. The batch path skips this entirely.

- [ ] **Step 1: Add snapshot creation to batch_check**

In `main.py`, after the `match_and_save_paper_links` call in `batch_check()` (around line 251), add the snapshot loop. This mirrors scheduler.py:218-236.

Change:
```python
                if validated:
                    is_seed = HTMLFetcher.is_first_extraction(url_id)
                    Publication.save_publications(url, validated, is_seed=is_seed)
                    reconcile_title_renames(url, validated)
                    match_and_save_paper_links(url_id, validated)
                    saved_pubs += len(validated)
                HTMLFetcher.mark_extracted(url_id)
```

To:
```python
                if validated:
                    is_seed = HTMLFetcher.is_first_extraction(url_id)
                    Publication.save_publications(url, validated, is_seed=is_seed)
                    reconcile_title_renames(url, validated)
                    match_and_save_paper_links(url_id, validated)

                    # Append paper snapshots (mirrors scheduler.py:218-236)
                    for pub in validated:
                        title_hash = Database.compute_title_hash(pub['title'])
                        paper_row = Database.fetch_one(
                            "SELECT id FROM papers WHERE title_hash = %s", (title_hash,)
                        )
                        if paper_row:
                            Database.append_paper_snapshot(
                                paper_id=paper_row['id'],
                                status=pub.get('status'),
                                venue=pub.get('venue'),
                                abstract=pub.get('abstract'),
                                draft_url=pub.get('draft_url'),
                                year=pub.get('year'),
                                source_url=url,
                                title=pub.get('title'),
                            )

                    saved_pubs += len(validated)
                HTMLFetcher.mark_extracted(url_id)
```

- [ ] **Step 2: Run full test suite**

Run: `poetry run pytest -v --tb=short`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "fix: add paper snapshots to batch result processing

The batch path skipped append_paper_snapshot() which meant: no paper
version history, no status_change feed events, and no denormalized
metadata updates. Now mirrors the scheduler's snapshot logic."
```

---

### Task 3: Expose `batch-submit` and `batch-check` as CLI subcommands

**Files:**
- Modify: `main.py:322-356` (CLI argument parser)

Currently `batch_submit()` and `batch_check()` are only callable via Python import. Add them as proper subcommands.

- [ ] **Step 1: Add subcommands to the argparse parser**

In `main.py`, in the `main()` function (around line 330-334), add:

```python
    subparsers.add_parser('batch-submit', help='Submit batch LLM extraction for URLs with new content')
    subparsers.add_parser('batch-check', help='Check pending batches and process completed results')
```

- [ ] **Step 2: Add handler branches**

In the `main()` function, after the `discover-domains` handler (around line 352-353), add:

```python
    elif args.command == 'batch-submit':
        batch_submit()
    elif args.command == 'batch-check':
        batch_check()
```

- [ ] **Step 3: Update the .PHONY line in Makefile**

In `Makefile` line 1, add `batch-submit batch-check` to the .PHONY list:

Change:
```makefile
.PHONY: setup dev kill seed reset-db scrape fetch classify-jel enrich enrich-jel discover-domains backfill-normalize populate-fields backfill-affiliations audit-zero-pubs check
```

To:
```makefile
.PHONY: setup dev kill seed reset-db scrape fetch batch-submit batch-check classify-jel enrich enrich-jel discover-domains backfill-normalize populate-fields backfill-affiliations audit-zero-pubs check
```

- [ ] **Step 4: Add Makefile targets**

After the `fetch:` target (line 35), add:

```makefile
batch-submit:
	poetry run python main.py batch-submit

batch-check:
	poetry run python main.py batch-check
```

- [ ] **Step 5: Verify CLI help works**

Run: `poetry run python main.py --help`
Expected: `batch-submit` and `batch-check` appear in the available commands

- [ ] **Step 6: Verify dry run of batch-submit**

Run: `poetry run python main.py batch-submit`
Expected: Either "Nothing to extract" (if no URLs need extraction) or a batch submission log. Should not error.

- [ ] **Step 7: Commit**

```bash
git add main.py Makefile
git commit -m "feat: expose batch-submit and batch-check as CLI commands

Adds argparse subcommands and Makefile targets so the batch LLM
pipeline stages can be run independently:
  make fetch          # Stage 1: download HTML
  make batch-submit   # Stage 2: submit to OpenAI Batch API
  make batch-check    # Stage 3: process completed batch results"
```

---

### Task 4: Update CLAUDE.md with new pipeline documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add batch commands to the Commands section**

In `CLAUDE.md`, after the `make fetch` line in the "Scraping pipeline" section, add:

```markdown
make batch-submit     # Stage 2: Submit changed URLs to OpenAI Batch API for extraction
make batch-check      # Stage 3: Process completed batch results → save papers, snapshots, links
```

- [ ] **Step 2: Add a "Granular Pipeline" subsection to Pipeline Details**

After the existing `make fetch` paragraph in Pipeline Details, add:

```markdown
**Granular batch pipeline** (run stages independently):
1. `make fetch` — Download HTML, detect changes via content hash. Safe to run anytime.
2. `make batch-submit` — Submit URLs where `content_hash ≠ extracted_hash` to OpenAI Batch API. Requires fetch first.
3. `make batch-check` — Poll pending batches, process results (save papers, snapshots, links, feed events). Idempotent for completed batches.

Each stage is independently safe. Feed event integrity is protected by `_title_in_previous_snapshot()` and the `_url_has_baseline()` check regardless of which pipeline path is used.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document granular batch pipeline stages in CLAUDE.md"
```

---

## Summary

| Task | What | Why |
|------|------|-----|
| 1 | Add `validate_publication()` to batch path | Prevents garbage extractions in batch mode |
| 2 | Add `append_paper_snapshot()` to batch path | Restores paper versioning + status_change events |
| 3 | Expose CLI subcommands + Makefile targets | Makes stages independently runnable |
| 4 | Update CLAUDE.md | Documents the new workflow |

**After implementation, the three-stage workflow is:**
```
make fetch          # Download HTML (safe, idempotent)
make batch-submit   # Submit to OpenAI Batch API (needs fetch first)
make batch-check    # Process results (needs batch-submit first)
```

All feed event guards (`_title_in_previous_snapshot`, `_url_has_baseline`, DB trigger) apply uniformly to both the real-time (`make scrape`) and batch paths.
