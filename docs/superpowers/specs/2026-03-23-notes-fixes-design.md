# Notes Fixes Design

Addresses 6 issues from `notes.txt`: search UX, abstract backfill, extraction validation, researcher filtering, duplicate paper merging, and GitHub exclusion.

## 1. Search Smoothness (#0)

**Problem:** Search triggers SWR skeleton flash and input focus loss.

**Changes:**
- `app/src/lib/api.ts` — add `keepPreviousData: true` to `usePublications` SWR options
- `app/src/app/NewsfeedContent.tsx` — keep `isLoading` for the initial skeleton state (first load with no cached data). Add a separate `isValidating && !isLoading` condition for a subtle "refetching" indicator (opacity fade or small spinner) that keeps previous results visible during subsequent fetches.

## 2. Abstract Backfill From Multiple Sources (#1)

**Problem:** When a duplicate paper is found on another researcher's page with new metadata (abstract, year, venue), the data is discarded because INSERT IGNORE skips entirely.

**Changes:**
- `publication.py` in `save_publications()` — after INSERT IGNORE for a duplicate (fetched existing paper by title_hash), compare new extraction fields against existing paper's NULL fields. Run UPDATE with COALESCE logic to backfill `abstract`, `year`, `venue` where currently NULL.

**Flow:**
1. LLM extracts paper from researcher B's page (with abstract)
2. INSERT IGNORE fails (title_hash exists)
3. Fetch existing paper, check for NULL fields
4. If new extraction has data and existing field is NULL, UPDATE to backfill
5. Same for year, venue

**Note:** Status progression (e.g., working_paper -> published) is intentionally out of scope here — that is handled by the existing status-change detection logic in the scraping pipeline.

## 3. Post-Extraction Validation (#2 + GitHub Packages)

**Problem:** LLM produces garbage extractions (title words as author names) and extracts GitHub repos as publications.

**Changes:**
- `publication.py` — add `validate_publication(pub) -> bool` function, called after LLM extraction and before `save_publications()`. Papers that fail are silently dropped.

**Validation checks:**
1. **Author sanity:** Skip if 2 or more author last names appear as whole words in the title (case-insensitive), excluding common short words (articles, prepositions like "a", "the", "of", "on", "in")
2. **Minimum author quality:** Skip if all authors have last names shorter than 2 characters
3. **GitHub exclusion:** Skip if `draft_url` contains `github.com` (not `github.io`). Also skip if title contains software/package indicators ("python package", "repository", "library") combined with a `draft_url` pointing to github.com or no venue/year.

**Examples caught:**
- "A Theory of Disappointment Aversion" with authors ["A. Theory", "o. Disappoinment", "A."] — title-word overlap + single-char names
- "Econ-Newsfeed" with draft_url github.com/... — GitHub exclusion
- "Automatic Causal Inference Python package" — software indicator in title

## 4. Filter Unvalidated Researchers (#3)

**Problem:** Noise entries like "BPH" appear in the researcher directory.

**Changes:**
- `api.py` on `/api/researchers` endpoint — add a base condition to the `conditions` list (used by both the COUNT query and the data query) requiring researchers to have at least one of:
  - `openalex_author_id IS NOT NULL`, OR
  - Exists in `researcher_urls` (has a monitored personal website)

This ensures both count and data queries are consistent. This is a display filter only — unvalidated researchers remain in the database for potential future enrichment.

## 5. Duplicate Paper Merging (#4)

**Problem:** Same paper with different titles (renamed between SSRN versions) appears twice. Dedup is exact title hash only.

**Changes:**
- New merge function (in `publication.py` or dedicated module), called after enrichment in `scheduler.py`.

**Merge logic:**
1. Query for paper groups sharing the same non-NULL `doi` OR `openalex_id`
2. For each group, pick canonical paper (earliest `discovered_at`, or most metadata)
3. Merge duplicates into canonical within a single database transaction:
   - Reassign rows using INSERT IGNORE + DELETE pattern (not bare UPDATE) to handle UNIQUE constraint conflicts on: `authorship`, `paper_urls`, `paper_links`, `feed_events`, `paper_snapshots`, `openalex_coauthors`, `paper_topics`
   - Backfill NULL fields on canonical from duplicates (abstract, venue, year)
   - Delete duplicate paper rows (remaining child rows cascade-delete)
4. Log every merge for auditability

**Constraint handling:** Tables like `authorship` (UNIQUE on researcher_id + publication_id) and `paper_links` (UNIQUE on paper_id + url) may have overlapping rows between canonical and duplicate. The INSERT IGNORE approach attempts to move rows to the canonical paper_id; conflicts (already exists) are silently skipped, then the duplicate's remaining rows are cleaned up by CASCADE on paper deletion.

**Limitations:** This merge is a conservative first step — it only matches on exact DOI or OpenAlex ID. The motivating example (two papers with substantially different titles) will only be caught if both are successfully enriched to the same identifier. If enrichment fails for one (e.g., title search doesn't match), manual intervention or a future fuzzy-matching step would be needed.

**Safety:** Exact identifier matches only (deterministic). No fuzzy matching. Each merge group executes in a single transaction (rollback on error). At least one integration test should cover the merge path including UNIQUE constraint conflicts.

## Files Changed

| File | Changes |
|------|---------|
| `app/src/lib/api.ts` | Add `keepPreviousData: true` to SWR hook |
| `app/src/app/NewsfeedContent.tsx` | Keep `isLoading` for initial load, add `isValidating && !isLoading` indicator |
| `publication.py` | Add `validate_publication()`, abstract backfill on duplicate |
| `api.py` | Filter researchers by validation status (base condition on both queries) |
| `scheduler.py` | Call merge step after enrichment |
| New: merge module or function | Post-enrichment duplicate detection and merging |
