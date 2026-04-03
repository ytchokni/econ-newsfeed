# Admin Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a password-protected admin dashboard at `/admin` with 6 tabs showing system health, content stats, data quality, LLM costs, scrape history, and feed activity.

**Architecture:** Single FastAPI endpoint (`GET /api/admin/dashboard`) returns all metrics via aggregation queries in `database/admin.py`. Next.js API routes handle auth (password → cookie) and proxy to FastAPI. Client-side tabbed dashboard uses SWR with 60s auto-refresh.

**Tech Stack:** Python/FastAPI (backend), Next.js/React/Tailwind (frontend), SWR (data fetching), HMAC (session tokens)

**Styling:** Use the `frontend-design` skill when implementing all frontend components. The admin dashboard should use a dark theme distinct from the main app's light theme.

---

### Task 1: Backend — `database/admin.py`

**Files:**
- Create: `database/admin.py`
- Test: `tests/test_admin_dashboard.py`

- [ ] **Step 1: Write the failing test for `get_admin_dashboard_stats()`**

Create `tests/test_admin_dashboard.py`:

```python
"""Tests for admin dashboard stats queries."""
import os

os.environ.setdefault("CONTENT_MAX_CHARS", "20000")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, MagicMock
from database.admin import get_admin_dashboard_stats


def test_get_admin_dashboard_stats_returns_all_sections():
    """Stats response contains all 6 dashboard sections."""
    mock_fetch_all = MagicMock(return_value=[])
    mock_fetch_one = MagicMock(return_value={
        "total_papers": 0,
        "total_researchers": 0,
        "total_urls": 0,
        "papers_with_abstract": 0,
        "papers_with_doi": 0,
        "papers_with_openalex": 0,
        "papers_with_draft_url": 0,
        "draft_url_valid": 0,
        "researchers_with_description": 0,
        "researchers_with_jel": 0,
        "researchers_with_openalex_id": 0,
        "total_cost_usd": 0,
        "total_tokens": 0,
        "total_scrapes": 0,
        "total_pubs_extracted": 0,
    })

    with patch("database.admin.fetch_all", mock_fetch_all), \
         patch("database.admin.fetch_one", mock_fetch_one):
        result = get_admin_dashboard_stats()

    assert "health" in result
    assert "content" in result
    assert "quality" in result
    assert "costs" in result
    assert "scrapes" in result
    assert "activity" in result

    # Health section
    assert "last_scrape" in result["health"]
    assert "scrape_in_progress" in result["health"]
    assert "total_researcher_urls" in result["health"]
    assert "urls_by_page_type" in result["health"]

    # Content section
    assert "total_papers" in result["content"]
    assert "total_researchers" in result["content"]
    assert "papers_by_status" in result["content"]
    assert "papers_by_year" in result["content"]
    assert "researchers_by_position" in result["content"]

    # Quality section
    assert "papers_with_abstract" in result["quality"]
    assert "papers_with_doi" in result["quality"]

    # Costs section
    assert "total_cost_usd" in result["costs"]
    assert "by_call_type" in result["costs"]
    assert "by_model" in result["costs"]
    assert "last_30_days" in result["costs"]

    # Scrapes section
    assert "recent" in result["scrapes"]
    assert "totals" in result["scrapes"]

    # Activity section
    assert "events_last_7d" in result["activity"]
    assert "events_last_30d" in result["activity"]
    assert "recent_events" in result["activity"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_admin_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'database.admin'`

- [ ] **Step 3: Implement `database/admin.py`**

Create `database/admin.py`:

```python
"""Admin dashboard aggregation queries."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from database.connection import fetch_all, fetch_one

logger = logging.getLogger(__name__)


def _iso_z(dt: datetime | None) -> str | None:
    """Format a datetime as ISO 8601 with trailing Z."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _get_health_stats() -> dict:
    """Last scrape info, next run, URL counts."""
    import scheduler

    last_scrape_row = fetch_one(
        """SELECT started_at, status, urls_checked, urls_changed,
                  pubs_extracted, finished_at
           FROM scrape_log ORDER BY id DESC LIMIT 1"""
    )

    last_scrape = None
    next_scrape_at = None
    if last_scrape_row:
        started = last_scrape_row["started_at"]
        finished = last_scrape_row["finished_at"]
        duration = None
        if started and finished:
            duration = int((finished - started).total_seconds())
        last_scrape = {
            "started_at": _iso_z(started),
            "status": last_scrape_row["status"],
            "urls_checked": last_scrape_row["urls_checked"] or 0,
            "urls_changed": last_scrape_row["urls_changed"] or 0,
            "pubs_extracted": last_scrape_row["pubs_extracted"] or 0,
            "duration_seconds": duration,
        }
        if started:
            next_scrape_at = _iso_z(
                started + timedelta(hours=scheduler.SCRAPE_INTERVAL_HOURS)
            )

    scrape_in_progress = scheduler.is_scrape_running()

    url_row = fetch_one("SELECT COUNT(*) AS total FROM researcher_urls")
    total_urls = url_row["total"] if url_row else 0

    url_types = fetch_all(
        "SELECT page_type, COUNT(*) AS cnt FROM researcher_urls GROUP BY page_type"
    )
    urls_by_page_type = {r["page_type"]: r["cnt"] for r in url_types}

    return {
        "last_scrape": last_scrape,
        "next_scrape_at": next_scrape_at,
        "scrape_in_progress": scrape_in_progress,
        "total_researcher_urls": total_urls,
        "urls_by_page_type": urls_by_page_type,
    }


def _get_content_stats() -> dict:
    """Paper and researcher counts and breakdowns."""
    counts = fetch_one(
        "SELECT "
        "(SELECT COUNT(*) FROM papers) AS total_papers, "
        "(SELECT COUNT(*) FROM researchers) AS total_researchers"
    )

    by_status = fetch_all(
        "SELECT status, COUNT(*) AS cnt FROM papers GROUP BY status"
    )
    papers_by_status = {r["status"]: r["cnt"] for r in by_status}

    by_year = fetch_all(
        "SELECT year, COUNT(*) AS count FROM papers "
        "WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC"
    )
    papers_by_year = [{"year": r["year"], "count": r["count"]} for r in by_year]

    by_position = fetch_all(
        "SELECT position, COUNT(*) AS cnt FROM researchers "
        "WHERE position IS NOT NULL GROUP BY position ORDER BY cnt DESC"
    )
    researchers_by_position = {r["position"]: r["cnt"] for r in by_position}

    return {
        "total_papers": counts["total_papers"] if counts else 0,
        "total_researchers": counts["total_researchers"] if counts else 0,
        "papers_by_status": papers_by_status,
        "papers_by_year": papers_by_year,
        "researchers_by_position": researchers_by_position,
    }


def _get_quality_stats() -> dict:
    """Data coverage metrics."""
    row = fetch_one(
        "SELECT "
        "(SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND abstract != '') AS papers_with_abstract, "
        "(SELECT COUNT(*) FROM papers WHERE doi IS NOT NULL) AS papers_with_doi, "
        "(SELECT COUNT(*) FROM papers WHERE openalex_id IS NOT NULL) AS papers_with_openalex, "
        "(SELECT COUNT(*) FROM papers WHERE draft_url IS NOT NULL AND draft_url != '') AS papers_with_draft_url, "
        "(SELECT COUNT(*) FROM papers WHERE draft_url_status = 'valid') AS draft_url_valid, "
        "(SELECT COUNT(*) FROM researchers WHERE description IS NOT NULL AND description != '') AS researchers_with_description, "
        "(SELECT COUNT(DISTINCT researcher_id) FROM researcher_jel_codes) AS researchers_with_jel, "
        "(SELECT COUNT(*) FROM researchers WHERE openalex_author_id IS NOT NULL) AS researchers_with_openalex_id"
    )
    if not row:
        return {k: 0 for k in [
            "papers_with_abstract", "papers_with_doi", "papers_with_openalex",
            "papers_with_draft_url", "draft_url_valid",
            "researchers_with_description", "researchers_with_jel",
            "researchers_with_openalex_id",
        ]}
    return dict(row)


def _get_cost_stats() -> dict:
    """LLM usage and cost breakdowns."""
    totals = fetch_one(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total_cost_usd, "
        "COALESCE(SUM(total_tokens), 0) AS total_tokens "
        "FROM llm_usage"
    )

    by_call_type = fetch_all(
        "SELECT call_type, "
        "COALESCE(SUM(estimated_cost_usd), 0) AS cost, "
        "COALESCE(SUM(total_tokens), 0) AS tokens, "
        "COUNT(*) AS count "
        "FROM llm_usage GROUP BY call_type ORDER BY cost DESC"
    )

    by_model = fetch_all(
        "SELECT model, "
        "COALESCE(SUM(estimated_cost_usd), 0) AS cost, "
        "COALESCE(SUM(total_tokens), 0) AS tokens "
        "FROM llm_usage GROUP BY model ORDER BY cost DESC"
    )

    batch_totals = fetch_one(
        "SELECT "
        "COALESCE(SUM(CASE WHEN is_batch = 1 THEN estimated_cost_usd ELSE 0 END), 0) AS batch_cost, "
        "COALESCE(SUM(CASE WHEN is_batch = 0 THEN estimated_cost_usd ELSE 0 END), 0) AS realtime_cost "
        "FROM llm_usage"
    )

    daily = fetch_all(
        "SELECT DATE(called_at) AS date, "
        "COALESCE(SUM(estimated_cost_usd), 0) AS cost, "
        "COALESCE(SUM(total_tokens), 0) AS tokens "
        "FROM llm_usage "
        "WHERE called_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) "
        "GROUP BY DATE(called_at) ORDER BY date"
    )
    last_30_days = [
        {"date": str(r["date"]), "cost": float(r["cost"]), "tokens": int(r["tokens"])}
        for r in daily
    ]

    return {
        "total_cost_usd": float(totals["total_cost_usd"]) if totals else 0,
        "total_tokens": int(totals["total_tokens"]) if totals else 0,
        "by_call_type": [
            {"call_type": r["call_type"], "cost": float(r["cost"]),
             "tokens": int(r["tokens"]), "count": r["count"]}
            for r in by_call_type
        ],
        "by_model": [
            {"model": r["model"], "cost": float(r["cost"]), "tokens": int(r["tokens"])}
            for r in by_model
        ],
        "batch_vs_realtime": {
            "batch_cost": float(batch_totals["batch_cost"]) if batch_totals else 0,
            "realtime_cost": float(batch_totals["realtime_cost"]) if batch_totals else 0,
        },
        "last_30_days": last_30_days,
    }


def _get_scrape_stats() -> dict:
    """Recent scrape history."""
    recent = fetch_all(
        """SELECT started_at, status, urls_checked, urls_changed,
                  pubs_extracted, finished_at,
                  COALESCE((SELECT SUM(total_tokens) FROM llm_usage
                            WHERE scrape_log_id = s.id), 0) AS tokens_used
           FROM scrape_log s ORDER BY id DESC LIMIT 30"""
    )
    recent_list = []
    for r in recent:
        started = r["started_at"]
        finished = r["finished_at"]
        duration = None
        if started and finished:
            duration = int((finished - started).total_seconds())
        recent_list.append({
            "started_at": _iso_z(started),
            "status": r["status"],
            "urls_checked": r["urls_checked"] or 0,
            "urls_changed": r["urls_changed"] or 0,
            "pubs_extracted": r["pubs_extracted"] or 0,
            "tokens_used": int(r["tokens_used"]),
            "duration_seconds": duration,
        })

    totals = fetch_one(
        "SELECT COUNT(*) AS total_scrapes, "
        "COALESCE(SUM(pubs_extracted), 0) AS total_pubs_extracted "
        "FROM scrape_log"
    )

    return {
        "recent": recent_list,
        "totals": {
            "total_scrapes": totals["total_scrapes"] if totals else 0,
            "total_pubs_extracted": int(totals["total_pubs_extracted"]) if totals else 0,
        },
    }


def _get_activity_stats() -> dict:
    """Feed event summaries."""
    def _event_counts(days: int) -> dict:
        rows = fetch_all(
            "SELECT event_type, COUNT(*) AS cnt FROM feed_events "
            "WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
            "GROUP BY event_type",
            (days,),
        )
        return {r["event_type"]: r["cnt"] for r in rows}

    recent = fetch_all(
        """SELECT fe.event_type, p.title AS paper_title,
                  fe.created_at, fe.old_status, fe.new_status
           FROM feed_events fe
           JOIN papers p ON fe.paper_id = p.id
           ORDER BY fe.created_at DESC LIMIT 50"""
    )
    recent_events = []
    for r in recent:
        details = None
        if r["event_type"] == "status_change" and r["old_status"] and r["new_status"]:
            details = f"{r['old_status']} → {r['new_status']}"
        recent_events.append({
            "event_type": r["event_type"],
            "paper_title": r["paper_title"],
            "created_at": _iso_z(r["created_at"]),
            "details": details,
        })

    return {
        "events_last_7d": _event_counts(7),
        "events_last_30d": _event_counts(30),
        "recent_events": recent_events,
    }


def get_admin_dashboard_stats() -> dict:
    """Aggregate all dashboard metrics into a single response dict."""
    return {
        "health": _get_health_stats(),
        "content": _get_content_stats(),
        "quality": _get_quality_stats(),
        "costs": _get_cost_stats(),
        "scrapes": _get_scrape_stats(),
        "activity": _get_activity_stats(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_admin_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/admin.py tests/test_admin_dashboard.py
git commit -m "feat: add admin dashboard aggregation queries"
```

---

### Task 2: Wire up `database/__init__.py` and add FastAPI endpoint

**Files:**
- Modify: `database/__init__.py`
- Modify: `api.py`
- Modify: `.dockerignore`
- Test: `tests/test_admin_dashboard.py`

- [ ] **Step 1: Write the failing test for the API endpoint**

Append to `tests/test_admin_dashboard.py`:

```python
def test_admin_dashboard_endpoint_requires_api_key(client):
    """GET /api/admin/dashboard returns 401 without API key."""
    resp = client.get("/api/admin/dashboard")
    assert resp.status_code == 401


def test_admin_dashboard_endpoint_returns_data(client):
    """GET /api/admin/dashboard returns stats with valid API key."""
    mock_stats = {
        "health": {"last_scrape": None, "next_scrape_at": None,
                   "scrape_in_progress": False, "total_researcher_urls": 0,
                   "urls_by_page_type": {}},
        "content": {"total_papers": 10, "total_researchers": 5,
                    "papers_by_status": {}, "papers_by_year": [],
                    "researchers_by_position": {}},
        "quality": {"papers_with_abstract": 0, "papers_with_doi": 0,
                    "papers_with_openalex": 0, "papers_with_draft_url": 0,
                    "draft_url_valid": 0, "researchers_with_description": 0,
                    "researchers_with_jel": 0, "researchers_with_openalex_id": 0},
        "costs": {"total_cost_usd": 0, "total_tokens": 0, "by_call_type": [],
                  "by_model": [], "batch_vs_realtime": {"batch_cost": 0, "realtime_cost": 0},
                  "last_30_days": []},
        "scrapes": {"recent": [], "totals": {"total_scrapes": 0, "total_pubs_extracted": 0}},
        "activity": {"events_last_7d": {}, "events_last_30d": {}, "recent_events": []},
    }
    with patch("api.Database.get_admin_dashboard_stats", return_value=mock_stats):
        resp = client.get(
            "/api/admin/dashboard",
            headers={"X-API-Key": "test-secret-key-for-ci-runs"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "content" in data
    assert data["content"]["total_papers"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_admin_dashboard.py::test_admin_dashboard_endpoint_requires_api_key -v`
Expected: FAIL — 404 (endpoint doesn't exist yet)

- [ ] **Step 3: Wire up `database/__init__.py`**

Add import at the top of `database/__init__.py` (after the `jel` imports):

```python
from database.admin import get_admin_dashboard_stats as _get_admin_dashboard_stats
```

Add to the `Database` class body (after `sync_researcher_fields_from_jel`):

```python
    # Admin
    get_admin_dashboard_stats = staticmethod(_get_admin_dashboard_stats)
```

- [ ] **Step 4: Add endpoint to `api.py`**

Add before the `# Publication endpoints` comment block (around line 510):

```python
@app.get("/api/admin/dashboard")
def admin_dashboard(request: Request):
    """Admin dashboard metrics — all stats in one response."""
    _require_api_key(request)
    return Database.get_admin_dashboard_stats()
```

- [ ] **Step 5: Add `database/admin.py` to `.dockerignore`**

The `.dockerignore` already includes `!database/` which allows the entire directory. No change needed — `database/admin.py` is already included by the existing `!database/` line.

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/test_admin_dashboard.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add database/__init__.py api.py tests/test_admin_dashboard.py
git commit -m "feat: add GET /api/admin/dashboard endpoint"
```

---

### Task 3: Next.js API routes — login, logout, dashboard proxy

**Files:**
- Create: `app/src/app/api/admin/login/route.ts`
- Create: `app/src/app/api/admin/logout/route.ts`
- Create: `app/src/app/api/admin/dashboard/route.ts`
- Modify: `app/src/lib/api.ts` (add `useAdminDashboard` hook)

**Important context:** The Next.js app rewrites `/api/:path*` to FastAPI in `next.config.mjs`. However, Next.js local API routes take precedence over rewrites, so these routes at `/api/admin/*` will be served by Next.js directly without being proxied to FastAPI.

- [ ] **Step 1: Create login route**

Create `app/src/app/api/admin/login/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";
import { createHmac, timingSafeEqual } from "crypto";

const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const COOKIE_NAME = "admin_session";
const COOKIE_MAX_AGE = 7 * 24 * 60 * 60; // 7 days in seconds

function signToken(timestamp: number): string {
  return createHmac("sha256", ADMIN_PASSWORD)
    .update(String(timestamp))
    .digest("hex");
}

export async function POST(request: NextRequest) {
  if (!ADMIN_PASSWORD) {
    return NextResponse.json(
      { error: "Admin not configured" },
      { status: 500 }
    );
  }

  const body = await request.json().catch(() => null);
  const password = body?.password || "";

  if (typeof password !== "string" || password.length === 0) {
    return NextResponse.json({ error: "Password required" }, { status: 400 });
  }

  // Constant-time comparison
  const a = Buffer.from(password);
  const b = Buffer.from(ADMIN_PASSWORD);
  const valid =
    a.length === b.length && timingSafeEqual(a, b);

  if (!valid) {
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  const timestamp = Date.now();
  const token = `${timestamp}.${signToken(timestamp)}`;

  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    maxAge: COOKIE_MAX_AGE,
    path: "/",
  });
  return response;
}
```

- [ ] **Step 2: Create logout route**

Create `app/src/app/api/admin/logout/route.ts`:

```typescript
import { NextResponse } from "next/server";

export async function POST() {
  const response = NextResponse.json({ ok: true });
  response.cookies.set("admin_session", "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    maxAge: 0,
    path: "/",
  });
  return response;
}
```

- [ ] **Step 3: Create dashboard proxy route**

Create `app/src/app/api/admin/dashboard/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server";
import { createHmac } from "crypto";

const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const SCRAPE_API_KEY = process.env.SCRAPE_API_KEY || "";
const API_INTERNAL_URL =
  process.env.API_INTERNAL_URL || "http://localhost:8000";
const COOKIE_NAME = "admin_session";
const COOKIE_MAX_AGE = 7 * 24 * 60 * 60; // 7 days in seconds

function verifyToken(token: string): boolean {
  const [timestampStr, signature] = token.split(".");
  if (!timestampStr || !signature) return false;

  const timestamp = Number(timestampStr);
  if (isNaN(timestamp)) return false;

  // Check expiry
  const ageSeconds = (Date.now() - timestamp) / 1000;
  if (ageSeconds > COOKIE_MAX_AGE) return false;

  const expected = createHmac("sha256", ADMIN_PASSWORD)
    .update(timestampStr)
    .digest("hex");
  return signature === expected;
}

export async function GET(request: NextRequest) {
  if (!ADMIN_PASSWORD) {
    return NextResponse.json({ error: "Not configured" }, { status: 500 });
  }

  const token = request.cookies.get(COOKIE_NAME)?.value || "";
  if (!token || !verifyToken(token)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const resp = await fetch(`${API_INTERNAL_URL}/api/admin/dashboard`, {
    headers: { "X-API-Key": SCRAPE_API_KEY },
  });

  if (!resp.ok) {
    return NextResponse.json(
      { error: "Backend error" },
      { status: resp.status }
    );
  }

  const data = await resp.json();
  return NextResponse.json(data);
}
```

- [ ] **Step 4: Add `useAdminDashboard` hook to `app/src/lib/api.ts`**

Add at the end of `app/src/lib/api.ts`:

```typescript
export interface AdminDashboardData {
  health: {
    last_scrape: {
      started_at: string;
      status: string;
      urls_checked: number;
      urls_changed: number;
      pubs_extracted: number;
      duration_seconds: number | null;
    } | null;
    next_scrape_at: string | null;
    scrape_in_progress: boolean;
    total_researcher_urls: number;
    urls_by_page_type: Record<string, number>;
  };
  content: {
    total_papers: number;
    total_researchers: number;
    papers_by_status: Record<string, number>;
    papers_by_year: { year: string; count: number }[];
    researchers_by_position: Record<string, number>;
  };
  quality: {
    papers_with_abstract: number;
    papers_with_doi: number;
    papers_with_openalex: number;
    papers_with_draft_url: number;
    draft_url_valid: number;
    researchers_with_description: number;
    researchers_with_jel: number;
    researchers_with_openalex_id: number;
  };
  costs: {
    total_cost_usd: number;
    total_tokens: number;
    by_call_type: {
      call_type: string;
      cost: number;
      tokens: number;
      count: number;
    }[];
    by_model: { model: string; cost: number; tokens: number }[];
    batch_vs_realtime: { batch_cost: number; realtime_cost: number };
    last_30_days: { date: string; cost: number; tokens: number }[];
  };
  scrapes: {
    recent: {
      started_at: string;
      status: string;
      urls_checked: number;
      urls_changed: number;
      pubs_extracted: number;
      tokens_used: number;
      duration_seconds: number | null;
    }[];
    totals: { total_scrapes: number; total_pubs_extracted: number };
  };
  activity: {
    events_last_7d: Record<string, number>;
    events_last_30d: Record<string, number>;
    recent_events: {
      event_type: string;
      paper_title: string;
      created_at: string;
      details: string | null;
    }[];
  };
}

async function fetchJsonWithAuth<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (res.status === 401) {
    throw new Error("UNAUTHORIZED");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function useAdminDashboard() {
  return useSWR<AdminDashboardData>(
    "/api/admin/dashboard",
    fetchJsonWithAuth,
    { refreshInterval: 60000 }
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add app/src/app/api/admin/login/route.ts \
      app/src/app/api/admin/logout/route.ts \
      app/src/app/api/admin/dashboard/route.ts \
      app/src/lib/api.ts
git commit -m "feat: add Next.js admin API routes (login, logout, dashboard proxy)"
```

---

### Task 4: Frontend — Login form and dashboard shell

**Files:**
- Create: `app/src/app/admin/page.tsx`
- Create: `app/src/app/admin/AdminDashboard.tsx`
- Create: `app/src/app/admin/LoginForm.tsx`

**Note:** Use the `frontend-design` skill for styling all components. The admin dashboard should use a dark theme.

- [ ] **Step 1: Create the server component page**

Create `app/src/app/admin/page.tsx`:

```tsx
import { Metadata } from "next";
import AdminDashboard from "./AdminDashboard";

export const metadata: Metadata = {
  title: "Admin Dashboard — Econ Newsfeed",
  robots: "noindex, nofollow",
};

export default function AdminPage() {
  return <AdminDashboard />;
}
```

- [ ] **Step 2: Create the login form**

Create `app/src/app/admin/LoginForm.tsx`:

```tsx
"use client";

import { useState } from "react";

interface LoginFormProps {
  onSuccess: () => void;
}

export default function LoginForm({ onSuccess }: LoginFormProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (!res.ok) {
        setError("Invalid password");
        return;
      }
      onSuccess();
    } catch {
      setError("Connection error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-8"
      >
        <h1 className="text-xl font-semibold text-zinc-100 mb-6 font-[family-name:var(--font-dm-sans)]">
          Admin Dashboard
        </h1>
        <label className="block text-sm text-zinc-400 mb-2 font-[family-name:var(--font-dm-sans)]">
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full px-3 py-2 bg-[#0f1117] border border-[#2a2d3a] rounded text-zinc-100 text-sm focus:outline-none focus:border-[#4a9eff] font-[family-name:var(--font-dm-sans)]"
          autoFocus
        />
        {error && (
          <p className="mt-2 text-sm text-red-400 font-[family-name:var(--font-dm-sans)]">{error}</p>
        )}
        <button
          type="submit"
          disabled={loading || !password}
          className="mt-4 w-full py-2 bg-[#4a9eff] text-white text-sm font-medium rounded hover:bg-[#3a8eef] disabled:opacity-50 disabled:cursor-not-allowed font-[family-name:var(--font-dm-sans)]"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 3: Create the dashboard shell with tabs**

Create `app/src/app/admin/AdminDashboard.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useAdminDashboard } from "@/lib/api";
import LoginForm from "./LoginForm";
import HealthTab from "./tabs/HealthTab";
import ContentTab from "./tabs/ContentTab";
import QualityTab from "./tabs/QualityTab";
import CostsTab from "./tabs/CostsTab";
import ScrapesTab from "./tabs/ScrapesTab";
import ActivityTab from "./tabs/ActivityTab";

const TABS = [
  { id: "health", label: "Health" },
  { id: "content", label: "Content" },
  { id: "quality", label: "Quality" },
  { id: "costs", label: "Costs" },
  { id: "scrapes", label: "Scrapes" },
  { id: "activity", label: "Activity" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function AdminDashboard() {
  const [activeTab, setActiveTab] = useState<TabId>("health");
  const { data, error, isLoading, mutate } = useAdminDashboard();
  const [authed, setAuthed] = useState<boolean | null>(null);

  // First load — check if we're authed
  if (authed === null) {
    if (isLoading) {
      return (
        <div className="min-h-screen bg-[#0f1117] flex items-center justify-center">
          <p className="text-zinc-500 font-[family-name:var(--font-dm-sans)]">Loading…</p>
        </div>
      );
    }
    if (error?.message === "UNAUTHORIZED") {
      return <LoginForm onSuccess={() => { setAuthed(true); mutate(); }} />;
    }
    if (data) {
      // We're authed — fall through to dashboard
      setAuthed(true);
    }
  }

  if (authed === false || error?.message === "UNAUTHORIZED") {
    return <LoginForm onSuccess={() => { setAuthed(true); mutate(); }} />;
  }

  async function handleLogout() {
    await fetch("/api/admin/logout", { method: "POST" });
    setAuthed(false);
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-zinc-100 font-[family-name:var(--font-dm-sans)]">
      {/* Header */}
      <div className="border-b border-[#2a2d3a] px-6 py-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Admin Dashboard</h1>
        <button
          onClick={handleLogout}
          className="text-sm text-zinc-500 hover:text-zinc-300"
        >
          Sign out
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-[#2a2d3a] px-6">
        <nav className="flex gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.id
                  ? "border-[#4a9eff] text-zinc-100"
                  : "border-transparent text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      <div className="p-6 max-w-6xl mx-auto">
        {isLoading && !data ? (
          <p className="text-zinc-500">Loading…</p>
        ) : error && error.message !== "UNAUTHORIZED" ? (
          <p className="text-red-400">Error loading dashboard data</p>
        ) : data ? (
          <>
            {activeTab === "health" && <HealthTab data={data.health} />}
            {activeTab === "content" && <ContentTab data={data.content} />}
            {activeTab === "quality" && (
              <QualityTab data={data.quality} totalPapers={data.content.total_papers} totalResearchers={data.content.total_researchers} />
            )}
            {activeTab === "costs" && <CostsTab data={data.costs} />}
            {activeTab === "scrapes" && <ScrapesTab data={data.scrapes} />}
            {activeTab === "activity" && <ActivityTab data={data.activity} />}
          </>
        ) : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add app/src/app/admin/page.tsx \
      app/src/app/admin/LoginForm.tsx \
      app/src/app/admin/AdminDashboard.tsx
git commit -m "feat: add admin dashboard shell with login and tab navigation"
```

---

### Task 5: Frontend — HealthTab

**Files:**
- Create: `app/src/app/admin/tabs/HealthTab.tsx`

- [ ] **Step 1: Create HealthTab component**

Create `app/src/app/admin/tabs/HealthTab.tsx`:

```tsx
import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["health"];
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: "bg-emerald-900/50 text-emerald-400 border-emerald-800",
    running: "bg-blue-900/50 text-blue-400 border-blue-800",
    failed: "bg-red-900/50 text-red-400 border-red-800",
  };
  const cls = colors[status] || "bg-zinc-800 text-zinc-400 border-zinc-700";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {status}
    </span>
  );
}

function formatRelativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function HealthTab({ data }: Props) {
  const { last_scrape, next_scrape_at, scrape_in_progress, total_researcher_urls, urls_by_page_type } = data;

  return (
    <div className="space-y-6">
      {/* Scrape status */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Scrape Status</h2>
        {scrape_in_progress && (
          <div className="mb-4 px-3 py-2 bg-blue-900/30 border border-blue-800 rounded text-sm text-blue-300">
            Scrape in progress…
          </div>
        )}
        {last_scrape ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-zinc-500 mb-1">Status</p>
              <StatusBadge status={last_scrape.status} />
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Last Run</p>
              <p className="text-sm text-zinc-200">{formatRelativeTime(last_scrape.started_at)}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">URLs Checked</p>
              <p className="text-sm text-zinc-200">{last_scrape.urls_checked}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">URLs Changed</p>
              <p className="text-sm text-zinc-200">{last_scrape.urls_changed}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Papers Extracted</p>
              <p className="text-sm text-zinc-200">{last_scrape.pubs_extracted}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Duration</p>
              <p className="text-sm text-zinc-200">
                {last_scrape.duration_seconds != null ? `${Math.floor(last_scrape.duration_seconds / 60)}m ${last_scrape.duration_seconds % 60}s` : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Next Run</p>
              <p className="text-sm text-zinc-200">
                {next_scrape_at ? formatRelativeTime(next_scrape_at) : "—"}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-zinc-500">No scrapes recorded yet</p>
        )}
      </div>

      {/* URL counts */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">
          Researcher URLs <span className="text-zinc-600">({total_researcher_urls})</span>
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {Object.entries(urls_by_page_type).map(([type, count]) => (
            <div key={type} className="flex items-center justify-between px-3 py-2 bg-[#0f1117] rounded border border-[#2a2d3a]">
              <span className="text-sm text-zinc-300">{type.replace(/_/g, " ")}</span>
              <span className="text-sm font-medium text-zinc-100">{count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add app/src/app/admin/tabs/HealthTab.tsx
git commit -m "feat: add HealthTab component"
```

---

### Task 6: Frontend — ContentTab

**Files:**
- Create: `app/src/app/admin/tabs/ContentTab.tsx`

- [ ] **Step 1: Create ContentTab component**

Create `app/src/app/admin/tabs/ContentTab.tsx`:

```tsx
import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["content"];
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-2xl font-semibold text-zinc-100">{value.toLocaleString()}</p>
    </div>
  );
}

export default function ContentTab({ data }: Props) {
  const { total_papers, total_researchers, papers_by_status, papers_by_year, researchers_by_position } = data;

  return (
    <div className="space-y-6">
      {/* Big numbers */}
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Total Papers" value={total_papers} />
        <StatCard label="Total Researchers" value={total_researchers} />
      </div>

      {/* Papers by status */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Papers by Status</h2>
        <div className="space-y-2">
          {Object.entries(papers_by_status).map(([status, count]) => (
            <div key={status} className="flex items-center justify-between py-1.5 border-b border-[#2a2d3a] last:border-0">
              <span className="text-sm text-zinc-300">{status.replace(/_/g, " ")}</span>
              <span className="text-sm font-medium text-zinc-100">{count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Papers by year */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Papers by Year</h2>
        <div className="space-y-2">
          {papers_by_year.map(({ year, count }) => (
            <div key={year} className="flex items-center justify-between py-1.5 border-b border-[#2a2d3a] last:border-0">
              <span className="text-sm text-zinc-300">{year}</span>
              <span className="text-sm font-medium text-zinc-100">{count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Researchers by position */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Researchers by Position</h2>
        <div className="space-y-2">
          {Object.entries(researchers_by_position).map(([position, count]) => (
            <div key={position} className="flex items-center justify-between py-1.5 border-b border-[#2a2d3a] last:border-0">
              <span className="text-sm text-zinc-300">{position}</span>
              <span className="text-sm font-medium text-zinc-100">{count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add app/src/app/admin/tabs/ContentTab.tsx
git commit -m "feat: add ContentTab component"
```

---

### Task 7: Frontend — QualityTab

**Files:**
- Create: `app/src/app/admin/tabs/QualityTab.tsx`

- [ ] **Step 1: Create QualityTab component**

Create `app/src/app/admin/tabs/QualityTab.tsx`:

```tsx
import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["quality"];
  totalPapers: number;
  totalResearchers: number;
}

function CoverageBar({
  label,
  count,
  total,
}: {
  label: string;
  count: number;
  total: number;
}) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  const barColor = pct >= 75 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="py-3 border-b border-[#2a2d3a] last:border-0">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm text-zinc-300">{label}</span>
        <span className="text-sm text-zinc-400">
          {count.toLocaleString()} / {total.toLocaleString()}{" "}
          <span className="font-medium text-zinc-200">({pct}%)</span>
        </span>
      </div>
      <div className="h-1.5 bg-[#0f1117] rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function QualityTab({ data, totalPapers, totalResearchers }: Props) {
  return (
    <div className="space-y-6">
      {/* Paper data quality */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Paper Data Coverage</h2>
        <CoverageBar label="Has Abstract" count={data.papers_with_abstract} total={totalPapers} />
        <CoverageBar label="Has DOI" count={data.papers_with_doi} total={totalPapers} />
        <CoverageBar label="OpenAlex Enriched" count={data.papers_with_openalex} total={totalPapers} />
        <CoverageBar label="Has Draft URL" count={data.papers_with_draft_url} total={totalPapers} />
        <CoverageBar label="Draft URL Valid" count={data.draft_url_valid} total={data.papers_with_draft_url || 1} />
      </div>

      {/* Researcher data quality */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Researcher Data Coverage</h2>
        <CoverageBar label="Has Description" count={data.researchers_with_description} total={totalResearchers} />
        <CoverageBar label="JEL Classified" count={data.researchers_with_jel} total={totalResearchers} />
        <CoverageBar label="OpenAlex ID" count={data.researchers_with_openalex_id} total={totalResearchers} />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add app/src/app/admin/tabs/QualityTab.tsx
git commit -m "feat: add QualityTab component with coverage bars"
```

---

### Task 8: Frontend — CostsTab

**Files:**
- Create: `app/src/app/admin/tabs/CostsTab.tsx`

- [ ] **Step 1: Create CostsTab component**

Create `app/src/app/admin/tabs/CostsTab.tsx`:

```tsx
import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["costs"];
}

function formatCost(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export default function CostsTab({ data }: Props) {
  const { total_cost_usd, total_tokens, by_call_type, by_model, batch_vs_realtime, last_30_days } = data;

  return (
    <div className="space-y-6">
      {/* Totals */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Spend</p>
          <p className="text-2xl font-semibold text-zinc-100">{formatCost(total_cost_usd)}</p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Tokens</p>
          <p className="text-2xl font-semibold text-zinc-100">{formatTokens(total_tokens)}</p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Batch / Real-time</p>
          <p className="text-sm text-zinc-200">
            {formatCost(batch_vs_realtime.batch_cost)} / {formatCost(batch_vs_realtime.realtime_cost)}
          </p>
        </div>
      </div>

      {/* By call type */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Cost by Call Type</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
              <th className="text-left py-2 font-medium">Type</th>
              <th className="text-right py-2 font-medium">Calls</th>
              <th className="text-right py-2 font-medium">Tokens</th>
              <th className="text-right py-2 font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {by_call_type.map((row) => (
              <tr key={row.call_type} className="border-b border-[#2a2d3a] last:border-0">
                <td className="py-2 text-zinc-300">{row.call_type.replace(/_/g, " ")}</td>
                <td className="py-2 text-right text-zinc-300">{row.count.toLocaleString()}</td>
                <td className="py-2 text-right text-zinc-300">{formatTokens(row.tokens)}</td>
                <td className="py-2 text-right text-zinc-100 font-medium">{formatCost(row.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* By model */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Cost by Model</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
              <th className="text-left py-2 font-medium">Model</th>
              <th className="text-right py-2 font-medium">Tokens</th>
              <th className="text-right py-2 font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {by_model.map((row) => (
              <tr key={row.model} className="border-b border-[#2a2d3a] last:border-0">
                <td className="py-2 text-zinc-300 font-mono text-xs">{row.model}</td>
                <td className="py-2 text-right text-zinc-300">{formatTokens(row.tokens)}</td>
                <td className="py-2 text-right text-zinc-100 font-medium">{formatCost(row.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Daily trend (last 30 days) */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Daily Cost (Last 30 Days)</h2>
        {last_30_days.length === 0 ? (
          <p className="text-sm text-zinc-500">No data yet</p>
        ) : (
          <div className="space-y-1">
            {last_30_days.map((day) => {
              const maxCost = Math.max(...last_30_days.map((d) => d.cost), 0.01);
              const widthPct = Math.round((day.cost / maxCost) * 100);
              return (
                <div key={day.date} className="flex items-center gap-3">
                  <span className="text-xs text-zinc-500 w-20 shrink-0 font-mono">{day.date.slice(5)}</span>
                  <div className="flex-1 h-4 bg-[#0f1117] rounded overflow-hidden">
                    <div
                      className="h-full bg-[#4a9eff] rounded"
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                  <span className="text-xs text-zinc-400 w-16 text-right shrink-0">{formatCost(day.cost)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add app/src/app/admin/tabs/CostsTab.tsx
git commit -m "feat: add CostsTab component with cost breakdowns and daily trend"
```

---

### Task 9: Frontend — ScrapesTab

**Files:**
- Create: `app/src/app/admin/tabs/ScrapesTab.tsx`

- [ ] **Step 1: Create ScrapesTab component**

Create `app/src/app/admin/tabs/ScrapesTab.tsx`:

```tsx
import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["scrapes"];
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: "bg-emerald-900/50 text-emerald-400 border-emerald-800",
    running: "bg-blue-900/50 text-blue-400 border-blue-800",
    failed: "bg-red-900/50 text-red-400 border-red-800",
  };
  const cls = colors[status] || "bg-zinc-800 text-zinc-400 border-zinc-700";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {status}
    </span>
  );
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export default function ScrapesTab({ data }: Props) {
  const { recent, totals } = data;

  return (
    <div className="space-y-6">
      {/* Totals */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Scrapes</p>
          <p className="text-2xl font-semibold text-zinc-100">{totals.total_scrapes.toLocaleString()}</p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Papers Extracted</p>
          <p className="text-2xl font-semibold text-zinc-100">{totals.total_pubs_extracted.toLocaleString()}</p>
        </div>
      </div>

      {/* Recent scrapes table */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5 overflow-x-auto">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Recent Scrapes</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
              <th className="text-left py-2 font-medium">Date</th>
              <th className="text-left py-2 font-medium">Status</th>
              <th className="text-right py-2 font-medium">Checked</th>
              <th className="text-right py-2 font-medium">Changed</th>
              <th className="text-right py-2 font-medium">Extracted</th>
              <th className="text-right py-2 font-medium">Tokens</th>
              <th className="text-right py-2 font-medium">Duration</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((row, i) => (
              <tr key={i} className="border-b border-[#2a2d3a] last:border-0">
                <td className="py-2 text-zinc-300 font-mono text-xs">{formatDate(row.started_at)}</td>
                <td className="py-2"><StatusBadge status={row.status} /></td>
                <td className="py-2 text-right text-zinc-300">{row.urls_checked}</td>
                <td className="py-2 text-right text-zinc-300">{row.urls_changed}</td>
                <td className="py-2 text-right text-zinc-100 font-medium">{row.pubs_extracted}</td>
                <td className="py-2 text-right text-zinc-300">{row.tokens_used.toLocaleString()}</td>
                <td className="py-2 text-right text-zinc-300">{formatDuration(row.duration_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {recent.length === 0 && (
          <p className="text-sm text-zinc-500 py-4 text-center">No scrapes recorded yet</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add app/src/app/admin/tabs/ScrapesTab.tsx
git commit -m "feat: add ScrapesTab component with scrape history table"
```

---

### Task 10: Frontend — ActivityTab

**Files:**
- Create: `app/src/app/admin/tabs/ActivityTab.tsx`

- [ ] **Step 1: Create ActivityTab component**

Create `app/src/app/admin/tabs/ActivityTab.tsx`:

```tsx
import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["activity"];
}

function EventBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    new_paper: "bg-emerald-900/50 text-emerald-400 border-emerald-800",
    status_change: "bg-amber-900/50 text-amber-400 border-amber-800",
    title_change: "bg-purple-900/50 text-purple-400 border-purple-800",
  };
  const cls = colors[type] || "bg-zinc-800 text-zinc-400 border-zinc-700";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {type.replace(/_/g, " ")}
    </span>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function EventCountCard({ label, counts }: { label: string; counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-zinc-500">{label}</p>
        <p className="text-lg font-semibold text-zinc-100">{total}</p>
      </div>
      <div className="space-y-1">
        {Object.entries(counts).map(([type, count]) => (
          <div key={type} className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">{type.replace(/_/g, " ")}</span>
            <span className="text-zinc-300">{count}</span>
          </div>
        ))}
        {Object.keys(counts).length === 0 && (
          <p className="text-xs text-zinc-600">No events</p>
        )}
      </div>
    </div>
  );
}

export default function ActivityTab({ data }: Props) {
  const { events_last_7d, events_last_30d, recent_events } = data;

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4">
        <EventCountCard label="Last 7 Days" counts={events_last_7d} />
        <EventCountCard label="Last 30 Days" counts={events_last_30d} />
      </div>

      {/* Recent events */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Recent Events</h2>
        <div className="space-y-3">
          {recent_events.map((event, i) => (
            <div
              key={i}
              className="flex items-start gap-3 py-2 border-b border-[#2a2d3a] last:border-0"
            >
              <EventBadge type={event.event_type} />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-zinc-200 truncate">{event.paper_title}</p>
                {event.details && (
                  <p className="text-xs text-zinc-500 mt-0.5">{event.details}</p>
                )}
              </div>
              <span className="text-xs text-zinc-600 shrink-0">{formatDate(event.created_at)}</span>
            </div>
          ))}
          {recent_events.length === 0 && (
            <p className="text-sm text-zinc-500 text-center py-4">No events yet</p>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add app/src/app/admin/tabs/ActivityTab.tsx
git commit -m "feat: add ActivityTab component with event summary and timeline"
```

---

### Task 11: Configuration — env vars and final verification

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add `ADMIN_PASSWORD` to `.env.example`**

Add after the `SCRAPE_API_KEY` line in `.env.example`:

```
ADMIN_PASSWORD=changeme-admin-password
```

- [ ] **Step 2: Run TypeScript type check**

Run: `cd app && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Run Python tests**

Run: `poetry run pytest tests/test_admin_dashboard.py -v`
Expected: All tests pass

- [ ] **Step 4: Run frontend dev server and verify**

Run: `cd app && npm run dev`
Then visit `http://localhost:3000/admin` — should see the login form.

- [ ] **Step 5: Commit**

```bash
git add .env.example
git commit -m "feat: add ADMIN_PASSWORD to .env.example"
```

---

### Task 12: Admin layout override (no Header on admin pages)

The main app layout (`app/src/app/layout.tsx`) renders the `<Header />` component on every page. The admin dashboard has its own dark full-screen layout, so the Header and main container should be excluded.

**Files:**
- Modify: `app/src/app/layout.tsx`
- Create: `app/src/app/admin/layout.tsx`

- [ ] **Step 1: Create admin layout that skips the main app chrome**

Create `app/src/app/admin/layout.tsx`:

```tsx
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
```

- [ ] **Step 2: Modify root layout to exclude Header/main wrapper on admin routes**

This won't work with a simple layout override since the root layout always renders. Instead, the admin dashboard already renders a full-screen `min-h-screen` dark div that covers the page. The simplest approach: hide the Header and main wrapper from the admin page using a conditional.

Update `app/src/app/layout.tsx` — wrap the Header and main in a component that checks the pathname:

Actually, since this is a server component and can't use `usePathname`, a simpler approach is to use a route group. Move the existing pages into a `(main)` route group and give admin its own layout.

Alternative simpler approach: just override with CSS in the admin page. The admin `AdminDashboard` component already renders `min-h-screen bg-[#0f1117]` which will cover the viewport. The Header will show on top, but we can hide it from the admin layout.

Simplest solution: the admin layout sets a class that hides the header:

Create `app/src/app/admin/layout.tsx`:

```tsx
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Admin — Econ Newsfeed",
  robots: "noindex, nofollow",
};

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="admin-layout">
      <style>{`
        .admin-layout ~ header,
        .admin-layout ~ main,
        body > header,
        nav { display: none !important; }
        body > main { padding: 0 !important; max-width: none !important; }
      `}</style>
      {children}
    </div>
  );
}
```

Wait — the root layout renders `<Header />` then `<main>{children}</main>`. The admin layout is nested inside `<main>`. So the admin page content is *inside* the main wrapper. The simplest clean approach:

Create `app/src/app/admin/layout.tsx` that adds a style tag to hide the parent chrome:

```tsx
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <style
        dangerouslySetInnerHTML={{
          __html: `
            body > header, [data-header] { display: none !important; }
            body > main { padding: 0 !important; max-width: none !important; margin: 0 !important; }
          `,
        }}
      />
      {children}
    </>
  );
}
```

- [ ] **Step 2: Add `data-header` attribute to Header for targeting**

Check if the Header component in `app/src/components/Header.tsx` has an identifiable wrapper. Read it first, then add a `data-header` attribute if needed, or target by existing class/tag.

Read `app/src/components/Header.tsx` and add `data-header` to the root element if the Header renders a `<header>` or `<nav>` tag.

- [ ] **Step 3: Verify admin page renders without Header**

Run: `cd app && npm run dev`
Visit `http://localhost:3000/admin` — should show only the dark admin UI with no site Header.

- [ ] **Step 4: Commit**

```bash
git add app/src/app/admin/layout.tsx
git commit -m "feat: admin layout hides main app header and container"
```
