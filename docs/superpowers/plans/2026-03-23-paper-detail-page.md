# Paper Detail Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clickable paper detail page (`/papers/{id}`) showing full metadata, links, and a combined history timeline (feed events + snapshot diffs).

**Architecture:** Extend the existing single-publication API endpoint to include feed events and technical fields. Extract shared UI code (`statusPillConfig`, `formatAuthor`) into a utility module. Build a new Next.js page consuming the endpoint via a new SWR hook. Make `PublicationCard` clickable via `onClick` + `router.push`.

**Tech Stack:** Python/FastAPI (backend), Next.js/React/TypeScript (frontend), SWR (data fetching), Tailwind CSS (styling), Jest + React Testing Library (frontend tests), pytest (backend tests)

**Spec:** `docs/superpowers/specs/2026-03-23-paper-detail-page-design.md`

**Worktree:** `.worktrees/paper-detail-page`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `api.py:603-638` | Add `feed_events`, `is_seed`, `title_hash`, `openalex_id` to single-publication endpoint |
| Modify | `app/src/lib/types.ts` | Add `FeedEvent`, `PaperSnapshot`, `PublicationDetail` types |
| Modify | `app/src/lib/api.ts` | Add `usePublication(id)` SWR hook |
| Create | `app/src/lib/publication-utils.ts` | Shared `statusPillConfig` and `formatAuthor` |
| Modify | `app/src/components/PublicationCard.tsx` | Import from shared utils, add `onClick` navigation + `stopPropagation` on inner elements |
| Create | `app/src/app/papers/[id]/page.tsx` | Next.js route with SSR metadata |
| Create | `app/src/app/papers/[id]/PaperDetailContent.tsx` | Client component: metadata, links, history timeline, dev-only technical details |
| Modify | `tests/test_api_publications.py` | Test feed_events + extra fields in single-publication response |
| Create | `app/src/components/__tests__/PaperDetailContent.test.tsx` | Tests for paper detail page rendering |
| Modify | `app/src/components/__tests__/PublicationCard.test.tsx` | Test clickable card behavior |

---

### Task 1: Extend API — Add feed_events and extra fields to single-publication endpoint

**Files:**
- Modify: `api.py:603-638` (the `get_publication` function)
- Modify: `tests/test_api_publications.py`

- [ ] **Step 1: Write failing test for feed_events in response**

Add to `tests/test_api_publications.py`:

```python
SAMPLE_FEED_EVENTS = [
    {"id": 5, "event_type": "status_change", "old_status": "working_paper",
     "new_status": "published", "created_at": datetime(2026, 3, 20, 12, 0)},
    {"id": 1, "event_type": "new_paper", "old_status": None,
     "new_status": None, "created_at": datetime(2026, 3, 15, 14, 30)},
]

SAMPLE_SNAPSHOTS = [
    {"status": "published", "venue": "JLE", "abstract": None,
     "draft_url": "https://ssrn.com/abstract=1", "draft_url_status": "valid",
     "year": "2024", "scraped_at": datetime(2026, 3, 20, 12, 0),
     "source_url": "https://example.com/pub"},
]


class TestGetPublicationHistory:
    """Tests for GET /api/publications/{id}?include_history=true."""

    def test_includes_feed_events(self, client):
        """Response includes feed_events when include_history=true."""
        pub_detail = {**SAMPLE_PUB_DETAIL, "is_seed": False,
                      "title_hash": "abc123", "openalex_id": "W12345"}
        with (
            patch("api.Database.fetch_one", return_value=pub_detail),
            patch("api.Database.fetch_all") as mock_fetch,
            patch("api.Database.get_paper_snapshots", return_value=SAMPLE_SNAPSHOTS),
        ):
            mock_fetch.side_effect = [
                SAMPLE_AUTHORS_PUB1,  # authors
                [],  # coauthors
                [],  # links
                SAMPLE_FEED_EVENTS,  # feed_events
            ]
            response = client.get("/api/publications/1?include_history=true")

        assert response.status_code == 200
        body = response.json()
        assert "feed_events" in body
        assert len(body["feed_events"]) == 2
        assert body["feed_events"][0]["event_type"] == "status_change"
        assert body["feed_events"][0]["old_status"] == "working_paper"
        assert body["feed_events"][1]["event_type"] == "new_paper"
        assert "history" in body
        assert len(body["history"]) == 1

    def test_includes_extra_fields(self, client):
        """Response includes is_seed, title_hash, openalex_id when include_history=true."""
        pub_detail = {**SAMPLE_PUB_DETAIL, "is_seed": False,
                      "title_hash": "abc123", "openalex_id": "W12345"}
        with (
            patch("api.Database.fetch_one", return_value=pub_detail),
            patch("api.Database.fetch_all") as mock_fetch,
            patch("api.Database.get_paper_snapshots", return_value=[]),
        ):
            mock_fetch.side_effect = [
                SAMPLE_AUTHORS_PUB1,
                [],  # coauthors
                [],  # links
                [],  # feed_events
            ]
            response = client.get("/api/publications/1?include_history=true")

        assert response.status_code == 200
        body = response.json()
        assert body["is_seed"] is False
        assert body["title_hash"] == "abc123"
        assert body["openalex_id"] == "W12345"

    def test_no_history_without_flag(self, client):
        """feed_events and history are not included without include_history=true."""
        with (
            patch("api.Database.fetch_one", return_value=SAMPLE_PUB_DETAIL),
            patch("api.Database.fetch_all") as mock_fetch,
        ):
            mock_fetch.side_effect = [SAMPLE_AUTHORS_PUB1, [], []]
            response = client.get("/api/publications/1")

        assert response.status_code == 200
        body = response.json()
        assert "feed_events" not in body
        assert "history" not in body
        assert "is_seed" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/paper-detail-page && poetry run pytest tests/test_api_publications.py::TestGetPublicationHistory -v`
Expected: FAIL — `feed_events` key not in response

- [ ] **Step 3: Implement API changes**

In `api.py`, update the `get_publication` function. Change the SQL query to include `is_seed`, `title_hash`, `openalex_id`:

```python
@app.get("/api/publications/{publication_id}")
@limiter.limit("60/minute")
def get_publication(
    request: Request,
    publication_id: int,
    include_history: bool = Query(False),
):
    row = Database.fetch_one(
        "SELECT id, title, year, venue, source_url, discovered_at, status, draft_url, abstract, draft_url_status, doi, is_seed, title_hash, openalex_id FROM papers WHERE id = %s",
        (publication_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Publication not found")

    authors = _get_authors_for_publication(publication_id)
    coauthors_map = _get_coauthors_for_publications([publication_id])
    links = _get_links_for_publication(publication_id)
    result = _format_publication(row, authors, coauthors_map.get(publication_id, []), links)

    if include_history:
        snapshots = Database.get_paper_snapshots(publication_id)
        result["history"] = [
            {
                "status": s['status'],
                "venue": s['venue'],
                "abstract": s['abstract'],
                "draft_url": s['draft_url'],
                "draft_url_status": s['draft_url_status'],
                "year": s['year'],
                "scraped_at": _iso_z(s['scraped_at']),
                "source_url": s['source_url'],
            }
            for s in snapshots
        ]

        feed_events = Database.fetch_all(
            "SELECT id, event_type, old_status, new_status, created_at FROM feed_events WHERE paper_id = %s ORDER BY created_at DESC",
            (publication_id,),
        )
        result["feed_events"] = [
            {
                "id": fe['id'],
                "event_type": fe['event_type'],
                "old_status": fe['old_status'],
                "new_status": fe['new_status'],
                "created_at": _iso_z(fe['created_at']),
            }
            for fe in feed_events
        ]

        result["is_seed"] = bool(row.get('is_seed'))
        result["title_hash"] = row.get('title_hash')
        result["openalex_id"] = row.get('openalex_id')

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/paper-detail-page && poetry run pytest tests/test_api_publications.py -v`
Expected: All tests PASS (including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
cd .worktrees/paper-detail-page
git add api.py tests/test_api_publications.py
git commit -m "feat(api): add feed_events and technical fields to single-publication endpoint"
```

---

### Task 2: Add frontend types and SWR hook

**Files:**
- Modify: `app/src/lib/types.ts`
- Modify: `app/src/lib/api.ts`

- [ ] **Step 1: Add new types to `types.ts`**

Append before the `FeedFilters` interface (line 94):

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

- [ ] **Step 2: Add `usePublication` hook to `api.ts`**

Add the import of `PublicationDetail` to the import block, then add:

```typescript
export function usePublication(id: number) {
  return useSWR<PublicationDetail>(
    `/api/publications/${id}?include_history=true`,
    fetchJson
  );
}
```

- [ ] **Step 3: Run TypeScript check**

Run: `cd .worktrees/paper-detail-page/app && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
cd .worktrees/paper-detail-page
git add app/src/lib/types.ts app/src/lib/api.ts
git commit -m "feat(frontend): add PublicationDetail types and usePublication hook"
```

---

### Task 3: Extract shared utilities from PublicationCard

**Files:**
- Create: `app/src/lib/publication-utils.ts`
- Modify: `app/src/components/PublicationCard.tsx`

- [ ] **Step 1: Create `publication-utils.ts`**

Extract `statusPillConfig` and `formatAuthor` into `app/src/lib/publication-utils.ts`:

```typescript
import type { PublicationStatus } from "./types";

export const statusPillConfig: Record<PublicationStatus, { label: string; className: string }> = {
  published: { label: "Published", className: "bg-teal-100 text-teal-700" },
  working_paper: { label: "Working Paper", className: "bg-blue-100 text-blue-700" },
  revise_and_resubmit: { label: "Revise & Resubmit", className: "bg-amber-100 text-amber-700" },
  reject_and_resubmit: { label: "Reject & Resubmit", className: "bg-rose-100 text-rose-700" },
  accepted: { label: "Accepted", className: "bg-emerald-100 text-emerald-700" },
};

export function formatAuthor(author: { id: number; first_name: string; last_name: string }) {
  const initial = author.first_name.charAt(0);
  return { display: `${initial}. ${author.last_name}`, id: author.id };
}
```

- [ ] **Step 2: Update `PublicationCard.tsx` to import from shared utils**

Replace the local `formatAuthor` function and `statusPillConfig` constant with imports:

```typescript
import { statusPillConfig, formatAuthor } from "@/lib/publication-utils";
```

Remove the local `formatAuthor` function (lines 7-10) and `statusPillConfig` constant (lines 12-18) from `PublicationCard.tsx`.

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `cd .worktrees/paper-detail-page/app && npx jest --testPathPattern=PublicationCard`
Expected: All existing `PublicationCard` tests pass

- [ ] **Step 4: Commit**

```bash
cd .worktrees/paper-detail-page
git add app/src/lib/publication-utils.ts app/src/components/PublicationCard.tsx
git commit -m "refactor: extract statusPillConfig and formatAuthor to shared utils"
```

---

### Task 4: Make PublicationCard clickable

**Files:**
- Modify: `app/src/components/PublicationCard.tsx`
- Modify: `app/src/components/__tests__/PublicationCard.test.tsx`

- [ ] **Step 1: Write failing test for card click navigation**

Add to `app/src/components/__tests__/PublicationCard.test.tsx`:

```typescript
import userEvent from "@testing-library/user-event";

// Mock next/navigation
const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));
```

Add a new test block:

```typescript
describe("PublicationCard navigation", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("navigates to paper detail on card click", async () => {
    const user = userEvent.setup();
    render(<PublicationCard publication={publication} />);
    const card = screen.getByText("Immigration and Wages: Evidence from Germany").closest("[data-testid='publication-card']")!;
    await user.click(card);
    expect(mockPush).toHaveBeenCalledWith("/papers/1");
  });

  it("does not navigate when clicking author link", async () => {
    const user = userEvent.setup();
    render(<PublicationCard publication={publication} />);
    const authorLink = screen.getByText(/M\. Steinhardt/);
    await user.click(authorLink);
    expect(mockPush).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/paper-detail-page/app && npx jest --testPathPattern=PublicationCard`
Expected: FAIL — `data-testid` not found, `useRouter` not imported

- [ ] **Step 3: Implement clickable card**

Update `PublicationCard.tsx`:

1. Add `useRouter` import:
```typescript
import { useRouter } from "next/navigation";
```

2. Inside the component, add router:
```typescript
const router = useRouter();
```

3. Update the outer `<div>` to:
```tsx
<div
  data-testid="publication-card"
  className="rounded-md bg-[var(--bg-card)] border border-[var(--border-light)] hover:border-[var(--border)] transition-colors duration-150 px-5 py-4 cursor-pointer"
  onClick={() => router.push(`/papers/${publication.id}`)}
>
```

4. Add `onClick={e => e.stopPropagation()}` to all inner interactive elements:
   - Each `<Link>` (author names) — wrap the `onClick`: `onClick={e => e.stopPropagation()}`
   - Each `<a>` tag (draft URL, DOI, paper links) — add `onClick={e => e.stopPropagation()}`
   - The abstract `<button>` — update to: `onClick={(e) => { e.stopPropagation(); setAbstractOpen((prev) => !prev); }}`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/paper-detail-page/app && npx jest --testPathPattern=PublicationCard`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd .worktrees/paper-detail-page
git add app/src/components/PublicationCard.tsx app/src/components/__tests__/PublicationCard.test.tsx
git commit -m "feat: make PublicationCard clickable with navigation to paper detail"
```

---

### Task 5: Create paper detail page

**Files:**
- Create: `app/src/app/papers/[id]/page.tsx`
- Create: `app/src/app/papers/[id]/PaperDetailContent.tsx`

- [ ] **Step 1: Write failing test for PaperDetailContent**

Create `app/src/app/papers/__tests__/PaperDetailContent.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import PaperDetailContent from "../[id]/PaperDetailContent";
import type { PublicationDetail } from "@/lib/types";

// Mock next/navigation
const mockBack = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ back: mockBack }),
}));

// Mock the usePublication hook
const mockPublication: PublicationDetail = {
  id: 1,
  title: "Trade and Wages: Evidence from Germany",
  authors: [
    { id: 10, first_name: "Max", last_name: "Steinhardt" },
    { id: 11, first_name: "Jane", last_name: "Doe" },
  ],
  year: "2024",
  venue: "Journal of Labor Economics",
  source_url: "https://example.com/pub",
  discovered_at: "2026-03-15T14:30:00Z",
  status: "published",
  abstract: "This paper studies the effects of immigration on wages.",
  draft_url: "https://ssrn.com/abstract=1",
  draft_url_status: "valid",
  draft_available: true,
  doi: "10.1257/aer.20181234",
  coauthors: [
    { display_name: "Max Steinhardt", openalex_author_id: "A111" },
    { display_name: "Jane Doe", openalex_author_id: "A222" },
  ],
  links: [
    { url: "https://ssrn.com/abstract=1", link_type: "ssrn" },
  ],
  feed_events: [
    { id: 5, event_type: "status_change", old_status: "working_paper", new_status: "published", created_at: "2026-03-20T12:00:00Z" },
    { id: 1, event_type: "new_paper", old_status: null, new_status: null, created_at: "2026-03-15T14:30:00Z" },
  ],
  history: [
    { status: "published", venue: "JLE", abstract: null, draft_url: "https://ssrn.com/abstract=1", draft_url_status: "valid", year: "2024", scraped_at: "2026-03-20T12:00:00Z", source_url: "https://example.com/pub" },
    { status: "working_paper", venue: null, abstract: null, draft_url: "https://ssrn.com/abstract=1", draft_url_status: "valid", year: "2024", scraped_at: "2026-03-15T14:30:00Z", source_url: "https://example.com/pub" },
  ],
  is_seed: false,
  title_hash: "abc123def456",
  openalex_id: "W12345",
};

jest.mock("@/lib/api", () => ({
  usePublication: () => ({
    data: mockPublication,
    error: undefined,
    isLoading: false,
  }),
}));

describe("PaperDetailContent", () => {
  it("renders the paper title", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText("Trade and Wages: Evidence from Germany")).toBeInTheDocument();
  });

  it("renders the abstract", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText(/effects of immigration on wages/)).toBeInTheDocument();
  });

  it("renders author links", () => {
    render(<PaperDetailContent id={1} />);
    const links = screen.getAllByRole("link");
    const authorLinks = links.filter(l => l.getAttribute("href")?.startsWith("/researchers/"));
    expect(authorLinks.length).toBeGreaterThanOrEqual(2);
  });

  it("renders status pill", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText("Published")).toBeInTheDocument();
  });

  it("renders DOI link", () => {
    render(<PaperDetailContent id={1} />);
    const doiLink = screen.getByText("DOI").closest("a");
    expect(doiLink).toHaveAttribute("href", "https://doi.org/10.1257/aer.20181234");
  });

  it("renders history timeline with feed events", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText(/Discovered/)).toBeInTheDocument();
    expect(screen.getByText(/Status changed/)).toBeInTheDocument();
  });

  it("renders back link", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText(/Back/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/paper-detail-page/app && npx jest --testPathPattern=PaperDetailContent`
Expected: FAIL — module not found

- [ ] **Step 3: Create `page.tsx`**

Create `app/src/app/papers/[id]/page.tsx`:

```tsx
import PaperDetailContent from "./PaperDetailContent";

export default async function PaperDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <PaperDetailContent id={Number(id)} />;
}
```

- [ ] **Step 4: Create `PaperDetailContent.tsx`**

Create `app/src/app/papers/[id]/PaperDetailContent.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { usePublication } from "@/lib/api";
import { statusPillConfig, formatAuthor } from "@/lib/publication-utils";
import type { FeedEvent, PaperSnapshot, PublicationStatus } from "@/lib/types";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function SnapshotDiff({ current, previous }: { current: PaperSnapshot; previous: PaperSnapshot }) {
  const changes: { field: string; from: string | null; to: string | null }[] = [];
  const fields: (keyof PaperSnapshot)[] = ["status", "venue", "year", "draft_url", "draft_url_status"];
  for (const field of fields) {
    if (current[field] !== previous[field]) {
      changes.push({ field, from: String(previous[field] ?? "none"), to: String(current[field] ?? "none") });
    }
  }
  if ((current.abstract ?? "") !== (previous.abstract ?? "")) {
    changes.push({
      field: "abstract",
      from: previous.abstract ? "present" : "none",
      to: current.abstract ? "present" : "none",
    });
  }
  if (changes.length === 0) return null;
  return (
    <ul className="mt-1 ml-4 text-xs text-[var(--text-muted)] space-y-0.5">
      {changes.map((c) => (
        <li key={c.field}>
          <span className="font-medium">{c.field}:</span> {c.from} → {c.to}
        </li>
      ))}
    </ul>
  );
}

function TimelineEntry({
  event,
  snapshots,
  index,
}: {
  event: FeedEvent;
  snapshots: PaperSnapshot[];
  index: number;
}) {
  const [diffOpen, setDiffOpen] = useState(false);

  const label =
    event.event_type === "new_paper"
      ? "Discovered"
      : `Status changed: ${
          event.old_status ? statusPillConfig[event.old_status as PublicationStatus]?.label ?? event.old_status : "?"
        } → ${
          event.new_status ? statusPillConfig[event.new_status as PublicationStatus]?.label ?? event.new_status : "?"
        }`;

  // Find matching snapshot pair for diffs (closest snapshot by date)
  const eventDate = new Date(event.created_at).getTime();
  const snapshotIdx = snapshots.findIndex(
    (s) => Math.abs(new Date(s.scraped_at).getTime() - eventDate) < 86400000
  );
  const hasSnapshotDiff =
    event.event_type === "status_change" && snapshotIdx >= 0 && snapshotIdx < snapshots.length - 1;

  return (
    <div className="relative pl-6 pb-4">
      {/* Timeline dot */}
      <div className="absolute left-0 top-1.5 w-2.5 h-2.5 rounded-full bg-[var(--border)] border-2 border-[var(--bg-card)]" />
      {/* Timeline line (except last item) */}
      {index > 0 && (
        <div className="absolute left-[4.5px] top-4 bottom-0 w-px bg-[var(--border-light)]" />
      )}
      <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
      <p className="text-xs text-[var(--text-muted)]">{formatDate(event.created_at)}</p>
      {hasSnapshotDiff && (
        <>
          <button
            onClick={() => setDiffOpen((prev) => !prev)}
            className="text-xs text-[var(--link)] hover:underline mt-0.5"
          >
            {diffOpen ? "Hide changes" : "Show changes"}
          </button>
          {diffOpen && (
            <SnapshotDiff
              current={snapshots[snapshotIdx]}
              previous={snapshots[snapshotIdx + 1]}
            />
          )}
        </>
      )}
    </div>
  );
}

export default function PaperDetailContent({ id }: { id: number }) {
  const router = useRouter();
  const { data: publication, error, isLoading } = usePublication(id);

  if (isLoading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-4 w-24 bg-[var(--border-light)] rounded" />
        <div className="h-8 w-3/4 bg-[var(--border-light)] rounded" />
        <div className="h-4 w-1/2 bg-[var(--border-light)] rounded" />
        <div className="h-32 bg-[var(--border-light)] rounded" />
      </div>
    );
  }

  if (error || !publication) {
    return (
      <div className="text-center py-12">
        <p className="text-[var(--text-muted)] mb-4">
          {error?.message || "Paper not found."}
        </p>
        <Link href="/" className="text-[var(--link)] hover:underline">
          ← Back to feed
        </Link>
      </div>
    );
  }

  const authors = publication.authors.map(formatAuthor);
  const venueYear = [publication.venue, publication.year].filter(Boolean).join(", ");

  // Sort events newest-first (API already does this, but be safe)
  const events = [...publication.feed_events].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  // Sort snapshots newest-first for diff pairing
  const snapshots = [...publication.history].sort(
    (a, b) => new Date(b.scraped_at).getTime() - new Date(a.scraped_at).getTime()
  );

  return (
    <div className="max-w-2xl mx-auto">
      {/* Back link */}
      <button
        onClick={() => router.back()}
        className="font-sans text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors mb-6"
      >
        ← Back
      </button>

      {/* Status pill */}
      {publication.status && (
        <span
          className={`inline-block text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 mb-3 ${statusPillConfig[publication.status].className}`}
        >
          {statusPillConfig[publication.status].label}
        </span>
      )}

      {/* Title */}
      <h1 className="font-serif text-2xl font-semibold text-[var(--text-primary)] leading-snug mb-2">
        {publication.title}
      </h1>

      {/* Authors */}
      <p className="font-sans text-sm font-medium mb-1">
        {authors.map((a, i) => (
          <span key={a.id}>
            {i > 0 && ", "}
            <Link
              href={`/researchers/${a.id}`}
              className="text-[var(--link)] hover:underline"
            >
              {a.display}
            </Link>
          </span>
        ))}
      </p>

      {/* Venue + Year */}
      {venueYear && (
        <p className="font-sans text-sm italic text-[var(--text-muted)] mb-4">
          {venueYear}
        </p>
      )}

      {/* Abstract */}
      {publication.abstract && (
        <div className="mb-4">
          <h2 className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] mb-1.5">
            Abstract
          </h2>
          <p className="text-sm text-[var(--text-secondary)] leading-relaxed bg-[#faf8f4] border-l-2 border-[var(--border-light)] rounded-r-md p-3">
            {publication.abstract}
          </p>
        </div>
      )}

      {/* Links bar */}
      <div className="font-sans flex items-center gap-2.5 flex-wrap mb-4">
        {publication.doi && (
          <a
            href={`https://doi.org/${publication.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-indigo-100 text-indigo-700 hover:bg-indigo-200 transition-colors"
          >
            DOI
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        )}
        {publication.draft_available && publication.draft_url && (
          <a
            href={publication.draft_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-[#c2594b]/10 text-[#c2594b] hover:bg-[#c2594b]/20 transition-colors"
          >
            Draft
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
            </svg>
          </a>
        )}
        {publication.links?.map((link) => (
          <a
            key={link.url}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-violet-50 text-violet-700 hover:bg-violet-100 transition-colors"
          >
            {link.link_type?.toUpperCase() || "LINK"}
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
            </svg>
          </a>
        ))}
      </div>

      {/* All Authors (OpenAlex) */}
      {publication.coauthors && publication.coauthors.length > 0 && (
        <div className="mb-6">
          <h2 className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] mb-1.5">
            All Authors
          </h2>
          <p className="font-sans text-sm text-[var(--text-secondary)]">
            {publication.coauthors.map((ca) => ca.display_name).join(", ")}
          </p>
        </div>
      )}

      {/* History Timeline */}
      {events.length > 0 && (
        <div className="mb-6">
          <h2 className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">
            History
          </h2>
          <div className="border-l-2 border-[var(--border-light)] ml-1">
            {events.map((event, i) => (
              <TimelineEntry key={event.id} event={event} snapshots={snapshots} index={i} />
            ))}
          </div>
        </div>
      )}

      {/* Technical Details (dev only) */}
      {process.env.NODE_ENV === "development" && (
        <details className="mb-6">
          <summary className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] cursor-pointer hover:text-[var(--text-secondary)]">
            Technical Details
          </summary>
          <dl className="mt-2 font-sans text-xs text-[var(--text-muted)] space-y-1">
            {publication.openalex_id && (
              <>
                <dt className="font-medium inline">OpenAlex ID: </dt>
                <dd className="inline">
                  <a
                    href={`https://openalex.org/works/${publication.openalex_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[var(--link)] hover:underline"
                  >
                    {publication.openalex_id}
                  </a>
                </dd>
                <br />
              </>
            )}
            <dt className="font-medium inline">Source URL: </dt>
            <dd className="inline">{publication.source_url || "—"}</dd>
            <br />
            <dt className="font-medium inline">Discovered: </dt>
            <dd className="inline">{formatDate(publication.discovered_at)}</dd>
            <br />
            <dt className="font-medium inline">Draft URL status: </dt>
            <dd className="inline">{publication.draft_url_status || "—"}</dd>
            <br />
            <dt className="font-medium inline">Seed paper: </dt>
            <dd className="inline">{publication.is_seed ? "Yes" : "No"}</dd>
            <br />
            <dt className="font-medium inline">Title hash: </dt>
            <dd className="inline font-mono">{publication.title_hash}</dd>
          </dl>
        </details>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd .worktrees/paper-detail-page/app && npx jest --testPathPattern=PaperDetailContent`
Expected: All tests PASS

- [ ] **Step 6: Run full frontend test suite**

Run: `cd .worktrees/paper-detail-page/app && npx jest`
Expected: All 71+ tests pass (no regressions)

- [ ] **Step 7: Commit**

```bash
cd .worktrees/paper-detail-page
git add app/src/app/papers/
git commit -m "feat: add paper detail page with metadata and history timeline"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full Python test suite**

Run: `cd .worktrees/paper-detail-page && poetry run pytest`
Expected: 423+ passed (5 pre-existing failures in `test_api_search.py` only)

- [ ] **Step 2: Run full frontend test suite**

Run: `cd .worktrees/paper-detail-page/app && npx jest`
Expected: All tests pass

- [ ] **Step 3: Run TypeScript check**

Run: `cd .worktrees/paper-detail-page/app && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Verify dev server renders page (manual)**

Run: `cd .worktrees/paper-detail-page && make dev`
Navigate to `http://localhost:3000`, click a paper card, verify the detail page renders.
