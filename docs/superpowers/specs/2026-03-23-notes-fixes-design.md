# Notes Fixes Design

Addresses 6 issues from `notes.txt`: search UX, abstract backfill, extraction validation, researcher filtering, duplicate paper merging, and GitHub exclusion.

## 1. Search Smoothness (#0)

**Problem:** Search triggers SWR skeleton flash and input focus loss.

**Changes:**
- `app/src/lib/api.ts` — add `keepPreviousData: true` to `usePublications` SWR options
- `app/src/app/NewsfeedContent.tsx` — replace `isLoading` content gate with `isValidating`-based subtle loading indicator (opacity fade or small spinner); keep previous results visible during fetch

## 2. Abstract Backfill From Multiple Sources (#1)

**Problem:** When a duplicate paper is found on another researcher's page with new metadata (abstract, year, venue), the data is discarded because INSERT IGNORE skips entirely.

**Changes:**
- `publication.py` in `save_publications()` — after INSERT IGNORE for a duplicate (fetched existing paper by title_hash), compare new extraction fields against existing paper's NULL fields. Run UPDATE with COALESCE logic to backfill `abstract`, `year`, `venue`, `status` where currently NULL.

**Flow:**
1. LLM extracts paper from researcher B's page (with abstract)
2. INSERT IGNORE fails (title_hash exists)
3. Fetch existing paper, check for NULL fields
4. If new extraction has data and existing field is NULL, UPDATE to backfill
5. Same for year, venue, status

## 3. Post-Extraction Validation (#2 + GitHub Packages)

**Problem:** LLM produces garbage extractions (title words as author names) and extracts GitHub repos as publications.

**Changes:**
- `publication.py` — add `validate_publication(pub) -> bool` function, called after LLM extraction and before `save_publications()`. Papers that fail are silently dropped.

**Validation checks:**
1. **Author sanity:** Skip if any author's last name is a single character (not an initial pattern like "J." — literally "A" or "o") or if author names overlap significantly with title words
2. **GitHub exclusion:** Skip if `draft_url` contains `github.com` (not `github.io`)
3. **Minimum author quality:** Skip if all authors have last names shorter than 2 characters

**Examples caught:**
- "A Theory of Disappointment Aversion" with authors ["A. Theory", "o. Disappoinment", "A."] — title-word overlap + single-char names
- "Econ-Newsfeed" with draft_url github.com/... — GitHub exclusion

## 4. Filter Unvalidated Researchers (#3)

**Problem:** Noise entries like "BPH" appear in the researcher directory.

**Changes:**
- `api.py` on `/api/researchers` endpoint — add WHERE clause requiring researchers to have at least one of:
  - `openalex_author_id IS NOT NULL`, OR
  - Exists in `researcher_urls` (has a monitored personal website)

This is a display filter only. Unvalidated researchers remain in the database for potential future enrichment.

## 5. Duplicate Paper Merging (#4)

**Problem:** Same paper with different titles (renamed between SSRN versions) appears twice. Dedup is exact title hash only.

**Changes:**
- New merge function (in `publication.py` or dedicated module), called after enrichment in `scheduler.py`.

**Merge logic:**
1. Query for paper groups sharing the same non-NULL `doi` OR `openalex_id`
2. For each group, pick canonical paper (earliest `discovered_at`, or most metadata)
3. Merge duplicates into canonical:
   - Reassign `authorship` rows
   - Reassign `paper_urls` rows
   - Reassign `paper_links` rows
   - Reassign `feed_events` rows
   - Backfill NULL fields on canonical from duplicates (abstract, venue, year)
   - Delete duplicate paper rows
4. Log every merge for auditability

**Safety:** Only exact identifier matches (deterministic). No fuzzy matching.

## Files Changed

| File | Changes |
|------|---------|
| `app/src/lib/api.ts` | Add `keepPreviousData: true` to SWR hook |
| `app/src/app/NewsfeedContent.tsx` | Replace skeleton flash with subtle loading indicator |
| `publication.py` | Add `validate_publication()`, abstract backfill on duplicate |
| `api.py` | Filter researchers by validation status |
| `scheduler.py` | Call merge step after enrichment |
| New: merge module or function | Post-enrichment duplicate detection and merging |
