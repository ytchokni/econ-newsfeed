# Paper Detail Page — Design Spec

## Summary

Add a dedicated `/papers/{id}` page that displays full paper metadata (abstract, authors, links, venue, DOI) and a combined history timeline (feed events + snapshot diffs). Papers become clickable from the feed and researcher pages via the entire `PublicationCard` area.

## User Flow

1. User sees papers in the newsfeed or on a researcher detail page
2. Hovering over a `PublicationCard` shows a pointer cursor and subtle hover state
3. Clicking anywhere on the card navigates to `/papers/{id}`
4. Clicking inner interactive elements (author links, DOI, draft, paper links, abstract toggle) does **not** navigate — these keep their current behavior via `stopPropagation`
5. The paper detail page shows full metadata + history
6. A "Back to feed" link at the top returns to the previous page

## Clickable Card Changes

**File:** `app/src/components/PublicationCard.tsx`

- Add `onClick={() => router.push(`/papers/${publication.id}`)}` to the outer card `<div>` (avoids invalid nested `<a>` tags that a wrapping `<Link>` would create)
- Inner `<Link>` elements (author names) and `<a>` tags (DOI, draft, paper links) add `onClick={e => e.stopPropagation()}` to prevent navigation
- The abstract toggle `<button>` also gets `stopPropagation`
- Card hover state: existing `hover:border-[var(--border)]` already provides visual feedback; add `cursor-pointer` to the outer div

## Paper Detail Page

**New files:**
- `app/src/app/papers/[id]/page.tsx` — Next.js route with SSR metadata
- `app/src/app/papers/[id]/PaperDetailContent.tsx` — client component

### Layout: Single Column

Top to bottom:

1. **Back link** — `← Back` using `router.back()`, styled as muted text link
2. **Status pill** — same styling as `PublicationCard` (reuse `statusPillConfig` — extract to shared location)
3. **Title** — serif, large (`text-2xl`)
4. **Authors** — linked to `/researchers/{id}`, same format as card (`I. LastName`)
5. **Venue + Year** — italic, muted
6. **Abstract** — full text, always visible (not collapsed). Same warm background style as the card's expanded abstract
7. **Links bar** — DOI link, draft URL (with status indicator), paper links (PDF, SSRN, NBER, etc.) — same pill styling as card
8. **All Authors (OpenAlex)** — full coauthor list, muted text
9. **History Timeline** — vertical timeline with:
   - Feed events as primary entries: "Discovered" (new_paper) and "Status changed: WP → Published" (status_change) with timestamps
   - Under each status_change event: expandable "Show changes" that displays snapshot diff (what fields changed between consecutive snapshots)
10. **Technical Details (dev only)** — collapsible section shown when `process.env.NODE_ENV === 'development'`:
    - OpenAlex ID (linked to `https://openalex.org/works/{id}`)
    - Source URL
    - `discovered_at` timestamp
    - `draft_url_status`
    - `is_seed` flag
    - `title_hash`

### Shared Code Extraction

Extract `statusPillConfig` from `PublicationCard.tsx` into a shared module (e.g., `app/src/lib/publication-utils.ts`) so both `PublicationCard` and `PaperDetailContent` can use it.

## API Changes

### Extend `GET /api/publications/{id}`

The existing endpoint already returns paper metadata and snapshots (via `?include_history=true`). Add **feed events** to the response:

```json
{
  "id": 1,
  "title": "...",
  "authors": [...],
  "year": "2026",
  "venue": "AER",
  "status": "published",
  "abstract": "...",
  "doi": "10.1257/...",
  "source_url": "https://...",
  "discovered_at": "2026-01-05T00:00:00Z",
  "draft_url": "https://...",
  "draft_url_status": "valid",
  "coauthors": [...],
  "links": [...],
  "history": [...],
  "feed_events": [
    {
      "id": 10,
      "event_type": "status_change",
      "old_status": "working_paper",
      "new_status": "published",
      "created_at": "2026-03-10T12:00:00Z"
    },
    {
      "id": 1,
      "event_type": "new_paper",
      "old_status": null,
      "new_status": null,
      "created_at": "2026-01-05T00:00:00Z"
    }
  ]
}
```

**Implementation:** When `include_history=true`, also query `feed_events` for the paper:

```sql
SELECT id, event_type, old_status, new_status, created_at
FROM feed_events
WHERE paper_id = %s
ORDER BY created_at DESC
```

Also add `is_seed`, `title_hash`, and `openalex_id` to the single-publication response (already in the DB row, just not returned currently). These are only needed for the dev-mode technical details section.

### Frontend: New SWR Hook

**File:** `app/src/lib/api.ts`

```typescript
export function usePublication(id: number) {
  return useSWR<PublicationDetail>(
    `/api/publications/${id}?include_history=true`,
    fetchJson
  );
}
```

### Frontend: New Types

**File:** `app/src/lib/types.ts`

```typescript
export interface FeedEvent {
  id: number;
  event_type: EventType;
  old_status: PublicationStatus | null;
  new_status: PublicationStatus | null;
  created_at: string;
}

export interface PaperSnapshot {
  status: PublicationStatus | null;
  venue: string | null;
  abstract: string | null;
  draft_url: string | null;
  draft_url_status: DraftUrlStatus | null;
  year: string | null;
  scraped_at: string;
  source_url: string | null;
}

export interface PublicationDetail extends Publication {
  feed_events: FeedEvent[];
  history: PaperSnapshot[];
  is_seed: boolean;
  title_hash: string;
  openalex_id: string | null;
}
```

## History Timeline Component

The timeline merges feed events and snapshot diffs into a single chronological view:

- **Feed events** are the primary entries (human-readable: "Discovered", "Status changed")
- **Snapshot diffs** are shown as expandable detail under `status_change` events
  - Compare consecutive snapshots to show what changed (e.g., "venue: null → AER", "abstract: added")
  - Only show fields that actually changed between snapshots
- Timeline is ordered newest-first (most recent event at top)
- Each entry shows a relative or absolute date

## Error & Loading States

- Loading: skeleton placeholder matching the single-column layout
- 404: "Paper not found" message with link back to feed
- Error: generic error message with retry button

## No Changes Required

- Database schema — no new tables or columns
- Existing `PublicationCard` rendering logic — stays the same, just wrapped in a link
- Other pages — `/researchers/[id]` cards also become clickable (same `PublicationCard` component)
