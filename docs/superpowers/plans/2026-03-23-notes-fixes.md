# Notes Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 issues from notes.txt — search UX, extraction validation, abstract backfill, researcher filtering, and duplicate paper merging.

**Architecture:** Five independent changes across frontend (SWR config + rendering) and backend (validation function in publication.py, backfill logic in save_publications, filter clause in api.py, merge function called from scheduler.py). Each task is self-contained and can be committed independently.

**Tech Stack:** Next.js (SWR), Python (FastAPI), MySQL

**Spec:** `docs/superpowers/specs/2026-03-23-notes-fixes-design.md`

---

### Task 1: Search Smoothness — SWR keepPreviousData

**Files:**
- Modify: `app/src/lib/api.ts:63-66`
- Modify: `app/src/app/NewsfeedContent.tsx:311,339-346,352`

- [ ] **Step 1: Add keepPreviousData to usePublications hook**

In `app/src/lib/api.ts`, change the `usePublications` hook to pass SWR options:

```typescript
export function usePublications(page = 1, perPage = 20, filters?: FeedFilters) {
  const url = buildPublicationsUrl(page, perPage, filters);
  return useSWR<PaginatedResponse<Publication>>(url, fetchJson, {
    keepPreviousData: true,
  });
}
```

- [ ] **Step 2: Update NewsfeedContent to use isValidating instead of isLoading for content gate**

In `app/src/app/NewsfeedContent.tsx`, destructure `isValidating` from the hook and update the rendering logic:

```typescript
// Line 311: add isValidating
const { data, error, isLoading, isValidating } = usePublications(page, 20, mergedFilters);
```

Replace the loading/content rendering block (lines 339-403). The key changes:

1. Keep `isLoading` skeleton for initial load only (no data at all yet)
2. When data exists and `isValidating`, show results with subtle opacity fade
3. Remove the `!isLoading &&` guard on the empty state check

```tsx
{isLoading && !data && (
  <div className="space-y-4">
    <p className="font-sans text-sm text-[var(--text-muted)]">Loading publications...</p>
    {Array.from({ length: 3 }).map((_, i) => (
      <PublicationCardSkeleton key={i} />
    ))}
  </div>
)}

{error && !data && (
  <ErrorMessage message="Failed to load publications." />
)}

{!isLoading && data && data.items.length === 0 && (
  <EmptyState
    message={
      activeTab === "new_paper"
        ? "No new publications yet. Papers will appear here as researchers update their pages."
        : "No status changes yet. Updates will appear here when papers change status."
    }
  />
)}

{data && data.items.length > 0 && (
  <div className={isValidating && !isLoading ? "opacity-60 transition-opacity duration-200" : "transition-opacity duration-200"}>
    {/* ... existing results rendering (groupByDate, pagination) unchanged ... */}
  </div>
)}
```

- [ ] **Step 3: Verify manually**

Run: `make dev`

Test: type in search box — results should stay visible with a subtle fade instead of skeleton flash. Focus should not be lost.

- [ ] **Step 4: Run existing tests**

Run: `cd app && npx jest`

Expected: All existing tests pass (no breaking changes).

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/api.ts app/src/app/NewsfeedContent.tsx
git commit -m "fix: smooth search by keeping previous SWR data during refetch"
```

---

### Task 2: Post-Extraction Validation

**Files:**
- Modify: `publication.py` (add `validate_publication` function, wire into `extract_publications`)
- Create: `tests/test_validate_publication.py`

- [ ] **Step 1: Write failing tests for validate_publication**

Create `tests/test_validate_publication.py`:

```python
"""Tests for validate_publication — catches garbage LLM extractions."""
import pytest
from publication import validate_publication


class TestAuthorTitleOverlap:
    """Skip papers where 2+ author last names appear as words in the title."""

    def test_rejects_title_words_as_author_names(self):
        """'A Theory of Disappointment Aversion' with authors Theory, Disappoinment, A."""
        pub = {
            "title": "A Theory of Disappointment Aversion",
            "authors": [["A.", "Theory"], ["o.", "Disappoinment"], ["A.", ""]],
        }
        assert validate_publication(pub) is False

    def test_accepts_legitimate_paper(self):
        pub = {
            "title": "Trade and Wages in a Global Economy",
            "authors": [["John", "Smith"], ["Jane", "Doe"]],
        }
        assert validate_publication(pub) is True

    def test_ignores_common_short_words(self):
        """Words like 'a', 'the', 'of', 'on', 'in' should not count as overlap."""
        pub = {
            "title": "A Study of Trade in Europe",
            "authors": [["Anna", "Tradeworth"], ["Oliver", "Europe"]],
        }
        # 'Europe' matches title word, but only 1 overlap — not >= 2
        assert validate_publication(pub) is True

    def test_rejects_when_two_last_names_match_title(self):
        pub = {
            "title": "Growth and Trade in Africa",
            "authors": [["A.", "Growth"], ["B.", "Trade"]],
        }
        assert validate_publication(pub) is False


class TestMinimumAuthorQuality:
    """Skip if all authors have last names shorter than 2 characters."""

    def test_rejects_all_single_char_last_names(self):
        pub = {
            "title": "Some Paper Title",
            "authors": [["X", "A"], ["Y", "B"]],
        }
        assert validate_publication(pub) is False

    def test_accepts_if_at_least_one_valid_last_name(self):
        pub = {
            "title": "Some Paper Title",
            "authors": [["X", "A"], ["John", "Smith"]],
        }
        assert validate_publication(pub) is True


class TestGitHubExclusion:
    """Skip papers with github.com draft URLs."""

    def test_rejects_github_com_draft_url(self):
        pub = {
            "title": "Econ-Newsfeed",
            "authors": [["Y.", "Tchokni"]],
            "draft_url": "https://github.com/user/repo",
        }
        assert validate_publication(pub) is False

    def test_allows_github_io_draft_url(self):
        pub = {
            "title": "My Research Paper",
            "authors": [["Y.", "Tchokni"]],
            "draft_url": "https://user.github.io/paper.pdf",
        }
        assert validate_publication(pub) is True

    def test_allows_no_draft_url(self):
        pub = {
            "title": "My Research Paper",
            "authors": [["Y.", "Tchokni"]],
        }
        assert validate_publication(pub) is True

    def test_rejects_software_title_indicators(self):
        pub = {
            "title": "Automatic Causal Inference Python package",
            "authors": [["Y.", "Tchokni"]],
        }
        assert validate_publication(pub) is False

    def test_allows_legitimate_paper_with_common_words(self):
        """Papers about software/libraries as economic topics should pass."""
        pub = {
            "title": "Labor Market Dynamics in Library Services",
            "authors": [["John", "Smith"]],
        }
        assert validate_publication(pub) is True


class TestEdgeCases:
    """Edge cases that should not crash."""

    def test_empty_authors_list(self):
        pub = {"title": "Some Title", "authors": []}
        assert validate_publication(pub) is True

    def test_missing_draft_url_key(self):
        pub = {"title": "Some Title", "authors": [["John", "Doe"]]}
        assert validate_publication(pub) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_validate_publication.py -v`

Expected: ImportError — `validate_publication` does not exist yet.

- [ ] **Step 3: Implement validate_publication**

Add to `publication.py` after the `PublicationExtractionList` class (after line 65):

```python
# Words too common to count as author-title overlap
_STOPWORDS = frozenset({
    'a', 'an', 'the', 'of', 'on', 'in', 'and', 'or', 'for', 'to', 'at',
    'by', 'is', 'it', 'as', 'do', 'no', 'not', 'with', 'from', 'but',
})

# Multi-word title phrases indicating software, not academic papers.
# Only specific multi-word phrases to avoid false positives on legitimate papers
# (e.g., "Labor Market Dynamics in Library Services" should NOT be rejected).
_SOFTWARE_INDICATORS = (
    'python package', 'r package', 'npm package', 'pip install',
    'github repository', 'code repository', 'open source software',
)


def validate_publication(pub: dict) -> bool:
    """Return False for garbage extractions that should be silently dropped."""
    title = pub.get('title', '')
    authors = pub.get('authors', [])
    draft_url = pub.get('draft_url') or ''

    # GitHub exclusion: reject draft URLs pointing to github.com (not github.io)
    if 'github.com' in draft_url.lower() and 'github.io' not in draft_url.lower():
        return False

    # Software/package title indicators
    title_lower = title.lower()
    if any(indicator in title_lower for indicator in _SOFTWARE_INDICATORS):
        return False

    # Collect last names (stripped, lowered)
    last_names = []
    for author in authors:
        if not author:
            continue
        last = author[-1].strip().lower() if author else ''
        if last:
            last_names.append(last)

    # Minimum author quality: reject if ALL last names are < 2 chars
    if last_names and all(len(ln) < 2 for ln in last_names):
        return False

    # Author-title overlap: reject if 2+ non-stopword last names appear in the title
    title_words = set(re.sub(r'[^a-z\s]', '', title_lower).split())
    overlap_count = sum(
        1 for ln in last_names
        if ln in title_words and ln not in _STOPWORDS and len(ln) >= 2
    )
    if overlap_count >= 2:
        return False

    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_validate_publication.py -v`

Expected: All PASS.

- [ ] **Step 5: Wire validate_publication into extract_publications**

In `publication.py`, modify `extract_publications` (around line 269) to filter results before returning:

```python
            # Before: return [pub.model_dump() for pub in result.publications]
            validated = []
            for pub in result.publications:
                d = pub.model_dump()
                if validate_publication(d):
                    validated.append(d)
                else:
                    logging.info(f"Validation dropped: {d.get('title', '<no title>')}")
            return validated
```

- [ ] **Step 6: Run full test suite**

Run: `poetry run pytest`

Expected: All pass (existing tests unaffected — they mock the OpenAI call).

- [ ] **Step 7: Commit**

```bash
git add publication.py tests/test_validate_publication.py
git commit -m "feat: add post-extraction validation to drop garbage LLM outputs and GitHub repos"
```

---

### Task 3: Abstract Backfill on Duplicate Discovery

**Files:**
- Modify: `publication.py:125-159` (the `else` branch in `save_publications`)
- Modify: `tests/test_save_publications.py` (add backfill tests)

- [ ] **Step 1: Write failing test for abstract backfill**

Add to `tests/test_save_publications.py`:

```python
class TestAbstractBackfill:
    """When a duplicate paper is found, backfill NULL abstract/year/venue from new extraction."""

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_backfills_abstract_when_existing_is_null(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """Duplicate path should UPDATE abstract when existing paper has NULL abstract."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        # Simulate INSERT IGNORE → duplicate (lastrowid=0)
        cursor.lastrowid = 0
        # fetchone for title_hash lookup → returns paper id=10
        # fetchone for existing paper fields → abstract is NULL
        cursor.fetchone.side_effect = [
            (10,),          # SELECT id FROM papers WHERE title_hash = ...
            (None, None, None),  # SELECT abstract, year, venue FROM papers WHERE id = ...
            (0,),           # SELECT COUNT(*) FROM feed_events (existing logic)
        ]
        cursor.rowcount = 1  # new_to_this_url = True for paper_urls INSERT

        Publication.save_publications("http://new-source.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "year": "2024",
            "venue": "AER",
            "abstract": "This paper studies trade.",
            "status": "working_paper",
        }])

        # Verify UPDATE was called to backfill
        update_calls = [
            call for call in cursor.execute.call_args_list
            if 'UPDATE papers SET' in str(call) and 'COALESCE' in str(call)
        ]
        assert len(update_calls) == 1, f"Expected 1 backfill UPDATE, got {len(update_calls)}"

    @patch("publication.Database.get_researcher_id", return_value=42)
    @patch("publication.Database.get_connection")
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    def test_skips_backfill_when_existing_has_all_fields(
        self, mock_hash, mock_get_conn, mock_get_researcher
    ):
        """No UPDATE if existing paper already has abstract, year, venue."""
        conn, cursor = _mock_conn()
        mock_get_conn.return_value = conn
        cursor.lastrowid = 0
        cursor.fetchone.side_effect = [
            (10,),                           # title_hash lookup
            ("Existing abstract", "2023", "QJE"),  # all fields populated
            (0,),                            # feed_events COUNT
        ]
        cursor.rowcount = 1

        Publication.save_publications("http://new-source.com", [{
            "title": "Test Paper",
            "authors": [["John", "Doe"]],
            "abstract": "New abstract",
            "year": "2024",
            "venue": "AER",
        }])

        update_calls = [
            call for call in cursor.execute.call_args_list
            if 'UPDATE papers SET' in str(call) and 'COALESCE' in str(call)
        ]
        assert len(update_calls) == 0, "Should not backfill when all fields exist"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_save_publications.py::TestAbstractBackfill -v`

Expected: FAIL — the backfill UPDATE doesn't exist yet.

- [ ] **Step 3: Implement backfill in save_publications**

In `publication.py`, in the `save_publications` method, after the duplicate paper ID is fetched (around line 135), and before the paper_urls INSERT (line 137), add:

```python
                        publication_id = row[0]

                        # Backfill NULL fields from new extraction
                        cursor.execute(
                            "SELECT abstract, year, venue FROM papers WHERE id = %s",
                            (publication_id,),
                        )
                        existing = cursor.fetchone()
                        if existing:
                            existing_abstract, existing_year, existing_venue = existing
                            new_abstract = pub.get('abstract')
                            new_year = pub.get('year')
                            new_venue = pub.get('venue')
                            needs_backfill = (
                                (not existing_abstract and new_abstract)
                                or (not existing_year and new_year)
                                or (not existing_venue and new_venue)
                            )
                            if needs_backfill:
                                cursor.execute(
                                    """UPDATE papers SET
                                        abstract = COALESCE(abstract, %s),
                                        year = COALESCE(year, %s),
                                        venue = COALESCE(venue, %s)
                                    WHERE id = %s""",
                                    (new_abstract, new_year, new_venue, publication_id),
                                )
                                logging.info(f"Backfilled metadata for duplicate: {pub['title']}")

                        # Add the new source URL to paper_urls ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_save_publications.py -v`

Expected: All PASS.

- [ ] **Step 5: Run full Python test suite**

Run: `poetry run pytest`

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_save_publications.py
git commit -m "feat: backfill abstract/year/venue when duplicate paper found from new source"
```

---

### Task 4: Filter Unvalidated Researchers

**Files:**
- Modify: `api.py:846-847` (add base condition to conditions list)
- Modify: `tests/test_api_researchers.py` (add test for the filter)

- [ ] **Step 1: Write failing test**

First, read the existing test file to understand the pattern:

Run: `cat tests/test_api_researchers.py` to understand existing tests.

Add a test to `tests/test_api_researchers.py`:

```python
class TestResearcherValidationFilter:
    """Only researchers with openalex_author_id or researcher_urls should appear."""

    def test_researchers_endpoint_filters_unvalidated(self, client):
        """The base query must include a validation filter condition."""
        with patch("api.Database.fetch_one") as mock_count, \
             patch("api.Database.fetch_all") as mock_data, \
             patch("api.Database.get_jel_codes_for_researchers", return_value={}):
            mock_count.return_value = {"total": 0}
            mock_data.return_value = []
            resp = client.get("/api/researchers")
            assert resp.status_code == 200
            # Verify the SQL includes the validation filter
            count_sql = mock_count.call_args[0][0]
            assert "openalex_author_id" in count_sql or "researcher_urls" in count_sql, \
                "Researchers query must filter by validation status"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_api_researchers.py::TestResearcherValidationFilter -v`

Expected: FAIL — the query doesn't include the filter yet.

- [ ] **Step 3: Add validation filter to list_researchers**

In `api.py`, in the `list_researchers` function, add a base condition right after `conditions = []` and `params: list = []` (lines 846-847):

```python
    conditions = []
    params: list = []

    # Only show validated researchers (have OpenAlex ID or a monitored website)
    conditions.append(
        "(r.openalex_author_id IS NOT NULL OR EXISTS "
        "(SELECT 1 FROM researcher_urls ru WHERE ru.researcher_id = r.id))"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_api_researchers.py -v`

Expected: All PASS.

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest`

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add api.py tests/test_api_researchers.py
git commit -m "fix: filter researcher directory to only show validated entries"
```

---

### Task 5: Post-Enrichment Duplicate Paper Merging

**Files:**
- Create: `paper_merge.py` (standalone merge module)
- Create: `tests/test_paper_merge.py`
- Modify: `scheduler.py:280-282` (call merge after enrichment)

- [ ] **Step 1: Write failing tests for merge logic**

Create `tests/test_paper_merge.py`:

```python
"""Tests for post-enrichment duplicate paper merging."""
import pytest
from unittest.mock import patch, MagicMock, call
from paper_merge import find_duplicate_groups, merge_paper_group


class TestFindDuplicateGroups:
    """find_duplicate_groups returns groups of paper IDs sharing doi or openalex_id."""

    @patch("paper_merge.Database.fetch_all")
    def test_finds_papers_sharing_doi(self, mock_fetch):
        mock_fetch.side_effect = [
            # DOI duplicates query
            [{"doi": "10.1234/test", "ids": "1,2"}],
            # OpenAlex duplicates query
            [],
        ]
        groups = find_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2}

    @patch("paper_merge.Database.fetch_all")
    def test_finds_papers_sharing_openalex_id(self, mock_fetch):
        mock_fetch.side_effect = [
            [],
            [{"openalex_id": "W123", "ids": "3,4"}],
        ]
        groups = find_duplicate_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {3, 4}

    @patch("paper_merge.Database.fetch_all")
    def test_returns_empty_when_no_duplicates(self, mock_fetch):
        mock_fetch.side_effect = [[], []]
        groups = find_duplicate_groups()
        assert groups == []

    @patch("paper_merge.Database.fetch_all")
    def test_deduplicates_across_doi_and_openalex(self, mock_fetch):
        """If papers 1,2 share a DOI and papers 1,3 share an openalex_id, merge all."""
        mock_fetch.side_effect = [
            [{"doi": "10.1234/test", "ids": "1,2"}],
            [{"openalex_id": "W123", "ids": "1,3"}],
        ]
        groups = find_duplicate_groups()
        # Should be merged into a single group {1,2,3}
        assert len(groups) == 1
        assert set(groups[0]) == {1, 2, 3}


class TestMergePaperGroup:
    """merge_paper_group reassigns child rows and deletes duplicates."""

    @patch("paper_merge.Database.get_connection")
    @patch("paper_merge.Database.fetch_all")
    def test_picks_earliest_as_canonical(self, mock_fetch_all, mock_get_conn):
        """Canonical paper is the one with earliest discovered_at."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        mock_fetch_all.return_value = [
            {"id": 2, "discovered_at": "2026-03-20", "abstract": None, "year": None, "venue": None},
            {"id": 5, "discovered_at": "2026-03-23", "abstract": "An abstract", "year": "2024", "venue": "AER"},
        ]

        merge_paper_group([2, 5])

        # Verify exactly one DELETE targeting paper 5 (not canonical paper 2)
        delete_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "DELETE FROM papers" in str(c)
        ]
        assert len(delete_calls) == 1
        assert delete_calls[0] == call("DELETE FROM papers WHERE id = %s", (5,))

    @patch("paper_merge.Database.get_connection")
    @patch("paper_merge.Database.fetch_all")
    def test_backfills_null_fields_from_duplicate(self, mock_fetch_all, mock_get_conn):
        """Canonical paper gets NULL fields filled from duplicate before deletion."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        mock_fetch_all.return_value = [
            {"id": 2, "discovered_at": "2026-03-20", "abstract": None, "year": None, "venue": None},
            {"id": 5, "discovered_at": "2026-03-23", "abstract": "An abstract", "year": "2024", "venue": "AER"},
        ]

        merge_paper_group([2, 5])

        # Verify a COALESCE UPDATE was issued for backfill
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "COALESCE" in str(c)
        ]
        assert len(update_calls) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_paper_merge.py -v`

Expected: ImportError — `paper_merge` module doesn't exist yet.

- [ ] **Step 3: Implement paper_merge.py**

Create `paper_merge.py`:

```python
"""Post-enrichment duplicate paper merging.

After OpenAlex enrichment assigns DOIs and OpenAlex IDs, this module finds papers
sharing the same identifier and merges them into a single canonical record.
"""
import logging
from database import Database

logger = logging.getLogger(__name__)

# Tables with paper_id FK that need row reassignment before deleting duplicates.
_CHILD_TABLES = [
    ("authorship", "publication_id"),
    ("paper_urls", "paper_id"),
    ("paper_links", "paper_id"),
    ("feed_events", "paper_id"),
    ("paper_snapshots", "paper_id"),
    ("openalex_coauthors", "paper_id"),
    ("paper_topics", "paper_id"),
]


def find_duplicate_groups() -> list[list[int]]:
    """Find groups of papers sharing the same DOI or OpenAlex ID."""
    doi_groups = Database.fetch_all(
        """SELECT doi, GROUP_CONCAT(id ORDER BY discovered_at) AS ids
           FROM papers WHERE doi IS NOT NULL
           GROUP BY doi HAVING COUNT(*) > 1"""
    )
    oa_groups = Database.fetch_all(
        """SELECT openalex_id, GROUP_CONCAT(id ORDER BY discovered_at) AS ids
           FROM papers WHERE openalex_id IS NOT NULL
           GROUP BY openalex_id HAVING COUNT(*) > 1"""
    )

    raw_groups: list[set[int]] = []
    for row in doi_groups:
        raw_groups.append({int(x) for x in row['ids'].split(',')})
    for row in oa_groups:
        raw_groups.append({int(x) for x in row['ids'].split(',')})

    # Merge overlapping groups (papers sharing DOI AND openalex_id)
    merged: list[set[int]] = []
    for group in raw_groups:
        found = None
        for i, existing in enumerate(merged):
            if group & existing:
                found = i
                break
        if found is not None:
            merged[found] |= group
        else:
            merged.append(group)

    return [sorted(g) for g in merged]


def merge_paper_group(paper_ids: list[int]) -> None:
    """Merge duplicate papers into the earliest-discovered canonical record."""
    papers = Database.fetch_all(
        f"""SELECT id, discovered_at, abstract, year, venue
            FROM papers WHERE id IN ({','.join(['%s'] * len(paper_ids))})
            ORDER BY discovered_at""",
        tuple(paper_ids),
    )
    if len(papers) < 2:
        return

    canonical_id = papers[0]['id']
    duplicates = papers[1:]
    dup_ids = [p['id'] for p in duplicates]

    logger.info("Merging papers %s into canonical %s", dup_ids, canonical_id)

    with Database.get_connection() as conn:
        cursor = conn.cursor()
        try:
            for dup in duplicates:
                cursor.execute(
                    """UPDATE papers SET
                        abstract = COALESCE(abstract, %s),
                        year = COALESCE(year, %s),
                        venue = COALESCE(venue, %s)
                    WHERE id = %s""",
                    (dup['abstract'], dup['year'], dup['venue'], canonical_id),
                )

            # UPDATE IGNORE skips rows that would violate UNIQUE constraints
            # (already exist for canonical_id). CASCADE deletion cleans up the rest.
            for dup_id in dup_ids:
                for table, col in _CHILD_TABLES:
                    cursor.execute(
                        f"UPDATE IGNORE `{table}` SET `{col}` = %s WHERE `{col}` = %s",
                        (canonical_id, dup_id),
                    )

            for dup_id in dup_ids:
                cursor.execute("DELETE FROM papers WHERE id = %s", (dup_id,))

            conn.commit()
            logger.info("Merged %d duplicates into paper %s", len(dup_ids), canonical_id)
        except Exception:
            conn.rollback()
            logger.exception("Failed to merge papers %s", paper_ids)
            raise
        finally:
            cursor.close()


def merge_duplicate_papers() -> int:
    """Find and merge all duplicate paper groups. Returns count of merges."""
    groups = find_duplicate_groups()
    if not groups:
        logger.info("No duplicate papers found")
        return 0

    logger.info("Found %d duplicate paper groups to merge", len(groups))
    merged = 0
    for group in groups:
        try:
            merge_paper_group(group)
            merged += 1
        except Exception:
            logger.exception("Skipping failed merge for group %s", group)
    logger.info("Completed %d/%d merges", merged, len(groups))
    return merged
```

Wait — I made an error above with duplicate function definitions and the `_get_other_columns` dead code. Let me write the clean version:

```python
"""Post-enrichment duplicate paper merging.

After OpenAlex enrichment assigns DOIs and OpenAlex IDs, this module finds papers
sharing the same identifier and merges them into a single canonical record.
"""
import logging
from database import Database

logger = logging.getLogger(__name__)

# Tables with paper_id FK that need row reassignment before deleting duplicates.
_CHILD_TABLES = [
    ("authorship", "publication_id"),
    ("paper_urls", "paper_id"),
    ("paper_links", "paper_id"),
    ("feed_events", "paper_id"),
    ("paper_snapshots", "paper_id"),
    ("openalex_coauthors", "paper_id"),
    ("paper_topics", "paper_id"),
]


def find_duplicate_groups() -> list[list[int]]:
    """Find groups of papers sharing the same DOI or OpenAlex ID."""
    doi_groups = Database.fetch_all(
        """SELECT doi, GROUP_CONCAT(id ORDER BY discovered_at) AS ids
           FROM papers WHERE doi IS NOT NULL
           GROUP BY doi HAVING COUNT(*) > 1"""
    )
    oa_groups = Database.fetch_all(
        """SELECT openalex_id, GROUP_CONCAT(id ORDER BY discovered_at) AS ids
           FROM papers WHERE openalex_id IS NOT NULL
           GROUP BY openalex_id HAVING COUNT(*) > 1"""
    )

    raw_groups: list[set[int]] = []
    for row in doi_groups:
        raw_groups.append({int(x) for x in row['ids'].split(',')})
    for row in oa_groups:
        raw_groups.append({int(x) for x in row['ids'].split(',')})

    # Merge overlapping groups
    merged: list[set[int]] = []
    for group in raw_groups:
        found = None
        for i, existing in enumerate(merged):
            if group & existing:
                found = i
                break
        if found is not None:
            merged[found] |= group
        else:
            merged.append(group)

    return [sorted(g) for g in merged]


def merge_paper_group(paper_ids: list[int]) -> None:
    """Merge duplicate papers into the earliest-discovered canonical record."""
    papers = Database.fetch_all(
        f"""SELECT id, discovered_at, abstract, year, venue
            FROM papers WHERE id IN ({','.join(['%s'] * len(paper_ids))})
            ORDER BY discovered_at""",
        tuple(paper_ids),
    )
    if len(papers) < 2:
        return

    canonical_id = papers[0]['id']
    duplicates = papers[1:]
    dup_ids = [p['id'] for p in duplicates]

    logger.info("Merging papers %s into canonical %s", dup_ids, canonical_id)

    with Database.get_connection() as conn:
        cursor = conn.cursor()
        try:
            for dup in duplicates:
                cursor.execute(
                    """UPDATE papers SET
                        abstract = COALESCE(abstract, %s),
                        year = COALESCE(year, %s),
                        venue = COALESCE(venue, %s)
                    WHERE id = %s""",
                    (dup['abstract'], dup['year'], dup['venue'], canonical_id),
                )

            for dup_id in dup_ids:
                for table, col in _CHILD_TABLES:
                    cursor.execute(
                        f"UPDATE IGNORE `{table}` SET `{col}` = %s WHERE `{col}` = %s",
                        (canonical_id, dup_id),
                    )

            for dup_id in dup_ids:
                cursor.execute("DELETE FROM papers WHERE id = %s", (dup_id,))

            conn.commit()
            logger.info("Merged %d duplicates into paper %s", len(dup_ids), canonical_id)
        except Exception:
            conn.rollback()
            logger.exception("Failed to merge papers %s", paper_ids)
            raise
        finally:
            cursor.close()


def merge_duplicate_papers() -> int:
    """Find and merge all duplicate paper groups. Returns count of merges."""
    groups = find_duplicate_groups()
    if not groups:
        logger.info("No duplicate papers found")
        return 0

    logger.info("Found %d duplicate paper groups to merge", len(groups))
    merged = 0
    for group in groups:
        try:
            merge_paper_group(group)
            merged += 1
        except Exception:
            logger.exception("Skipping failed merge for group %s", group)
    logger.info("Completed %d/%d merges", merged, len(groups))
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_paper_merge.py -v`

Expected: All PASS.

- [ ] **Step 5: Wire merge into scheduler**

In `scheduler.py`, after the OpenAlex enrichment call (line 280-282), add the merge step:

```python
    # Enrich after releasing lock
    t0 = time.time()
    _enrich_with_openalex()
    enrich_s = time.time() - t0
    logger.info(f"OpenAlex enrichment: {enrich_s:.1f}s")

    # Merge duplicate papers identified by shared DOI/OpenAlex ID
    t0 = time.time()
    try:
        from paper_merge import merge_duplicate_papers
        merge_duplicate_papers()
    except Exception as e:
        logger.error("Paper merge failed: %s: %s", type(e).__name__, e)
    merge_s = time.time() - t0
    logger.info(f"Paper merge: {merge_s:.1f}s")
```

- [ ] **Step 6: Run full test suite**

Run: `poetry run pytest`

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add paper_merge.py tests/test_paper_merge.py scheduler.py
git commit -m "feat: add post-enrichment duplicate paper merging by DOI/OpenAlex ID"
```

---

### Task 6: Final Verification

- [ ] **Step 1: Run full Python test suite**

Run: `poetry run pytest -v`

Expected: All pass.

- [ ] **Step 2: Run frontend tests**

Run: `cd app && npx jest`

Expected: All pass.

- [ ] **Step 3: Run TypeScript check**

Run: `cd app && npx tsc --noEmit`

Expected: No errors.
