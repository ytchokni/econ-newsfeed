# Extraction Progress Admin Tab

**Date:** 2026-06-10
**Status:** Approved

## Problem

The continuous extraction worker (PR #139/#140) is draining a ~11k-page backlog in prod, but the only way to surveil it is SSH + log grepping + manual SQL. Worker LLM calls log with `scrape_log_id=NULL`, so the existing Scrapes tab shows nothing about it.

## Decision

Add an **"Extraction" tab** to the existing admin dashboard, fed by a new `extraction` section in the existing `/api/admin/dashboard` aggregate response. No new endpoint, no schema changes — all metrics derive from `html_content` and `llm_usage`. The dashboard's SWR hook gains a 60s `refreshInterval` so the page self-updates while watching.

## Backend: `_get_extraction_stats()` in `database/admin.py`

Added to `get_admin_dashboard_stats()` as `"extraction"`. Response shape:

```json
{
  "worker_enabled": true,
  "queue": {"never_extracted": 6705, "changed_pending": 4213, "total": 10918},
  "throughput": {
    "completions": {"last_hour": 38, "last_24h": 950, "last_7d": 950},
    "attempts":    {"last_hour": 41, "last_24h": 1010, "last_7d": 1010}
  },
  "eta_days": 11.5,
  "last_call_at": "2026-06-10T08:11:02Z",
  "last_extracted_at": "2026-06-10T08:09:14Z",
  "tokens_last_24h": 412345,
  "daily": [{"date": "2026-06-10", "count": 950}, ...],
  "recent_calls": [{"called_at": "...", "context_url": "...", "model": "gemma-4-31b-it", "total_tokens": 4102}, ...]
}
```

Definitions:
- **Queue** uses the same predicate as the worker's `get_urls_needing_extraction()` (active URLs, `content_hash IS NOT NULL`, hash mismatch or never extracted), split by `extracted_hash IS NULL`. The number on the dashboard matches what the worker logs.
- **Completions** = `html_content.extracted_at` in window (a successful `mark_extracted`). **Attempts** = `llm_usage` rows with `call_type = 'publication_extraction'` in window. Attempts − completions ≈ failures/empty-parse retries, making quota exhaustion visible.
- **eta_days** = `queue.total / completions.last_24h` (null when the 24h rate is 0). Float, one decimal in UI.
- **worker_enabled** read from `scheduler.EXTRACTION_WORKER_ENABLED` (same import-in-function pattern as `_get_health_stats`).
- **daily** = completions per day, last 14 days, from `extracted_at`. Caveat (accepted): `extracted_at` is overwritten on re-extraction, so historic days undercount slightly.
- **recent_calls** = last 20 `publication_extraction` rows (called_at, context_url, model, total_tokens).

### Liveness semantics (cross-process caveat)

The API request may be served by the gunicorn worker that does NOT own the worker thread, so thread state is unreadable. Liveness is proxied: the UI shows a **stalled** warning when `worker_enabled` is true, `queue.total > 0`, and `last_call_at` is older than 30 minutes (or null). The 30-minute threshold is a frontend constant — the worker's worst-case quiet period while healthy is the 10-minute backoff.

## Frontend

- `app/src/app/admin/tabs/ExtractionTab.tsx`, styled like the existing tabs (stat cards + tables, dark palette):
  - Status row: worker enabled/disabled badge, stalled warning per the liveness rule, last call / last extraction relative times.
  - Stat cards: queue total (with never/changed split), completions last 24h, ETA days, tokens last 24h.
  - Throughput table: 1h / 24h / 7d × completions / attempts.
  - Daily table: last 14 days (date, count) — same plain-rows style as Costs' 30-day list.
  - Recent calls table: time, URL (truncated, link), model, tokens.
- `AdminDashboard.tsx`: add `{ id: "extraction", label: "Extraction" }` to `TABS` + render branch.
- `lib/api.ts`: extend `AdminDashboardData` with the `extraction` type; add `refreshInterval: 60_000` to the `useAdminDashboard` SWR options.

## Testing

- `tests/test_admin_dashboard.py`: `_get_extraction_stats` returns the documented shape with mocked `fetch_one`/`fetch_all` (match the file's existing mocking pattern); ETA null when 24h completions = 0; queue split sums to total.
- Frontend jest: `ExtractionTab` renders fixture data (queue numbers, ETA, stalled badge when last_call_at old, recent-calls rows).

## Out of Scope

- No dedicated polling endpoint (60s aggregate refresh is enough at ~1 URL/min).
- No per-run attribution of worker calls (`scrape_log_id` stays NULL — known follow-up from PR #139 review).
- No worker control (pause/resume) from the dashboard.
