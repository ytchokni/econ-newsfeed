# Feed Event Type Toggle

## Summary

Add a segmented pill toggle to the main newsfeed page that lets users switch between **New Projects** (new working papers appearing on researcher pages) and **Status Changes** (existing papers changing status, e.g. working_paper → accepted). Defaults to New Projects.

## Motivation

The current newsfeed intermixes both event types in a single chronological list. Users primarily care about one or the other at any given time — separating them makes the feed more focused and scannable.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Toggle placement | Pill inside filter bar | Compact, keeps everything in one card, minimal layout change |
| Default tab | New Projects (`new_paper`) | Primary use case is discovering new papers |
| URL persistence | `?tab=new_paper` / `?tab=status_change` | Shareable/bookmarkable |
| Filter behavior on tab switch | Reset all filters + page to 1 | The two views have different contexts |
| Filtering approach | Server-side via API query param | Consistent with existing filter pattern, correct pagination |

## Backend Changes

### `api.py` — `GET /api/publications`

Add optional query parameter:

```
event_type: Optional[str] = None   # "new_paper" or "status_change"
```

When present, append to both the count and data SQL queries:

```sql
AND fe.event_type = %s
```

When absent, no change — returns both types (backward compatible).

Validate that the value is one of `new_paper` or `status_change`; return 400 otherwise.

## Frontend Changes

### `types.ts`

Add to `FeedFilters` interface:

```typescript
event_type?: "new_paper" | "status_change";
```

### `api.ts`

In `buildPublicationsUrl()`, include `event_type` param when present on filters.

### `NewsfeedContent.tsx`

**New state:**
- `activeTab`: `"new_paper" | "status_change"`, initialized from URL `?tab=` param, defaults to `"new_paper"`

**Pill toggle component:**
- Segmented control rendered as first element inside the `FilterBar` card, above the search input
- Two options: "New Projects" / "Status Changes"
- Active tab: `bg-[var(--bg-header)]` with white text
- Inactive tab: transparent with `text-[var(--text-muted)]`
- Container: `bg-[var(--bg)]` with `rounded-lg` and 2px padding

**Tab switch behavior:**
1. Set `activeTab` to the selected value
2. Reset `filters` to `{}`
3. Reset `page` to 1
4. Update URL search param `tab`

Note: The "Clear all" button in the filter bar should only reset sub-filters (status, year, institution, search) — it should NOT reset the active tab.

**Empty state:** When no results, show tab-appropriate messaging: "No new publications yet." for New Projects, "No status changes yet." for Status Changes.

**Data flow:**
- `activeTab` is merged into the filters object passed to `usePublications` as `event_type`
- The API client appends `&event_type=<value>` to the request URL

## Files Modified

| File | Change |
|------|--------|
| `api.py` | Add `event_type` query param + SQL WHERE clause |
| `app/src/lib/types.ts` | Add `event_type` to `FeedFilters` |
| `app/src/lib/api.ts` | Pass `event_type` in query string |
| `app/src/app/NewsfeedContent.tsx` | Add pill toggle, tab state, URL sync, filter reset on switch |

## Testing

- **API**: Test that `?event_type=new_paper` returns only new_paper events, `?event_type=status_change` returns only status_change events, and omitting the param returns both
- **Frontend**: Test that toggle switches active tab, resets filters, updates URL, and passes event_type to the API call
