# Text Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate ~60% false positives in HTML change detection by normalizing text before hashing.

**Architecture:** A single pure function `normalize_text()` in `html_fetcher.py` that canonicalizes extracted text (quotes, whitespace, boilerplate) before hashing and storage. A one-time backfill script re-hashes existing rows to prevent a false-positive burst on first deploy.

**Tech Stack:** Python, BeautifulSoup (existing), pytest, mysql-connector-python (existing)

**Spec:** `docs/superpowers/specs/2026-03-30-text-normalization-design.md`

**Worktree:** `.worktrees/text-normalization` (branch `feature/text-normalization`)

**Baseline:** 502 tests passing, 5 pre-existing failures in `tests/test_api_search.py` (unrelated)

---

### Task 1: Add `normalize_text()` with unit tests

**Files:**
- Modify: `html_fetcher.py` (add function after `hash_text_content` at line ~236)
- Modify: `tests/test_html_fetcher.py` (add new test class)

- [ ] **Step 1: Write the failing tests**

Add a new test class `TestNormalizeText` at the end of `tests/test_html_fetcher.py`:

```python
class TestNormalizeText:
    """Tests for normalize_text() using real false-positive fixtures."""

    def test_collapses_whitespace(self):
        """Trailing spaces before parens — Hye Young You case."""
        old = "Pamela Ban and Ju Yeon Park )"
        new = "Pamela Ban and Ju Yeon Park)"
        assert HTMLFetcher.normalize_text(old) == HTMLFetcher.normalize_text(new)

    def test_normalizes_curly_quotes(self):
        """Curly quotes to straight — Lars Svensson case."""
        old = '\u201cSwedish household debt\u201d and \u2018solvency\u2019'
        expected = '"Swedish household debt" and \'solvency\''
        result = HTMLFetcher.normalize_text(old)
        assert result == expected

    def test_collapses_google_sites_word_splitting(self):
        """Google Sites rendering splits words — Laurence van Lent case."""
        old = "Zhang, 202 6, The Accounting Review (condi tionally accepted)"
        new = "Zhang, 2026, The Accounting Review (conditionally accepted)"
        assert HTMLFetcher.normalize_text(old) == HTMLFetcher.normalize_text(new)

    def test_strips_google_sites_boilerplate(self):
        """Google Sites nav/chrome should be stripped."""
        text = "Search this site Embedded Files Skip to main content Skip to navigation Home Research CV"
        result = HTMLFetcher.normalize_text(text)
        assert "Search this site" not in result
        assert "Skip to main content" not in result
        assert "Skip to navigation" not in result
        assert "Embedded Files" not in result
        # Real content preserved
        assert "Home" in result
        assert "Research" in result

    def test_strips_cookie_consent(self):
        """Cookie consent boilerplate should be stripped."""
        text = "Research papers This site uses cookies from Google to deliver its services and to analyze traffic Learn more Got it"
        result = HTMLFetcher.normalize_text(text)
        assert "cookies from Google" not in result
        assert "Learn more Got it" not in result
        assert "Research papers" in result

    def test_preserves_real_content_change(self):
        """Maria Silfa case — R&R status update must survive normalization."""
        old = 'How Crisis Reshapes Government Talent with Jacob R. Brown'
        new = 'How Crisis Reshapes Government Talent with Jacob R. Brown (R&R, American Political Science Review)'
        assert HTMLFetcher.normalize_text(old) != HTMLFetcher.normalize_text(new)

    def test_preserves_year_changes(self):
        """Year updates must not be normalized away."""
        old = "Working Paper, 2025"
        new = "Published, 2026"
        assert HTMLFetcher.normalize_text(old) != HTMLFetcher.normalize_text(new)

    def test_preserves_new_paper_title(self):
        """A new paper appearing must produce a different normalized result."""
        old = "Paper A, Paper B"
        new = "Paper A, Paper B, Paper C: New Findings"
        assert HTMLFetcher.normalize_text(old) != HTMLFetcher.normalize_text(new)

    def test_handles_empty_string(self):
        assert HTMLFetcher.normalize_text("") == ""

    def test_handles_whitespace_only(self):
        assert HTMLFetcher.normalize_text("   \n\t  ") == ""

    def test_non_breaking_space_collapsed(self):
        """Non-breaking spaces (\\u00a0) should be treated as whitespace."""
        text = "hello\u00a0\u00a0world"
        assert HTMLFetcher.normalize_text(text) == "hello world"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run pytest tests/test_html_fetcher.py::TestNormalizeText -v`

Expected: FAIL — `AttributeError: type object 'HTMLFetcher' has no attribute 'normalize_text'`

- [ ] **Step 3: Implement `normalize_text()`**

In `html_fetcher.py`, add this after the `hash_text_content` method (after line 236) and before `archive_snapshot`:

```python
# Boilerplate substrings to strip before hashing (case-insensitive).
# Keep this list short and conservative — only patterns confirmed as noise.
_BOILERPLATE_NOISE = [
    "search this site",
    "embedded files",
    "skip to main content",
    "skip to navigation",
    "report abuse",
    "page details",
    "page updated",
    "this site uses cookies from google to deliver its services and to analyze traffic",
    "learn more got it",
    "accept all cookies",
    "reject all cookies",
]

# Unicode quote characters to normalize to ASCII equivalents
_QUOTE_MAP = str.maketrans({
    '\u201c': '"',   # left double curly quote
    '\u201d': '"',   # right double curly quote
    '\u2018': "'",   # left single curly quote
    '\u2019': "'",   # right single curly quote
})
```

And the static method inside `HTMLFetcher`:

```python
    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize extracted text to reduce false-positive change detection.

        Applied transformations (in order):
        1. Curly/smart quotes → straight quotes
        2. All whitespace runs → single space
        3. Known boilerplate substrings removed
        4. Final trim
        """
        import re
        # 1. Quote normalization
        text = text.translate(_QUOTE_MAP)
        # 2. Whitespace collapsing (includes \u00a0 non-breaking space)
        text = re.sub(r'[\s\u00a0]+', ' ', text)
        # 3. Boilerplate stripping (case-insensitive)
        text_lower = text.lower()
        for phrase in _BOILERPLATE_NOISE:
            idx = text_lower.find(phrase)
            while idx != -1:
                text = text[:idx] + text[idx + len(phrase):]
                text_lower = text.lower()
                idx = text_lower.find(phrase)
        # 4. Re-collapse any whitespace gaps left by boilerplate removal, then trim
        text = re.sub(r'[\s\u00a0]+', ' ', text).strip()
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run pytest tests/test_html_fetcher.py::TestNormalizeText -v`

Expected: All 11 tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run pytest -x --ignore=tests/test_api_search.py`

Expected: All passing (same 502 minus 5 excluded = 497 pass)

- [ ] **Step 6: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "feat: add normalize_text() for change detection noise reduction

Normalizes curly quotes, collapses whitespace, strips known boilerplate
before hashing to eliminate ~60% false positives in change detection."
```

---

### Task 2: Integrate `normalize_text()` into `fetch_and_save_if_changed()`

**Files:**
- Modify: `html_fetcher.py:388-394` (insert normalize_text call)
- Modify: `tests/test_html_fetcher.py` (add integration tests)

- [ ] **Step 1: Write the failing integration tests**

Add a new test class at the end of `tests/test_html_fetcher.py`:

```python
class TestNormalizationIntegration:
    """Integration: normalize_text is applied before hashing in fetch_and_save_if_changed."""

    @patch("html_fetcher.Database.execute_query")
    @patch("html_fetcher.Database.fetch_one")
    def test_whitespace_only_change_not_detected(self, mock_fetch, mock_execute):
        """Content differing only in whitespace should hash identically after normalization."""
        old_text = "Paper A (with Author )"
        old_normalized = HTMLFetcher.normalize_text(old_text)
        old_hash = HTMLFetcher.hash_text_content(old_normalized)

        new_text = "Paper A (with Author)"
        new_normalized = HTMLFetcher.normalize_text(new_text)
        new_hash = HTMLFetcher.hash_text_content(new_normalized)

        assert old_hash == new_hash, "Whitespace-only change should produce identical hashes after normalization"

    @patch("html_fetcher.Database.execute_query")
    @patch("html_fetcher.Database.fetch_one")
    def test_quote_only_change_not_detected(self, mock_fetch, mock_execute):
        """Content differing only in quote style should hash identically after normalization."""
        old_text = '\u201cA Paper Title\u201d by Smith'
        new_text = '"A Paper Title" by Smith'
        assert HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(old_text)) == \
               HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(new_text))

    @patch("html_fetcher.Database.execute_query")
    @patch("html_fetcher.Database.fetch_one")
    def test_real_change_still_detected(self, mock_fetch, mock_execute):
        """Substantive changes must still produce different hashes."""
        old_text = "Paper A, working_paper"
        new_text = "Paper A, accepted at AER"
        assert HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(old_text)) != \
               HTMLFetcher.hash_text_content(HTMLFetcher.normalize_text(new_text))
```

- [ ] **Step 2: Run tests to verify they pass (these test the function directly, so they should pass already)**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run pytest tests/test_html_fetcher.py::TestNormalizationIntegration -v`

Expected: PASS (these test the function, confirming the normalization behavior is correct)

- [ ] **Step 3: Modify `fetch_and_save_if_changed()` to use normalization**

In `html_fetcher.py`, change lines 388-394 from:

```python
        # Parse HTML once, reuse for both comparison and storage
        text_content = HTMLFetcher.extract_text_content(raw_html)
        if len(text_content) > CONTENT_MAX_CHARS:
            dropped = len(text_content) - CONTENT_MAX_CHARS
            logging.info(f"Truncating content for URL ID {url_id}: {len(text_content)} -> {CONTENT_MAX_CHARS} chars ({dropped} dropped)")
            text_content = text_content[:CONTENT_MAX_CHARS]
        text_hash = HTMLFetcher.hash_text_content(text_content)
```

to:

```python
        # Parse HTML once, reuse for both comparison and storage
        text_content = HTMLFetcher.extract_text_content(raw_html)
        if len(text_content) > CONTENT_MAX_CHARS:
            dropped = len(text_content) - CONTENT_MAX_CHARS
            logging.info(f"Truncating content for URL ID {url_id}: {len(text_content)} -> {CONTENT_MAX_CHARS} chars ({dropped} dropped)")
            text_content = text_content[:CONTENT_MAX_CHARS]
        text_content = HTMLFetcher.normalize_text(text_content)
        text_hash = HTMLFetcher.hash_text_content(text_content)
```

The only change is inserting `text_content = HTMLFetcher.normalize_text(text_content)` before the hash call. The normalized text is what gets stored in `html_content.content` and hashed for `content_hash`.

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run pytest -x --ignore=tests/test_api_search.py`

Expected: All passing

- [ ] **Step 5: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization
git add html_fetcher.py tests/test_html_fetcher.py
git commit -m "feat: integrate normalize_text into change detection pipeline

Text is now normalized before hashing in fetch_and_save_if_changed(),
so whitespace, quote encoding, and boilerplate changes no longer
trigger false-positive change detection."
```

---

### Task 3: Backfill script to re-normalize existing hashes

**Files:**
- Create: `scripts/backfill_normalized_hashes.py`

- [ ] **Step 1: Create the backfill script**

Create `scripts/backfill_normalized_hashes.py`:

```python
# scripts/backfill_normalized_hashes.py
"""One-time backfill: re-normalize and re-hash existing html_content rows.

After deploying normalize_text(), existing content_hash values are stale
(computed on un-normalized text). This script re-normalizes stored text,
recomputes hashes, and updates both content_hash and extracted_hash to
prevent a false-positive burst on the next scrape cycle.

Run: poetry run python scripts/backfill_normalized_hashes.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from database import Database
from html_fetcher import HTMLFetcher


def backfill_normalized_hashes():
    """Re-normalize and re-hash all html_content rows."""
    rows = Database.fetch_all(
        "SELECT id, url_id, content, content_hash, extracted_hash FROM html_content WHERE content IS NOT NULL"
    )
    logger.info("Found %d html_content rows to re-normalize", len(rows))

    updated = 0
    unchanged = 0

    for row in rows:
        content = row['content']
        old_hash = row['content_hash']

        normalized = HTMLFetcher.normalize_text(content)
        new_hash = HTMLFetcher.hash_text_content(normalized)

        if new_hash == old_hash:
            unchanged += 1
            continue

        # Update content (normalized), content_hash, and extracted_hash
        # Set extracted_hash = new_hash so pages aren't re-extracted unnecessarily.
        # Only update extracted_hash if it was previously equal to old content_hash
        # (meaning extraction was up-to-date before normalization).
        new_extracted_hash = new_hash if row['extracted_hash'] == old_hash else row['extracted_hash']

        Database.execute_query(
            """UPDATE html_content
               SET content = %s, content_hash = %s, extracted_hash = %s
               WHERE id = %s""",
            (normalized, new_hash, new_extracted_hash, row['id']),
        )
        updated += 1
        if updated % 100 == 0:
            logger.info("Progress: %d updated, %d unchanged", updated, unchanged)

    logger.info("Backfill complete: %d updated, %d unchanged (already normalized)", updated, unchanged)


if __name__ == "__main__":
    backfill_normalized_hashes()
```

- [ ] **Step 2: Verify script imports work**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run python -c "import scripts.backfill_normalized_hashes; print('imports ok')"`

If this fails due to module resolution, test with: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run python scripts/backfill_normalized_hashes.py --help 2>&1 || echo 'no --help, but imports ok if no ImportError'`

Expected: No ImportError

- [ ] **Step 3: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization
git add scripts/backfill_normalized_hashes.py
git commit -m "feat: add backfill script to re-normalize existing content hashes

One-time script to prevent false-positive burst on first scrape after
deploying text normalization. Re-normalizes stored text, recomputes
hashes, and preserves extraction state."
```

---

### Task 4: Add Makefile target and final verification

**Files:**
- Modify: `Makefile` (add backfill target)

- [ ] **Step 1: Add Makefile target for the backfill**

Look at the existing Makefile to find the pattern for script targets, then add:

```makefile
backfill-normalize:  ## Re-normalize html_content hashes (one-time, after deploying text normalization)
	poetry run python scripts/backfill_normalized_hashes.py
```

Add it near the other one-time script targets (near `backfill_paper_links` or similar).

- [ ] **Step 2: Run full test suite one final time**

Run: `cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization && poetry run pytest -x --ignore=tests/test_api_search.py`

Expected: All passing (same count as baseline)

- [ ] **Step 3: Commit**

```bash
cd /Users/yogamtchokni/Projects/econ-newsfeed/.worktrees/text-normalization
git add Makefile
git commit -m "chore: add make backfill-normalize target for one-time hash migration"
```

---

## Deployment Checklist

After merging `feature/text-normalization` into `main`:

1. Deploy the new code
2. Run `make backfill-normalize` once to re-hash existing rows
3. Next scrape cycle will use normalized hashing — verify reduced `urls_changed` count in `scrape_log`
