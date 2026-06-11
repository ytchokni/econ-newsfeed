# Design: Stop status-change event flapping

**Date:** 2026-06-11
**Status:** Approved (approach chosen by user)
**Related:** #145 / PR #153 (blocked backward regressions; this closes the remaining forward-flapping gap)

## Problem

`append_paper_snapshot()` (database/snapshots.py) takes `old_status` from the
**latest raw paper snapshot**. Snapshots deliberately store raw LLM output, and
the same paper is extracted from multiple co-authors' pages, which can disagree
on status. PR #153 blocks *backward* events, but forward flapping persists:

1. Alice's page says `published` → snapshot(`published`).
2. Bob's page says `working_paper` → regression, event correctly suppressed,
   but snapshot(`working_paper`) is stored.
3. Next cycle, Alice's page again → previous snapshot is Bob's
   `working_paper`, so `working_paper → published` looks like a forward
   progression — a **spurious event fires**. Repeats every fetch cycle.

Prod also still contains spurious forward events from past flapping (the
surviving halves of contradictory pairs the #153 cleanup left behind).

## Solution (chosen: compare against effective status)

### 1. Comparison baseline change — `database/snapshots.py`

In `append_paper_snapshot()`, read the current `papers.status` with
`SELECT ... FOR UPDATE` (locking the paper row for the transaction) and use it
as `old_status` instead of the latest snapshot's raw status.

Unchanged:
- Snapshot insert — raw LLM output, full audit trail.
- `papers` update — `effective_status` regression guard from #153.
- Emit site in `publication.py` `append_snapshots_for_pubs()` — already keys
  off `PaperSnapshotResult.status_changed`, which requires strict progression.

The row lock closes the race where two co-author pages are extracted
concurrently, both read the same old status, and both emit.

**Invariant:** `papers.status` rank never decreases, and a `status_change`
event fires only when that rank actually increases → at most 4 status-change
events per paper, ever (one per rank crossed in
`working_paper < reject_and_resubmit < revise_and_resubmit < accepted < published`).

Note: the snapshot-level change-detection hash check (skip if content
identical to previous snapshot) stays first; the effective-status read only
replaces where `old_status` comes from.

### 2. Cleanup migration — `database/schema.py`

Appended to the existing migration block, same idempotent style as the #153
cleanup. Delete any `status_change` event B where an earlier event A
(`created_at, id` order) on the same paper has
`FIELD(A.new_status, ...) >= FIELD(B.new_status, ...)` — i.e. B does not
advance past the highest rank already reached. The #153 `papers.status`
restore already re-runs on every boot and needs no change.

### 3. Tests — `tests/test_snapshots_integration.py` (extend)

- Flapping: `published` → `working_paper` → `published` snapshots ⇒ no
  status-change emission after the first state is established.
- Genuine progression past effective status still emits
  (`accepted` → `published`).
- First-ever status (effective status NULL) emits nothing, as today.
- Migration: seed duplicate/non-advancing forward events, run cleanup, assert
  only the earliest event per rank-crossing survives.

## Out of scope

- `venue` / `abstract` / `draft_url` on `papers` being overwritten by
  whichever page extracted last (a sparser co-author page can erase a richer
  one). Deserves its own issue.
- Per-source-URL snapshot semantics (rejected alternative: needs event dedup
  on top anyway, more churn for the same outcome).
- Emit-time dedup against `feed_events` history (rejected alternative: makes
  event history a second source of truth).
