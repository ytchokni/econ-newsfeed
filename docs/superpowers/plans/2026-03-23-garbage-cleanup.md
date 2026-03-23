# Garbage Paper Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove existing garbage entries from the papers table and strengthen extraction validation to prevent future garbage.

**Architecture:** Two-part fix: (1) add new validation rules to `validate_publication()` that catch website snippets, LLM "no publications" hallucinations, and GitHub-venue papers, (2) run a one-time DB cleanup script to delete existing garbage matching these patterns. Validation runs first so the rules can be tested before the cleanup uses the same logic.

**Tech Stack:** Python, MySQL

---

### Task 1: Strengthen validate_publication with new rules

**Files:**
- Modify: `publication.py:67-118` (add constants and rules to `validate_publication`)
- Modify: `tests/test_validate_publication.py` (add new test cases)

- [ ] **Step 1: Write failing tests for new validation rules**

Add to `tests/test_validate_publication.py`:

```python
class TestWebsiteSnippetRejection:
    """Reject titles that are clearly website elements, not paper titles."""

    def test_rejects_very_short_title(self):
        pub = {"title": "CV", "authors": [["John", "Doe"]]}
        assert validate_publication(pub) is False

    def test_rejects_website_element_titles(self):
        for title in ["Email", "Follow", "Sitemap", "Feed", "Teaching", "Publications"]:
            pub = {"title": title, "authors": [["John", "Doe"]]}
            assert validate_publication(pub) is False, f"Should reject '{title}'"

    def test_rejects_no_publications_hallucination(self):
        pub = {"title": "No publications found in the provided page content", "authors": []}
        assert validate_publication(pub) is False

    def test_rejects_copyright_notice(self):
        pub = {"title": "© 2025 Jason Chen, Powered by Jekyll", "authors": [["Jason", "Chen"]]}
        assert validate_publication(pub) is False

    def test_rejects_bio_snippet(self):
        pub = {"title": "I will be on the job market in the 2025-26 academic year.", "authors": []}
        assert validate_publication(pub) is False

    def test_rejects_welcome_message(self):
        pub = {"title": "Welcome to my academic webpage.", "authors": []}
        assert validate_publication(pub) is False

    def test_rejects_github_venue(self):
        pub = {"title": "My Cool Project", "authors": [["J", "Doe"]], "venue": "GitHub"}
        assert validate_publication(pub) is False

    def test_accepts_short_but_real_title(self):
        """Real papers can have short titles like 'Voting' or 'Big G'."""
        pub = {"title": "Voting", "authors": [["John", "Smith"]], "status": "published", "venue": "AER"}
        assert validate_publication(pub) is True

    def test_accepts_normal_paper(self):
        pub = {"title": "The Effect of Trade on Growth", "authors": [["J", "Smith"]]}
        assert validate_publication(pub) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_validate_publication.py::TestWebsiteSnippetRejection -v`

Expected: FAIL — new rules don't exist yet.

- [ ] **Step 3: Add new validation rules to validate_publication**

In `publication.py`, add new constants after `_SOFTWARE_INDICATORS` (around line 78):

```python
# Website elements that LLMs sometimes extract as paper titles
_WEBSITE_NOISE = frozenset({
    'cv', 'feed', 'email', 'follow', 'sitemap', 'teaching', 'publications',
    'papers', 'research', 'home', 'contact', 'about', 'links', 'news',
    'jmp', 'bio', 'vita',
})

# Patterns that indicate an LLM hallucination or website snippet, not a paper
_GARBAGE_PATTERNS = (
    'no publications',
    'powered by',
    'welcome to my',
    'i will be on the job market',
    'i am a ',
    'i am an ',
    'my research interests',
    'site last updated',
    'academic webpage',
    'currently, i am',
)
```

Then add these checks at the top of `validate_publication`, right after the `draft_url` line and before the GitHub check:

```python
    # Reject empty or very short titles (< 5 chars and no venue/status)
    title_lower = title.lower().strip()
    if len(title_lower) < 5 and not pub.get('venue') and not pub.get('status'):
        return False

    # Reject titles that are website elements
    if title_lower.rstrip('.') in _WEBSITE_NOISE:
        return False

    # Reject LLM hallucinations and website snippets
    if any(pattern in title_lower for pattern in _GARBAGE_PATTERNS):
        return False

    # Reject copyright notices
    if title_lower.startswith('©') or title_lower.startswith('(c)'):
        return False

    # Reject GitHub as venue
    venue = (pub.get('venue') or '').lower()
    if 'github' in venue:
        return False
```

Note: move the existing `title_lower = title.lower()` line up to where the new code is, and remove the duplicate later. The existing `_SOFTWARE_INDICATORS` check already uses `title_lower` so make sure it still works.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_validate_publication.py -v`

Expected: All PASS.

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest`

Expected: All pass (pre-existing failures excluded).

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_validate_publication.py
git commit -m "feat: strengthen extraction validation to reject website snippets and LLM hallucinations"
```

---

### Task 2: Clean existing garbage from the database

**Files:**
- Create: `scripts/cleanup_garbage_papers.py`

- [ ] **Step 1: Create cleanup script**

Create `scripts/cleanup_garbage_papers.py`:

```python
"""One-time cleanup: delete garbage entries from the papers table.

Identifies papers that would fail the current validate_publication() rules
or match known garbage patterns. Uses CASCADE deletion so authorship,
feed_events, paper_links, etc. are automatically cleaned up.

Usage: poetry run python scripts/cleanup_garbage_papers.py [--dry-run]
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def find_garbage_papers() -> list[dict]:
    """Find papers matching garbage patterns."""
    return Database.fetch_all("""
        SELECT p.id, p.title, p.source_url, p.venue
        FROM papers p
        WHERE
            -- Very short titles with no status/venue (website elements)
            (LENGTH(p.title) < 5 AND p.status IS NULL AND p.venue IS NULL)
            -- Known website noise words
            OR LOWER(TRIM(TRAILING '.' FROM p.title)) IN (
                'cv', 'feed', 'email', 'follow', 'sitemap', 'teaching',
                'publications', 'papers', 'research', 'home', 'contact',
                'about', 'links', 'news', 'jmp', 'bio', 'vita'
            )
            -- LLM hallucinations
            OR LOWER(p.title) LIKE '%no publications%'
            -- Website snippets
            OR LOWER(p.title) LIKE 'welcome to my%'
            OR LOWER(p.title) LIKE 'i will be on the job market%'
            OR LOWER(p.title) LIKE 'i am a %'
            OR LOWER(p.title) LIKE 'i am an %'
            OR LOWER(p.title) LIKE 'my research interests%'
            OR LOWER(p.title) LIKE 'site last updated%'
            OR LOWER(p.title) LIKE 'currently, i am%'
            OR LOWER(p.title) LIKE '%academic webpage%'
            OR LOWER(p.title) LIKE '%powered by%'
            -- Copyright notices
            OR p.title LIKE '©%'
            -- GitHub venue
            OR LOWER(p.venue) LIKE '%github%'
        ORDER BY LENGTH(p.title)
    """)


def main():
    dry_run = "--dry-run" in sys.argv

    garbage = find_garbage_papers()
    logger.info("Found %d garbage papers", len(garbage))

    for g in garbage:
        logger.info("  [%d] %s", g["id"], g["title"][:70])

    if dry_run:
        logger.info("\nDry run — no deletions made. Remove --dry-run to delete.")
        return

    if not garbage:
        return

    ids = [g["id"] for g in garbage]
    placeholders = ",".join(["%s"] * len(ids))
    deleted = Database.execute_query(
        f"DELETE FROM papers WHERE id IN ({placeholders})",
        tuple(ids),
    )
    logger.info("\nDeleted %d garbage papers (CASCADE cleaned child rows)", len(ids))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry run to verify what will be deleted**

Run: `poetry run python scripts/cleanup_garbage_papers.py --dry-run`

Expected: Lists ~30-35 garbage entries. Review the list to ensure no real papers are caught.

- [ ] **Step 3: Run the cleanup**

Run: `poetry run python scripts/cleanup_garbage_papers.py`

Expected: Deletes the garbage entries.

- [ ] **Step 4: Verify**

Run: `poetry run python scripts/cleanup_garbage_papers.py --dry-run`

Expected: "Found 0 garbage papers"

- [ ] **Step 5: Commit**

```bash
git add scripts/cleanup_garbage_papers.py
git commit -m "chore: add and run garbage paper cleanup script"
```
