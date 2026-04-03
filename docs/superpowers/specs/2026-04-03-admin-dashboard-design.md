# Admin Dashboard Design

**Date:** 2026-04-03
**Status:** Approved

## Overview

A password-protected admin dashboard at `/admin` in the existing Next.js app. Tabbed interface showing system health, content stats, data quality, LLM costs, scrape history, and feed activity. Single backend endpoint returns all metrics. Styling guided by the `frontend-design` skill.

## Authentication

### Login Flow

1. `/admin` checks for a session cookie. No cookie → password form.
2. Password form POSTs to Next.js API route `/api/admin/login`.
3. Server compares input against `ADMIN_PASSWORD` env var (constant-time comparison).
4. On success: sets an httpOnly, secure, sameSite=strict cookie containing an HMAC-signed timestamp (using `ADMIN_PASSWORD` as secret). Expires in 7 days.
5. `/api/admin/logout` clears the cookie.

### Backend Auth

- New FastAPI endpoint `GET /api/admin/dashboard` protected by existing `SCRAPE_API_KEY` mechanism.
- Next.js API route `/api/admin/dashboard/route.ts` validates session cookie, then proxies to FastAPI with `X-API-Key` header. Browser never sees the API key.

### Env Vars

| Var | Where | New? |
|-----|-------|------|
| `ADMIN_PASSWORD` | Vercel + local `.env` | Yes |
| `SCRAPE_API_KEY` | Already exists everywhere | No |

## Backend: `GET /api/admin/dashboard`

Single endpoint returning all dashboard data. Implemented in a new `database/admin.py` submodule with a `get_admin_dashboard_stats()` method.

### Response Shape

```json
{
  "health": {
    "last_scrape": {
      "started_at": "2026-04-03T10:00:00Z",
      "status": "completed",
      "urls_checked": 42,
      "urls_changed": 5,
      "pubs_extracted": 12,
      "duration_seconds": 180
    },
    "next_scrape_at": "2026-04-03T22:00:00Z",
    "scrape_in_progress": false,
    "total_researcher_urls": 42,
    "urls_by_page_type": { "personal_website": 30, "cv": 8, "google_scholar": 4 }
  },
  "content": {
    "total_papers": 850,
    "papers_by_status": { "published": 600, "working_paper": 200, "accepted": 50 },
    "papers_by_year": [{ "year": 2024, "count": 120 }],
    "total_researchers": 45,
    "researchers_by_position": { "Professor": 20, "Assistant Professor": 15 }
  },
  "quality": {
    "papers_with_abstract": 700,
    "papers_with_doi": 500,
    "papers_with_openalex": 450,
    "papers_with_draft_url": 300,
    "draft_url_valid": 250,
    "researchers_with_description": 40,
    "researchers_with_jel": 35,
    "researchers_with_openalex_id": 42
  },
  "costs": {
    "total_cost_usd": 12.50,
    "total_tokens": 2500000,
    "by_call_type": [
      { "call_type": "publication_extraction", "cost": 8.0, "tokens": 1800000, "count": 500 }
    ],
    "by_model": [
      { "model": "gpt-4o-mini", "cost": 10.0, "tokens": 2200000 }
    ],
    "batch_vs_realtime": { "batch_cost": 3.0, "realtime_cost": 9.50 },
    "last_30_days": [{ "date": "2026-04-01", "cost": 0.45, "tokens": 50000 }]
  },
  "scrapes": {
    "recent": [
      // Last 30 scrapes
      {
        "started_at": "2026-04-03T10:00:00Z",
        "status": "completed",
        "urls_checked": 42,
        "urls_changed": 5,
        "pubs_extracted": 12,
        "tokens_used": 50000,
        "duration_seconds": 180
      }
    ],
    "totals": { "total_scrapes": 200, "total_pubs_extracted": 850 }
  },
  "activity": {
    "events_last_7d": { "new_paper": 12, "status_change": 5, "title_change": 2 },
    "events_last_30d": { "new_paper": 45, "status_change": 18, "title_change": 8 },
    "recent_events": [
      // Last 50 events
      { "event_type": "new_paper", "paper_title": "...", "created_at": "...", "details": "..." }
    ]
  }
}
```

## Frontend

### Route & Components

```
app/src/app/admin/
├── page.tsx              # Server component, renders AdminDashboard
├── AdminDashboard.tsx    # Client component: login gate + tab container
├── LoginForm.tsx         # Password form
├── tabs/
│   ├── HealthTab.tsx     # Last scrape, next run, URL counts
│   ├── ContentTab.tsx    # Papers/researchers counts, breakdowns
│   ├── QualityTab.tsx    # Coverage percentages, missing data gaps
│   ├── CostsTab.tsx      # LLM spend, token usage, trends
│   ├── ScrapesTab.tsx    # Scrape history table
│   └── ActivityTab.tsx   # Feed events summary + recent events list
```

### Next.js API Routes

- `app/api/admin/login/route.ts` — POST: validate password, set session cookie
- `app/api/admin/logout/route.ts` — POST: clear session cookie
- `app/api/admin/dashboard/route.ts` — GET: validate cookie, proxy to FastAPI

### Tab Content

| Tab | Visuals |
|-----|---------|
| **Health** | Status badge (ok/in-progress/error), last scrape timestamp + key stats, next run countdown, URL count by page type |
| **Content** | Big number cards (total papers, researchers), tables for by-status and by-year breakdowns |
| **Quality** | Progress bars showing coverage (e.g. "DOI: 500/850 = 59%"), gaps highlighted |
| **Costs** | Total spend card, breakdown tables by call type and model, daily cost trend for last 30 days |
| **Scrapes** | Table: date, status, URLs checked/changed, papers extracted, duration |
| **Activity** | Summary cards (7d / 30d event counts), recent events list with type badges |

### Data Fetching

- SWR hook `useAdminDashboard()` calls `/api/admin/dashboard` with 60s refresh interval.
- 401 response → show login form.
- Loading/error states handled per existing app patterns.

### Styling

- Tailwind CSS, consistent with existing app.
- No charting library — tables, number cards, CSS progress bars.
- Dark theme, guided by `frontend-design` skill during implementation.

## Files Changed

### New Files

| File | Purpose |
|------|---------|
| `database/admin.py` | `get_admin_dashboard_stats()` — all aggregation queries |
| `app/src/app/admin/page.tsx` | Server component entry |
| `app/src/app/admin/AdminDashboard.tsx` | Client component: auth gate + tabs |
| `app/src/app/admin/LoginForm.tsx` | Password form |
| `app/src/app/admin/tabs/HealthTab.tsx` | Health tab |
| `app/src/app/admin/tabs/ContentTab.tsx` | Content tab |
| `app/src/app/admin/tabs/QualityTab.tsx` | Quality tab |
| `app/src/app/admin/tabs/CostsTab.tsx` | Costs tab |
| `app/src/app/admin/tabs/ScrapesTab.tsx` | Scrapes tab |
| `app/src/app/admin/tabs/ActivityTab.tsx` | Activity tab |
| `app/src/app/api/admin/login/route.ts` | Login endpoint |
| `app/src/app/api/admin/logout/route.ts` | Logout endpoint |
| `app/src/app/api/admin/dashboard/route.ts` | Dashboard data proxy |

### Modified Files

| File | Change |
|------|--------|
| `api.py` | Add `GET /api/admin/dashboard` endpoint |
| `database/__init__.py` | Wire up `admin.py` methods on `Database` facade |
| `.env.example` | Add `ADMIN_PASSWORD` |
| `.dockerignore` | Add `!database/admin.py` |

## Out of Scope

- No user management (single password)
- No data editing from the dashboard (read-only)
- No charting library (keep lightweight)
- No mobile-specific layout (desktop-first, functional on mobile via responsive Tailwind)
