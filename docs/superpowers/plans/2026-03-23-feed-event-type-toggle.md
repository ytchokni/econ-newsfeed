# Feed Event Type Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pill toggle to the newsfeed filter bar that switches between "New Projects" and "Status Changes" feed event types, with server-side filtering and URL persistence.

**Architecture:** Add an `event_type` query param to the existing `GET /api/publications` endpoint. On the frontend, add a segmented pill control inside `FilterBar` that drives `event_type` through the existing `FeedFilters` → `usePublications` → API pipeline. Tab state is synced to URL `?tab=` param.

**Tech Stack:** Python/FastAPI (backend), Next.js/React/TypeScript/SWR/Tailwind (frontend)

**Spec:** `docs/superpowers/specs/2026-03-23-feed-event-type-toggle-design.md`

**Worktree:** `.worktrees/feed-event-toggle`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `api.py` | Modify (lines 466-553) | Add `event_type` query param + validation + SQL WHERE clause |
| `app/src/lib/types.ts` | Modify (line 94-100) | Add `event_type` to `FeedFilters` |
| `app/src/lib/api.ts` | Modify (line 34-38) | Pass `event_type` in `buildPublicationsUrl` |
| `app/src/app/NewsfeedContent.tsx` | Modify (lines 166-346) | Add pill toggle, `activeTab` state, URL sync, filter reset |
| `tests/test_api_publications.py` | Modify (add tests) | Test `event_type` filter param |
| `app/src/lib/__tests__/api.test.ts` | Modify (add tests) | Test `event_type` in URL building |
| `app/src/app/__tests__/NewsfeedContent.test.tsx` | Modify (add tests) | Test toggle rendering and behavior |

---

### Task 1: Backend — Add `event_type` query param to API

**Files:**
- Modify: `api.py:466-553`
- Test: `tests/test_api_publications.py`

- [ ] **Step 1: Write failing tests for `event_type` filter**

Add to `tests/test_api_publications.py`, inside `TestListPublications`:

```python
def test_event_type_filter_new_paper(self, client):
    """?event_type=new_paper returns only new_paper events."""
    with (
        patch("api.Database.fetch_one", return_value={"total": 2}),
        patch("api.Database.fetch_all") as mock_fetch,
    ):
        mock_fetch.side_effect = [
            SAMPLE_PUBLICATIONS[:2],
            BATCH_AUTHORS_PUBS_1_2_3,
            [],  # coauthors
            [],  # links
        ]
        response = client.get("/api/publications?event_type=new_paper")

    assert response.status_code == 200
    # Verify the SQL received the event_type condition
    call_args = mock_fetch.call_args_list[0]
    sql = call_args[0][0]
    assert "fe.event_type" in sql

def test_event_type_filter_status_change(self, client):
    """?event_type=status_change returns only status_change events."""
    status_change_pub = {
        **SAMPLE_PUBLICATIONS[0],
        "event_id": 200,
        "event_type": "status_change",
        "old_status": "working_paper",
        "new_status": "accepted",
    }
    with (
        patch("api.Database.fetch_one", return_value={"total": 1}),
        patch("api.Database.fetch_all") as mock_fetch,
    ):
        mock_fetch.side_effect = [
            [status_change_pub],
            BATCH_AUTHORS_PUB1,
            [],  # coauthors
            [],  # links
        ]
        response = client.get("/api/publications?event_type=status_change")

    assert response.status_code == 200
    call_args = mock_fetch.call_args_list[0]
    sql = call_args[0][0]
    assert "fe.event_type" in sql

def test_event_type_omitted_returns_both(self, client):
    """Omitting event_type returns all events (backward compatible)."""
    with (
        patch("api.Database.fetch_one", return_value={"total": 3}),
        patch("api.Database.fetch_all") as mock_fetch,
    ):
        mock_fetch.side_effect = [
            SAMPLE_PUBLICATIONS,
            BATCH_AUTHORS_PUBS_1_2_3,
            [],  # coauthors
            [],  # links
        ]
        response = client.get("/api/publications")

    assert response.status_code == 200
    call_args = mock_fetch.call_args_list[0]
    sql = call_args[0][0]
    assert "fe.event_type" not in sql

def test_event_type_invalid_returns_400(self, client):
    """Invalid event_type value returns 400."""
    response = client.get("/api/publications?event_type=invalid")
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/feed-event-toggle && poetry run pytest tests/test_api_publications.py -k "event_type" -v`
Expected: 4 FAIL (endpoint doesn't accept `event_type` yet)

- [ ] **Step 3: Implement `event_type` param in `api.py`**

In `api.py`, add `event_type` parameter to the function signature (after `search`):

```python
event_type: str | None = Query(None),
```

Add validation after the existing `preset` validation block (after line 496):

```python
valid_event_types = {"new_paper", "status_change"}
if event_type and event_type not in valid_event_types:
    raise HTTPException(status_code=400, detail=f"Invalid event_type value '{event_type}'. Must be one of: {', '.join(sorted(valid_event_types))}")
```

Add WHERE condition after the `search_term` block (after line 551, before `where = ...`):

```python
if event_type:
    conditions.append("fe.event_type = %s")
    params.append(event_type)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/feed-event-toggle && poetry run pytest tests/test_api_publications.py -k "event_type" -v`
Expected: 4 PASS

- [ ] **Step 5: Run full Python test suite**

Run: `cd .worktrees/feed-event-toggle && poetry run pytest --tb=short -q`
Expected: All previously passing tests still pass (397+4 pass, 5 pre-existing failures in test_api_search.py)

- [ ] **Step 6: Commit**

```bash
cd .worktrees/feed-event-toggle
git add api.py tests/test_api_publications.py
git commit -m "feat(api): add event_type filter to GET /api/publications"
```

---

### Task 2: Frontend — Add `event_type` to types and API client

**Files:**
- Modify: `app/src/lib/types.ts:94-100`
- Modify: `app/src/lib/api.ts:34-38`
- Test: `app/src/lib/__tests__/api.test.ts`

- [ ] **Step 1: Write failing test for `event_type` in URL building**

Add to `app/src/lib/__tests__/api.test.ts`:

```typescript
describe("buildPublicationsUrl with event_type", () => {
  it("includes event_type param in URL", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockPublicationsResponse),
    });

    await getPublications(1, 20, { event_type: "new_paper" });
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("event_type=new_paper")
    );
  });

  it("includes event_type=status_change param in URL", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockPublicationsResponse),
    });

    await getPublications(1, 20, { event_type: "status_change" });
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("event_type=status_change")
    );
  });

  it("omits event_type when not provided", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockPublicationsResponse),
    });

    await getPublications(1, 20, {});
    expect(global.fetch).toHaveBeenCalledWith(
      expect.not.stringContaining("event_type")
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/feed-event-toggle/app && npx jest --testPathPattern=api.test.ts --verbose`
Expected: 3 FAIL — `event_type` not a valid key on `FeedFilters`

- [ ] **Step 3: Add `event_type` to `FeedFilters` type**

In `app/src/lib/types.ts`, add to the `FeedFilters` interface (after `search?: string;`):

```typescript
event_type?: "new_paper" | "status_change";
```

- [ ] **Step 4: Add `event_type` to `buildPublicationsUrl`**

In `app/src/lib/api.ts`, add after line 38 (`if (filters?.search) ...`):

```typescript
if (filters?.event_type) params.set("event_type", filters.event_type);
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd .worktrees/feed-event-toggle/app && npx jest --testPathPattern=api.test.ts --verbose`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd .worktrees/feed-event-toggle
git add app/src/lib/types.ts app/src/lib/api.ts app/src/lib/__tests__/api.test.ts
git commit -m "feat(frontend): add event_type to FeedFilters and API client"
```

---

### Task 3: Frontend — Add pill toggle to NewsfeedContent

**Files:**
- Modify: `app/src/app/NewsfeedContent.tsx`
- Test: `app/src/app/__tests__/NewsfeedContent.test.tsx`

- [ ] **Step 1: Write failing tests for the toggle**

Add to `app/src/app/__tests__/NewsfeedContent.test.tsx`, inside the `describe("NewsfeedContent", ...)` block:

```typescript
it("renders New Projects and Status Changes toggle buttons", async () => {
  (global.fetch as jest.Mock).mockResolvedValueOnce({
    ok: true,
    json: async () => page1,
  });

  renderWithSWR(<NewsfeedContent />);

  await waitFor(() => {
    expect(screen.getByRole("button", { name: /new projects/i })).toBeInTheDocument();
  });
  expect(screen.getByRole("button", { name: /status changes/i })).toBeInTheDocument();
});

it("defaults to New Projects tab as active", async () => {
  (global.fetch as jest.Mock).mockResolvedValueOnce({
    ok: true,
    json: async () => page1,
  });

  renderWithSWR(<NewsfeedContent />);

  await waitFor(() => {
    expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
  });

  // Verify the API was called with event_type=new_paper
  expect(global.fetch).toHaveBeenCalledWith(
    expect.stringContaining("event_type=new_paper")
  );
});

it("switches to Status Changes tab and resets filters", async () => {
  (global.fetch as jest.Mock)
    .mockResolvedValueOnce({ ok: true, json: async () => page1 })
    .mockResolvedValueOnce({ ok: true, json: async () => page1 });

  renderWithSWR(<NewsfeedContent />);

  await waitFor(() => {
    expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
  });

  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /status changes/i }));

  await waitFor(() => {
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("event_type=status_change")
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/feed-event-toggle/app && npx jest --testPathPattern=NewsfeedContent.test.tsx --verbose`
Expected: 3 FAIL — no toggle buttons exist yet

- [ ] **Step 3: Implement the pill toggle in `NewsfeedContent.tsx`**

**3a. Add `activeTab` state and URL sync to `NewsfeedContent`:**

Replace the state/hook block at lines 270-273:

```typescript
export default function NewsfeedContent() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<FeedFilters>({});
  const { data, error, isLoading } = usePublications(page, 20, filters);
```

With:

```typescript
type TabValue = "new_paper" | "status_change";

function getInitialTab(): TabValue {
  if (typeof window === "undefined") return "new_paper";
  const params = new URLSearchParams(window.location.search);
  const tab = params.get("tab");
  return tab === "status_change" ? "status_change" : "new_paper";
}

export default function NewsfeedContent() {
  const [activeTab, setActiveTab] = useState<TabValue>(getInitialTab);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<FeedFilters>({});
  const mergedFilters = { ...filters, event_type: activeTab };
  const { data, error, isLoading } = usePublications(page, 20, mergedFilters);
```

**3b. Add tab switch handler** after `handleFilterChange`:

```typescript
const handleTabChange = useCallback((tab: TabValue) => {
  setActiveTab(tab);
  setFilters({});
  setPage(1);
  const url = new URL(window.location.href);
  url.searchParams.set("tab", tab);
  window.history.replaceState({}, "", url.toString());
}, []);
```

**3c. Add `activeTab` and `onTabChange` props to `FilterBar`:**

Update the `FilterBar` function signature:

```typescript
function FilterBar({
  filters,
  onChange,
  activeTab,
  onTabChange,
}: {
  filters: FeedFilters;
  onChange: (next: FeedFilters) => void;
  activeTab: TabValue;
  onTabChange: (tab: TabValue) => void;
}) {
```

**3d. Add the pill toggle** as the first element inside the FilterBar's outer div (before the search input div at line 213):

```tsx
{/* Event type toggle */}
<div className="flex items-center gap-2">
  <div className="inline-flex bg-[var(--bg)] rounded-lg p-0.5">
    <button
      onClick={() => onTabChange("new_paper")}
      className={`px-4 py-1.5 text-sm font-medium rounded-md transition-all ${
        activeTab === "new_paper"
          ? "bg-[var(--bg-header)] text-white shadow-sm"
          : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
      }`}
    >
      New Projects
    </button>
    <button
      onClick={() => onTabChange("status_change")}
      className={`px-4 py-1.5 text-sm font-medium rounded-md transition-all ${
        activeTab === "status_change"
          ? "bg-[var(--bg-header)] text-white shadow-sm"
          : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
      }`}
    >
      Status Changes
    </button>
  </div>
</div>
```

**3e. Update the FilterBar invocation** in the JSX return (line 283):

```tsx
<FilterBar
  filters={filters}
  onChange={handleFilterChange}
  activeTab={activeTab}
  onTabChange={handleTabChange}
/>
```

**3f. Update the empty state message** (line 299):

Replace:
```tsx
<EmptyState message="No new publications yet. Papers will appear here as researchers update their pages." />
```

With:
```tsx
<EmptyState
  message={
    activeTab === "new_paper"
      ? "No new publications yet. Papers will appear here as researchers update their pages."
      : "No status changes yet. Updates will appear here when papers change status."
  }
/>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/feed-event-toggle/app && npx jest --testPathPattern=NewsfeedContent.test.tsx --verbose`
Expected: All tests PASS (existing + 3 new)

- [ ] **Step 5: Run full frontend test suite**

Run: `cd .worktrees/feed-event-toggle/app && npx jest`
Expected: All 71+ tests PASS

- [ ] **Step 6: Commit**

```bash
cd .worktrees/feed-event-toggle
git add app/src/app/NewsfeedContent.tsx app/src/app/__tests__/NewsfeedContent.test.tsx
git commit -m "feat(frontend): add event type pill toggle to newsfeed filter bar"
```

---

### Task 4: Manual smoke test

- [ ] **Step 1: Start the dev server and verify the toggle works**

Run: `cd .worktrees/feed-event-toggle && make dev`

Check in browser at `http://localhost:3000`:
1. Pill toggle visible at top of filter bar with "New Projects" active by default
2. URL shows `?tab=new_paper`
3. Clicking "Status Changes" switches view, URL updates to `?tab=status_change`
4. Filters reset when switching tabs
5. "Clear all" does NOT reset the active tab
6. Direct navigation to `?tab=status_change` loads with Status Changes active
7. Empty state messages differ per tab

- [ ] **Step 2: Stop dev server**

Run: `make kill`
