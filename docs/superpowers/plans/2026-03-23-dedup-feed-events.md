# Dedup Branch Feed Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create feed events when an existing paper appears on a new URL during a non-seed extraction, so genuinely new papers are detected even when they already exist in the DB from another source.

**Architecture:** In the dedup branch of `save_publications`, after `INSERT IGNORE INTO paper_urls`, check `cursor.rowcount`. If >0 (paper is newly associated with this URL), and `is_seed=False`, and no `new_paper` feed event already exists for this paper, create one. This leverages the existing `paper_urls` unique key `(paper_id, url)` as the detection mechanism — `rowcount=1` means the paper was NOT previously found on this URL.

**Tech Stack:** Python, MySQL, pytest (mocked DB)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `publication.py` | Modify (lines 130-139) | Add feed event creation in dedup branch |
| `tests/test_publication_extraction.py` | Modify (append) | Tests for dedup-branch feed events |

---

### Task 1: Create feed events in the dedup branch for papers new to a URL

**Files:**
- Modify: `publication.py:130-139` (dedup branch of `save_publications`)
- Test: `tests/test_publication_extraction.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publication_extraction.py`, inside `class TestSavePublications`:

```python
    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_dedup_new_url_creates_feed_event(self, mock_get_conn, mock_hash, mock_get_rid):
        """Dedup paper appearing on a NEW url (non-seed) should create a feed event."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=0)
        # fetchone: (42,) for paper lookup, (0,) for COUNT(*) showing no event exists
        cursor.fetchone.side_effect = [(42,), (0,)]
        # Track execute calls to set rowcount dynamically
        original_execute = cursor.execute
        def _tracking_execute(*args, **kwargs):
            original_execute(*args, **kwargs)
            sql = args[0] if args else ""
            if "INSERT IGNORE INTO paper_urls" in sql:
                cursor.rowcount = 1  # new URL association
            elif "SELECT" in sql:
                cursor.rowcount = 1  # SELECT always returns rows
        cursor.execute = MagicMock(side_effect=_tracking_execute)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status="working_paper")
        Publication.save_publications("https://newsite.com", [pub], is_seed=False)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 1
        params = feed_calls[0][0][1]
        assert params[0] == 42  # paper_id
        assert params[1] == "working_paper"  # new_status

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_dedup_same_url_no_feed_event(self, mock_get_conn, mock_hash, mock_get_rid):
        """Dedup paper on the SAME url (already known) should NOT create a feed event."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=0)
        cursor.fetchone.return_value = (42,)
        # Track execute calls to set rowcount=0 for paper_urls (duplicate)
        original_execute = cursor.execute
        def _tracking_execute(*args, **kwargs):
            original_execute(*args, **kwargs)
            sql = args[0] if args else ""
            if "INSERT IGNORE INTO paper_urls" in sql:
                cursor.rowcount = 0  # already known URL
        cursor.execute = MagicMock(side_effect=_tracking_execute)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status="working_paper")
        Publication.save_publications("https://example.com", [pub], is_seed=False)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 0

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_dedup_new_url_seed_no_feed_event(self, mock_get_conn, mock_hash, mock_get_rid):
        """Dedup paper on new URL but is_seed=True should NOT create feed event."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=0)
        cursor.fetchone.return_value = (42,)
        cursor.rowcount = 1  # new URL, but seed → should still skip
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status="working_paper")
        Publication.save_publications("https://newsite.com", [pub], is_seed=True)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 0

    @patch("publication.Database.get_researcher_id", return_value=99)
    @patch("publication.Database.compute_title_hash", return_value="abc123")
    @patch("publication.Database.get_connection")
    def test_dedup_new_url_existing_event_no_duplicate(self, mock_get_conn, mock_hash, mock_get_rid):
        """Dedup paper on new URL that already has a feed event should NOT create duplicate."""
        cursor, conn, ctx_factory = self._setup_cursor(lastrowid=0)
        # fetchone: (42,) for paper lookup, (1,) for COUNT(*) showing event exists
        cursor.fetchone.side_effect = [(42,), (1,)]
        # Track execute to set rowcount=1 for paper_urls INSERT
        original_execute = cursor.execute
        def _tracking_execute(*args, **kwargs):
            original_execute(*args, **kwargs)
            sql = args[0] if args else ""
            if "INSERT IGNORE INTO paper_urls" in sql:
                cursor.rowcount = 1
        cursor.execute = MagicMock(side_effect=_tracking_execute)
        mock_get_conn.side_effect = lambda: ctx_factory()

        pub = _make_pub_dict(status="working_paper")
        Publication.save_publications("https://newsite.com", [pub], is_seed=False)

        feed_calls = [
            c for c in cursor.execute.call_args_list
            if "INSERT INTO feed_events" in c[0][0]
        ]
        assert len(feed_calls) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_publication_extraction.py::TestSavePublications::test_dedup_new_url_creates_feed_event -v`
Expected: FAIL — no feed_events INSERT in the dedup branch

- [ ] **Step 3: Write the implementation**

In `publication.py`, replace lines 130-139 (the dedup branch after fetching existing id):

From:
```python
                        publication_id = row[0]
                        # Add the new source URL to paper_urls for cross-researcher tracking
                        cursor.execute(
                            """
                            INSERT IGNORE INTO paper_urls (paper_id, url, discovered_at)
                            VALUES (%s, %s, %s)
                            """,
                            (publication_id, url, datetime.now(timezone.utc)),
                        )
                        logging.info(f"Duplicate publication (title_hash match), added source URL: {pub['title']}")
```

To:
```python
                        publication_id = row[0]
                        # Add the new source URL to paper_urls for cross-researcher tracking
                        cursor.execute(
                            """
                            INSERT IGNORE INTO paper_urls (paper_id, url, discovered_at)
                            VALUES (%s, %s, %s)
                            """,
                            (publication_id, url, datetime.now(timezone.utc)),
                        )
                        new_to_this_url = cursor.rowcount > 0
                        # Create feed event if paper is new to this URL, non-seed, and has no event yet
                        pub_status = pub.get('status')
                        if not is_seed and new_to_this_url and pub_status and pub_status != 'published':
                            cursor.execute(
                                "SELECT COUNT(*) FROM feed_events WHERE paper_id = %s AND event_type = 'new_paper'",
                                (publication_id,),
                            )
                            if cursor.fetchone()[0] == 0:
                                cursor.execute(
                                    """
                                    INSERT INTO feed_events (paper_id, event_type, new_status, created_at)
                                    VALUES (%s, 'new_paper', %s, %s)
                                    """,
                                    (publication_id, pub_status, datetime.now(timezone.utc)),
                                )
                        logging.info(f"Duplicate publication (title_hash match), added source URL: {pub['title']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_publication_extraction.py::TestSavePublications -v`
Expected: All tests pass (existing + 4 new)

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add publication.py tests/test_publication_extraction.py
git commit -m "feat: create feed events for papers appearing on new URLs in dedup branch"
```

---

### Task 2: Verify with real data

This task validates the fix against the 3 known papers (Tenreyro x2, Bai x1) that should generate feed events when their researchers' pages are re-extracted.

- [ ] **Step 1: Check URLs pending extraction for affected researchers**

Run: `poetry run python -c "from database import Database; ..."`

Verify that Tenreyro's `/LGW.html` and Bai's `/research` URLs have `needs_extraction=True`.

- [ ] **Step 2: Run the extraction pipeline**

Run: `make parse`

This re-extracts URLs with changed content. For Tenreyro and Bai:
- Their pages will be extracted (non-seed, since `extracted_at` is set)
- Papers already in DB will hit the dedup branch
- `INSERT IGNORE INTO paper_urls` with the new URL will succeed (`rowcount=1`)
- Feed events will be created

- [ ] **Step 3: Verify feed events were created**

Run:
```python
poetry run python -c "
from database import Database
rows = Database.fetch_all('''
    SELECT p.title, p.status, fe.created_at
    FROM feed_events fe
    JOIN papers p ON p.id = fe.paper_id
    ORDER BY fe.created_at DESC
    LIMIT 10
''')
for r in rows:
    print(f'{r[\"title\"][:60]} | {r[\"status\"]}')
"
```

Expected: The 3 papers should appear as feed events (among any other genuinely new papers found).
