# Known-Papers Exclusion Prompt Design

**Date**: 2026-03-19
**Status**: Draft
**Goal**: Reduce ongoing per-scrape LLM costs by sending known papers in the extraction prompt so the model only returns new or changed publications.

## Problem

The current pipeline extracts ALL publications from a researcher's page on every scrape where content changed, then deduplicates on save via `INSERT IGNORE` with `title_hash`. This wastes ~86% of completion tokens on publications already in the database. Completion tokens cost 3.6-6x more than prompt tokens, making this the dominant cost driver.

## Approach

Send the list of papers already stored for a researcher to the LLM as part of the extraction prompt. Instruct it to return only publications NOT in that list, or where metadata (status, venue, year) has visibly changed. Use `gpt-5.4-mini` for the extraction model specifically (other LLM calls stay on their current model).

## Evidence

Tested across 5 real researcher URLs with 2 injected fake papers each:

| Model | Prompt type | Recall (new papers) | Known leaked | Completion tokens |
|-------|------------|---------------------|--------------|-------------------|
| gpt-5.4-nano | Original (current) | ~94% | N/A | 4,840 |
| gpt-5.4-nano | Exclusion | ~60-90% | 0 | 561 |
| gpt-5.4-mini | Exclusion | **100%** | 1 (of ~76 known) | 679 |

Cost per scrape (200 changed URLs, publication extraction only):

| Configuration | Cost/scrape | Cost/month (daily) |
|--------------|-------------|-------------------|
| Current: nano + original prompt | $0.295 | ~$9 |
| **New: mini + exclusion prompt** | **$0.364** | **~$11** |

The $2/month increase buys 100% recall (vs ~94%) and a cleaner pipeline.

## Changes

### 1. New env var: `EXTRACTION_MODEL`

Introduce `EXTRACTION_MODEL` env var (default: `gpt-5.4-mini`) used only for publication extraction in both the real-time path (`extract_publications`) and the batch path (`batch_submit`). The existing `OPENAI_MODEL` continues to govern description extraction and researcher disambiguation, so those calls are unaffected in both model and cost.

**Files**: `publication.py`, `main.py` (batch_submit), `.env.example`

### 2. New query: `Database.get_known_papers_for_researcher(researcher_id)`

Fetches existing paper titles and metadata for ALL papers associated with a researcher (across all their URLs). This is scoped per-researcher rather than per-URL so that papers discovered from one URL (e.g., a HOME page) are excluded when extracting from another URL (e.g., a PUBLICATIONS page) for the same researcher.

```sql
SELECT DISTINCT p.title, p.year, p.venue, p.status
FROM papers p
JOIN authorship a ON a.publication_id = p.id
WHERE a.researcher_id = %s
```

Returns `list[dict]` with keys: `title`, `year`, `venue`, `status`. The query returns all papers; capping and sorting happen in Python (see Section 3).

**File**: `database.py`

### 3. Modified prompt: `Publication.build_extraction_prompt()`

Add optional `known_papers: list[dict] | None` parameter to both `build_extraction_prompt` and `extract_publications` (which passes it through).

- When `known_papers` is provided and non-empty: use exclusion prompt that lists known titles with metadata and asks for only new/changed publications.
- When `None` or empty (first scrape, or query failure): fall back to current "extract all" prompt.
- Cap known papers list at 50 entries. Capping is done in Python inside `build_extraction_prompt`: sort by year descending (NULL years sort last), take the first 50. All 50 entries include full metadata (title, year, venue, status).

**File**: `publication.py`

### 4. Remove 4000-char truncation

The current `build_extraction_prompt` truncates page content to `text_content[:4000]`. Remove this truncation — the test showed that full page content works well with gpt-5.4-mini and the known-papers list. The `CONTENT_MAX_CHARS` env var in `html_fetcher.py` (default 4000) already caps text at the fetch/storage layer, so the prompt input is bounded there.

**File**: `publication.py` (remove `[:4000]` slice on line 186)

### 5. Updated callers

Each call site that invokes `extract_publications` gains one line to query known papers first.

**`scheduler.py` (line ~172)**: Inside `run_scrape_job`:
```python
known = Database.get_known_papers_for_researcher(researcher_id)
pubs = Publication.extract_publications(extraction_text, url, known_papers=known, scrape_log_id=log_id)
```

**`main.py` — `extract_data_from_htmls` and `_process_one_url`**:
```python
known = Database.get_known_papers_for_researcher(researcher_id)
pubs = Publication.extract_publications(html_content, url, known_papers=known)
```

**`main.py` — `batch_submit`**: Use `EXTRACTION_MODEL` (not `OPENAI_MODEL`) for the batch request body model field, and include known papers in each batch request's prompt. Note: batch requests use free-form JSON (no `response_format`), and `batch_check` parses results via `parse_openai_response()`. The exclusion prompt works with both paths — an empty-list response `[]` parses correctly.

### 6. Prompt text

Exclusion variant (used when `known_papers` is non-empty):

```
Extract NEW or CHANGED academic publications from the following researcher page content from {url}.

For each publication, extract:
- title: the full publication title
- authors: a list of [first_name, last_name] pairs. Use full first names when available.
- year: publication year as a string, or null if unknown
- venue: journal or conference name, or null if unknown
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", or null
- draft_url: a URL to a PDF, SSRN, NBER, or working paper version, or null
- abstract: the paper abstract, or null if not shown on the page

Return ONLY publications that are NOT in the known list below, or where metadata has visibly changed.
If there are no new or changed publications, return an empty list.
Do not fabricate publications.

The following publications are ALREADY KNOWN:
- {title} ({year}) — {status}, {venue}
- ...

Content:
{text_content}
```

When `known_papers` is empty/None, the current "extract all" prompt is used unchanged.

### 7. Handle metadata updates in save path

Currently `save_publications` uses `INSERT IGNORE`, which silently drops metadata updates for existing papers. When the exclusion prompt returns a known paper because its metadata changed, the update is lost.

Fix: after `INSERT IGNORE`, if the row was a duplicate (`lastrowid` is 0), check whether the returned metadata differs from stored and call `Database.append_paper_snapshot()` to capture the change. This moves snapshot logic into `save_publications`, covering all code paths (scheduler, CLI, batch).

Since `save_publications` will now handle snapshots, **remove the separate `append_paper_snapshot` loop in `scheduler.py` (lines ~184-201)** to avoid double-snapshotting.

**Files**: `publication.py` (`save_publications`), `scheduler.py` (remove snapshot loop)

## Interaction with diff logic

The scheduler computes a diff (added lines only) when old content exists and sends that to the LLM instead of the full page. The exclusion prompt and diff optimization compose correctly:

- **Diff reduces input tokens**: Only changed text goes to the LLM.
- **Exclusion reduces output tokens**: Known papers in the diff are skipped.
- **Limitation**: When a diff is sent, the LLM can't see unchanged metadata for known papers, so it can't detect metadata changes on papers not in the diff. This is acceptable — metadata changes only get detected on full-page extractions (first scrape, or when the page changes significantly enough that the diff is large). The `INSERT IGNORE` + snapshot safety net handles any edge cases.

## What doesn't change

- **`INSERT IGNORE` dedup**: Stays as a safety net for any leaked known papers.
- **Change detection**: Hash-based content change detection in `html_fetcher.py` stays.
- **`extract_relevant_html`**: No change to HTML parsing.
- **Description extraction / researcher disambiguation**: Use `OPENAI_MODEL`, not `EXTRACTION_MODEL`. Unaffected.
- **Batch check (`batch_check` in `main.py`)**: Processes results and saves publications. Works unchanged — fewer results from the exclusion prompt means less work, but the flow is the same.

## Risk mitigation

- **Known list too long**: Capped at 50 entries (most recent by year). Full metadata on all 50.
- **Query failure**: Falls back to original "extract all" prompt — no worse than today.
- **Model leaks known paper**: `INSERT IGNORE` dedup catches it on save. Test showed 1.3% leak rate (1 paper leaked out of ~76 known across 5 URLs) — acceptable with the safety net.
- **First scrape for a URL**: No known papers → original prompt used automatically.
- **Rollback**: Full rollback is a git revert of the code change — restores the original prompt and removes the known-papers query. For a quick partial rollback, set `EXTRACTION_MODEL` back to the previous model value; the exclusion prompt still runs but with the cheaper model. No schema changes or data migration in either case.

## Test plan

- Unit test: `build_extraction_prompt` returns exclusion variant when `known_papers` provided, original when empty/None.
- Unit test: `get_known_papers_for_researcher` returns correct papers joined via authorship.
- Unit test: known papers list is capped at 50, sorted by year descending.
- Unit test: `save_publications` calls `append_paper_snapshot` when a duplicate paper has changed metadata.
- Integration test: Mock OpenAI response to verify full flow (query known → build prompt → extract → save only new).
- Manual validation: `test_exclusion_prompt.py` script against real data.
