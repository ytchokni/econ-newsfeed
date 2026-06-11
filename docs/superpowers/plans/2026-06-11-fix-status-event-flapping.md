# Fix Status-Change Event Flapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop spurious forward `status_change` feed events caused by co-author pages disagreeing on a paper's status, and clean up the spurious events already in prod.

**Architecture:** `append_paper_snapshot()` currently compares a new extraction's status against the **latest raw snapshot** (which deliberately stores raw LLM output). Change the comparison baseline to `papers.status`, which PR #153 already made monotone non-decreasing. Result: a status_change event fires only when a paper's effective rank actually increases — at most 4 events per paper, ever. A schema migration deletes historical non-advancing events.

**Tech Stack:** Python 3.12, mysql-connector (raw SQL, no ORM), pytest with mocked connections (no real DB in tests).

**Spec:** `docs/superpowers/specs/2026-06-11-status-event-flapping-design.md`

**Working directory:** `/Users/yogamtchokni/Documents/Projects/econ-newsfeed/.claude/worktrees/fix-status-event-flapping` (worktree, branch `worktree-fix-status-event-flapping`). All commands run here. Use `poetry run pytest ...`.

---

### Task 1: Effective-status baseline in `append_paper_snapshot()`

**Files:**
- Modify: `database/snapshots.py:104-147` (`append_paper_snapshot`)
- Test: `tests/test_snapshots_integration.py` (append new test class)

**Context for the implementer:** `append_paper_snapshot()` does one `fetchone()` today (previous snapshot's `content_hash` + `status`). After this change it does **two** `fetchone()` calls: (1) previous snapshot's `content_hash` for the skip-if-unchanged check, (2) `papers.status` with `FOR UPDATE` as the event baseline. The existing mock helper `_make_mock_conn(prev_row)` sets `fetchone.return_value`, so both calls return the same dict — existing tests keep passing because their `prev_row` dicts contain both `content_hash` and `status` keys. The new tests need a helper with `side_effect` to return *different* rows for the two reads, because the whole point is the case where the latest snapshot and `papers.status` disagree.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_snapshots_integration.py` (after class `TestStatusProgression`, before the "Researcher snapshot tests" divider):

```python
def _make_two_read_mock_conn(snapshot_row, papers_row):
    """Mock conn whose cursor returns snapshot_row, then papers_row, from fetchone().

    snapshot_row : dict | None — previous paper_snapshots row (content_hash check)
    papers_row : dict | None — papers row read with FOR UPDATE (event baseline)
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [snapshot_row, papers_row]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestEffectiveStatusBaseline:
    """old_status must come from papers.status (monotone), not the latest raw snapshot.

    Flapping scenario this prevents: paper is 'published' on page A but
    'working_paper' on page B. Snapshots alternate between the two raw values,
    so a snapshot-based baseline re-emits working_paper→published every cycle.
    papers.status never regresses, so an effective-status baseline emits nothing.
    """

    def test_flapping_does_not_reemit_forward_event(self):
        """Latest snapshot regressed to working_paper, papers.status is published:
        re-seeing published is NOT a status change."""
        old_hash = _compute_paper_content_hash("working_paper", "AER", "abs", None, "2024")
        mock_conn, _ = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": "published"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "published", "AER", "abs", None, "2024")

        assert result.changed is True
        assert result.status_changed is False
        assert result.old_status == "published"

    def test_progression_past_effective_status_emits(self):
        """A rank increase beyond papers.status still reports status_changed."""
        old_hash = _compute_paper_content_hash("accepted", "AER", "abs", None, "2024")
        mock_conn, _ = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": "accepted"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "published", "AER", "abs", None, "2024")

        assert result.status_changed is True
        assert result.old_status == "accepted"
        assert result.new_status == "published"

    def test_regression_below_effective_status_keeps_papers_status(self):
        """A raw status below papers.status: no event, papers keeps its status."""
        old_hash = _compute_paper_content_hash("published", "AER", "abs", None, "2024")
        mock_conn, mock_cursor = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": "published"},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "working_paper", "AER", "new abs", None, "2024")

        assert result.status_changed is False
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE papers" in str(c)
        ]
        assert len(update_calls) == 1
        assert update_calls[0][0][1][0] == "published"

    def test_null_effective_status_takes_new_without_event(self):
        """papers.status NULL: adopt the new status, but emit nothing."""
        old_hash = _compute_paper_content_hash(None, "AER", "abs", None, "2024")
        mock_conn, mock_cursor = _make_two_read_mock_conn(
            {"content_hash": old_hash},
            {"status": None},
        )
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            result = append_paper_snapshot(1, "working_paper", "AER", "abs", None, "2024")

        assert result.status_changed is False
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE papers" in str(c)
        ]
        assert update_calls[0][0][1][0] == "working_paper"

    def test_papers_row_read_with_for_update(self):
        """The papers.status read must lock the row (FOR UPDATE) so concurrent
        extractions of co-author pages cannot both emit for the same rank."""
        mock_conn, mock_cursor = _make_two_read_mock_conn(None, {"status": "accepted"})
        with patch("database.snapshots.get_connection", return_value=mock_conn):
            append_paper_snapshot(1, "published", "AER", "abs", None, "2024")

        sqls = _sql_statements(mock_cursor)
        assert any("FROM papers" in s and "FOR UPDATE" in s for s in sqls)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `poetry run pytest tests/test_snapshots_integration.py::TestEffectiveStatusBaseline -v`
Expected: FAIL — `test_flapping_does_not_reemit_forward_event` asserts `status_changed is False` but current code reports True (old comes from snapshot `working_paper`... note: with the side_effect mock the current code only calls fetchone once, so `papers_row` is never consumed and `old_status` comes from `snapshot_row`, which has no `status` key → KeyError is also an acceptable failure mode). `test_papers_row_read_with_for_update` fails because no `FOR UPDATE` SQL exists.

- [ ] **Step 3: Implement the baseline change**

In `database/snapshots.py`, replace the body of `append_paper_snapshot` between the `with` statements and the snapshot INSERT. Current code (lines 113–124):

```python
    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT content_hash, status FROM paper_snapshots "
                "WHERE paper_id = %s ORDER BY scraped_at DESC LIMIT 1",
                (paper_id,),
            )
            prev = cursor.fetchone()
            if prev and prev['content_hash'] == content_hash:
                return PaperSnapshotResult(changed=False)

            old_status = prev['status'] if prev else None
```

New code:

```python
    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT content_hash FROM paper_snapshots "
                "WHERE paper_id = %s ORDER BY scraped_at DESC LIMIT 1",
                (paper_id,),
            )
            prev = cursor.fetchone()
            if prev and prev['content_hash'] == content_hash:
                return PaperSnapshotResult(changed=False)

            # Baseline is the paper's effective status, not the latest raw
            # snapshot: snapshots keep raw LLM output and flap when co-author
            # pages disagree, which would re-emit forward events every cycle.
            # FOR UPDATE serializes concurrent extractions of the same paper.
            cursor.execute(
                "SELECT status FROM papers WHERE id = %s FOR UPDATE",
                (paper_id,),
            )
            paper_row = cursor.fetchone()
            old_status = paper_row['status'] if paper_row else None
```

Also update the function docstring (line 107–109) to:

```python
    """Append a paper snapshot if metadata changed. Updates denormalized papers table.
    Returns PaperSnapshotResult with status change info for explicit event emission;
    old_status is the paper's effective (monotone) status, not the latest raw snapshot.
    Hash check and insert run in a single transaction to prevent race conditions."""
```

Everything after (`INSERT INTO paper_snapshots`, the `effective_status` guard, `UPDATE papers`, commit, return) is unchanged — the guard already keys off `old_status`.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `poetry run pytest tests/test_snapshots_integration.py -v`
Expected: ALL PASS (new class + all pre-existing classes in the file).

- [ ] **Step 5: Run the full Python suite**

Run: `poetry run pytest -q`
Expected: 0 failures (baseline was 904 passed). If `tests/test_save_publications.py` or `tests/test_feed_events_emitter.py` fail on fetchone call counts, fix the mocks there the same way as Step 1's helper (two-read side_effect), NOT by weakening the implementation.

- [ ] **Step 6: Commit**

```bash
git add database/snapshots.py tests/test_snapshots_integration.py
git commit -m "fix: compare status against effective papers.status to stop event flapping"
```

---

### Task 2: Cleanup migration for historical non-advancing events

**Files:**
- Modify: `database/schema.py:715-717` (append a new migration block after the existing "status regression cleanup" block, before `finally:`)
- Test: `tests/test_startup.py` (append new test class)

**Context for the implementer:** Migrations are idempotent try/except blocks inside `create_tables()`, run under the `econ_migrations` advisory lock. The `_S` variable (the FIELD() rank list) is already defined at `database/schema.py:685` in the same scope and must be reused. Migration tests run `create_tables()` against a fully mocked connection and assert on the executed SQL strings (see `TestMigrationAdvisoryLock` in `tests/test_startup.py`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_startup.py`:

```python
class TestForwardFlappingCleanupMigration:
    """create_tables must delete status_change events that do not advance past
    the highest rank already reached by an earlier event for the same paper."""

    def test_create_tables_runs_forward_flapping_delete(self):
        mock_conn, mock_cursor = _make_mock_conn()
        from database import Database

        with patch("database.schema.get_connection", return_value=mock_conn):
            with patch("database.schema.seed_research_fields"), \
                 patch("database.schema.seed_jel_codes"):
                Database.create_tables()

        executed_sql = [str(c) for c in mock_cursor.execute.call_args_list]
        flap_deletes = [s for s in executed_sql if "DELETE b FROM feed_events b" in s]
        assert len(flap_deletes) == 1
        # Self-join orders by (created_at, id) and compares new_status ranks
        assert "a.created_at < b.created_at" in flap_deletes[0]
        assert "a.id < b.id" in flap_deletes[0]
        assert "FIELD(a.new_status" in flap_deletes[0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_startup.py::TestForwardFlappingCleanupMigration -v`
Expected: FAIL — `len(flap_deletes) == 1` is 0, no such SQL exists yet.

- [ ] **Step 3: Implement the migration**

In `database/schema.py`, insert after the existing cleanup block's `except` (line 716, `logging.warning("Migration: status regression cleanup: %s", e)`) and before `finally:` (line 717), at the same indentation as the surrounding `try` blocks:

```python
                    # Remove forward-flapping duplicates: a status_change event
                    # is spurious unless it advances past the highest rank an
                    # earlier event already reached for the same paper
                    try:
                        cursor.execute(f"""
                            DELETE b FROM feed_events b
                            JOIN feed_events a
                              ON a.paper_id = b.paper_id
                             AND a.event_type = 'status_change'
                             AND (a.created_at < b.created_at
                                  OR (a.created_at = b.created_at AND a.id < b.id))
                             AND FIELD(a.new_status, {_S}) >= FIELD(b.new_status, {_S})
                             AND FIELD(a.new_status, {_S}) > 0
                             AND FIELD(b.new_status, {_S}) > 0
                            WHERE b.event_type = 'status_change'
                        """)
                        flap_deleted = cursor.rowcount
                        conn.commit()
                        if flap_deleted:
                            logging.info(
                                "Migration: removed %d non-advancing status_change events",
                                flap_deleted,
                            )
                    except Exception as e:
                        logging.warning("Migration: forward-flapping cleanup: %s", e)
```

Why this is idempotent: after one run, surviving events per paper strictly increase in `new_status` rank over `(created_at, id)` order, so the join matches nothing on re-runs.

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_startup.py -v`
Expected: ALL PASS (new class + all pre-existing startup/migration tests).

- [ ] **Step 5: Commit**

```bash
git add database/schema.py tests/test_startup.py
git commit -m "fix: migration removes non-advancing status_change events from past flapping"
```

---

### Task 3: Full verification

- [ ] **Step 1: Run the complete Python suite**

Run: `poetry run pytest -q`
Expected: 0 failures, count ≥ 910 (904 baseline + new tests).

- [ ] **Step 2: Verify no frontend impact**

No frontend files were touched; skip jest/tsc. Confirm with `git diff main --stat` that only `database/snapshots.py`, `database/schema.py`, `tests/test_snapshots_integration.py`, `tests/test_startup.py`, and the two docs files changed.

- [ ] **Step 3: Sanity-check the migration SQL against MySQL syntax**

MySQL multi-table DELETE with self-join (`DELETE b FROM feed_events b JOIN feed_events a ...`) is valid MySQL 8 syntax; no action needed beyond the mocked test. (Real-DB verification happens on deploy — the migration is wrapped in try/except and logs a warning rather than crashing startup if it fails.)
