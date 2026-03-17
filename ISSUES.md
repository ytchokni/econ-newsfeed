# Issues

Tracking known issues and planned improvements for the econ-newsfeed scraping pipeline.

---

## Issue 1: Unbounded storage growth in `html_content`

**Status:** Open
**Severity:** Medium
**File:** `html_content` table / `html_fetcher.py:55-69`

Every time a page changes, `save_text()` inserts a new full-text row into `html_content`. Over time this table grows without bound — one row per URL per change — even though only the latest snapshot is ever used.

**Proposed fix:** Replace the append-only insert with an upsert (one row per `url_id`). Add a `UNIQUE` constraint on `url_id` and use `INSERT ... ON DUPLICATE KEY UPDATE` to keep only the most recent content.

---

## Issue 2: Binary change detection — no diffing

**Status:** Open
**Severity:** High
**File:** `html_fetcher.py:72-90`

The SHA-256 hash comparison tells us *if* a page changed, but not *what* changed. When a change is detected, the entire page text is sent to OpenAI for extraction — even if only one new paper was added among dozens of existing ones. This wastes tokens and increases API cost.

**Proposed fix:** When a change is detected, compute a text diff (`difflib.unified_diff`) between the old and new content. Send only the new/changed lines to the LLM for extraction. Keep the SHA-256 hash as a cheap first gate to skip unchanged pages entirely.

---

## Issue 3: No publication deduplication on re-extraction

**Status:** Open
**Severity:** High
**File:** `publication.py:20-63`

`save_publications()` inserts every extracted publication unconditionally. If a page changes (e.g. one new paper added), the LLM re-extracts all publications on the page, and all of them are inserted as new rows — creating duplicates of every previously known publication.

**Proposed fix:** Before inserting, check if a publication with the same title (and source URL) already exists. Skip duplicates. For robustness, consider normalizing titles (lowercase, strip whitespace) before comparison.

---

## Issue 4: `fetch_and_save_if_changed` doesn't return change status

**Status:** Open
**Severity:** Low
**File:** `html_fetcher.py:93-105`

`fetch_and_save_if_changed()` returns `None` in all cases. The scheduler integration in `DESIGN.md` (section 6.2) expects it to return a boolean indicating whether the content changed. This will cause the scheduler to never trigger publication extraction.

**Proposed fix:** Return `True` when content changed and was saved, `False` when unchanged, and `False`/`None` on fetch failure.

---

## Issue 5: 4000-character content truncation may miss publications

**Status:** Open
**Severity:** Medium
**File:** `publication.py:87`

The LLM prompt truncates page content to 4000 characters (`text_content[:4000]`). For researchers with long publication lists, later entries will be silently dropped. This is an intentional cost control but has no visibility into how much content is lost.

**Proposed fix:** Log when truncation occurs and how many characters were dropped. Consider a smarter truncation strategy — e.g. sending the content in chunks, or prioritizing the most recently added content (from the diff in Issue 2).

---

## Issue 6: No connection pooling in database access

**Status:** Open
**Severity:** Low (MVP-acceptable)
**File:** `database.py:34-43`

`get_connection()` creates a new MySQL connection on every query. At MVP scale this is fine, but will cause connection exhaustion under load or during large scrape runs with many URLs.

**Proposed fix (post-MVP):** Use `mysql.connector.pooling.MySQLConnectionPool` or switch to SQLAlchemy with a connection pool.

---

## Issue 7: OpenAI client instantiated per extraction call

**Status:** Open
**Severity:** Low
**File:** `publication.py:90`

A new `OpenAI()` client is created on every call to `extract_publications()`. This is wasteful and prevents connection reuse.

**Proposed fix:** Instantiate the client once at module level or as a class attribute.
