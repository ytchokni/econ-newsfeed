# Feed Events Design

## Problem

The newsfeed currently shows all non-seed papers. It should instead show two distinct event types: new working/unpublished papers and status changes on existing papers. Published-on-first-discovery papers and papers with unknown status should be excluded.

## Requirements

1. Feed has two item types: **new_paper** and **status_change**
2. **new_paper**: paper is non-seed, status is known, and status is not `published`
3. **status_change**: an existing paper's status changes (both old and new status must be non-NULL)
4. Both items coexist — a paper can appear as "new" and later again as "status update"
5. Papers discovered already published (status = `published`) never appear
6. Papers with unknown status (NULL) never appear

## Data Model

### New table: `feed_events`

```sql
CREATE TABLE feed_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    paper_id INT NOT NULL,
    event_type ENUM('new_paper', 'status_change') NOT NULL,
    old_status ENUM('published','accepted','revise_and_resubmit','reject_and_resubmit','working_paper') DEFAULT NULL,
    new_status ENUM('published','accepted','revise_and_resubmit','reject_and_resubmit','working_paper') DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    INDEX idx_paper_id (paper_id),
    INDEX idx_created_at (created_at),
    INDEX idx_event_type (event_type)
);
```

### Write points

**1. `publication.py` — `save_publications()`**

Inside the existing `with Database.get_connection()` block, **before** `conn.commit()`. The event INSERT must be atomic with the paper INSERT. Use `cursor.lastrowid` (non-zero means a new row was inserted, not a duplicate via INSERT IGNORE):

```python
paper_id = cursor.lastrowid
if paper_id and not is_seed and pub.get('status') and pub.get('status') != 'published':
    cursor.execute(
        """INSERT INTO feed_events (paper_id, event_type, new_status, created_at)
           VALUES (%s, 'new_paper', %s, %s)""",
        (paper_id, pub.get('status'), now)
    )
# ... then conn.commit() as before
```

Note: `cursor.lastrowid` is the correct duplicate-detection signal (returns 0 for INSERT IGNORE on duplicate), not `cursor.rowcount` (which can return 1 even on duplicates in some MySQL driver versions).

**2. `database.py` — `append_paper_snapshot()`**

When the snapshot detects a content hash change, retrieve the previous snapshot's status before inserting the new snapshot. Add a query to fetch `old_status`:

```python
# Fetch previous status
old_row = cursor.execute(
    "SELECT status FROM paper_snapshots WHERE paper_id = %s ORDER BY scraped_at DESC LIMIT 1",
    (paper_id,)
)
old_status = old_row[0] if old_row else None

# ... insert new snapshot ...

# Create feed event if status changed
if old_status != new_status and old_status is not None and new_status is not None:
    cursor.execute(
        """INSERT INTO feed_events (paper_id, event_type, old_status, new_status, created_at)
           VALUES (%s, 'status_change', %s, %s, %s)""",
        (paper_id, old_status, new_status, now)
    )
```

This must happen within the same transaction as the snapshot insert.

### Backfill

All current papers are seed (`is_seed=TRUE`), so no backfill of feed_events is needed. The table starts empty.

## API Changes

### `GET /api/publications`

Query switches from `papers` to `feed_events` joined to `papers`. Column aliases prevent ambiguity:

```sql
SELECT fe.id AS event_id, fe.event_type, fe.old_status AS event_old_status,
       fe.new_status AS event_new_status, fe.created_at AS event_date,
       p.id AS paper_id, p.title, p.year, p.venue, p.url, p.status, p.abstract,
       p.draft_url, p.draft_url_status
FROM feed_events fe
JOIN papers p ON p.id = fe.paper_id
WHERE 1=1
  -- existing filters (year, status, institution, researcher_id) apply on p.*
  -- IMPORTANT: `since` filter must apply to fe.created_at (not p.timestamp)
ORDER BY fe.created_at DESC
LIMIT %s OFFSET %s
```

**`since` filter**: Must filter on `fe.created_at >= %s` (not `p.timestamp`). This is a semantic change — `since` now means "events since this time", which is correct for polling clients. A `status_change` event created today for a paper discovered months ago must appear when polling with `since=<today>`.

### Response shape

A new `_format_feed_event()` helper replaces `_format_publication()` for the feed endpoint. Column-to-field mapping:

| SQL column | JSON field | Notes |
|-----------|-----------|-------|
| `p.id` | `id` | Paper ID — used by frontend for React keys and detail page links |
| `fe.id` | `event_id` | Feed event ID — unique per event |
| `fe.event_type` | `event_type` | `"new_paper"` or `"status_change"` |
| `fe.old_status` | `old_status` | Previous status (NULL for new_paper) |
| `fe.new_status` | `new_status` | Current status at time of event |
| `fe.created_at` | `event_date` | When the event was created (ISO 8601) |
| `p.*` | (existing fields) | title, year, venue, url, status, abstract, etc. |

```json
{
  "id": 42,
  "event_id": 7,
  "event_type": "new_paper",
  "old_status": null,
  "new_status": "working_paper",
  "event_date": "2026-03-19T10:30:00Z",
  "title": "...",
  "authors": [...],
  "venue": "...",
  "year": "2026",
  "status": "working_paper"
}
```

- `event_date` comes from `feed_events.created_at`
- `timestamp` remains on the paper (original discovery time)
- The `include_seed` parameter becomes unnecessary — feed_events only contains qualifying items by construction
- Existing filters (`status`, `year`, `institution`, `researcher_id`) continue to work
- The existing `_format_publication()` helper remains for the researcher detail page (which still queries papers directly)

## Frontend Changes

### PublicationCard

Two visual modes based on `event_type`:

**`new_paper`**: Same as current card — title, authors, venue, year, status badge, abstract snippet.

**`status_change`**: Same card plus a banner at the top showing "Status update: working_paper → accepted at QJE" with colored old/new status badges.

### Types

```typescript
interface Publication {
  // ...existing fields
  event_id: number;
  event_type: 'new_paper' | 'status_change';
  old_status: string | null;
  new_status: string | null;
  event_date: string;
}
```

### Empty state

Feed shows: "No new publications yet. Papers will appear here as researchers update their pages."

### Researcher detail page

No changes — continues to show all papers regardless of seed/event status.

## Files to Modify

| File | Change |
|------|--------|
| `database.py` | Add `feed_events` table to `create_tables()`, add status retrieval + event INSERT to `append_paper_snapshot()` |
| `publication.py` | Add `new_paper` event INSERT inside existing transaction, using `cursor.lastrowid` |
| `api.py` | Rework `/api/publications` to query `feed_events`, add `_format_feed_event()`, update `since` to use `fe.created_at` |
| `app/src/lib/types.ts` | Add `event_id`, `event_type`, `old_status`, `new_status`, `event_date` to `Publication` |
| `app/src/components/PublicationCard.tsx` | Add status-change banner mode |
| `app/src/app/NewsfeedContent.tsx` | Update empty state message |

## Edge Cases

- Paper with NULL status: excluded from feed entirely (no event created)
- Status change from NULL to known status: skipped (old_status is NULL)
- Status change from known to NULL: skipped (new_status is NULL)
- Co-authored paper on multiple researcher URLs: `INSERT IGNORE` dedup via `title_hash` means `cursor.lastrowid` is 0 for the second URL — no duplicate event
- Paper already in feed as new_paper, then status changes: both events coexist in feed
- `since` parameter: filters on `fe.created_at` (event time), not `p.timestamp` (discovery time)
