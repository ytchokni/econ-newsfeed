"""Public coverage and activity numbers for the About page (``GET /api/stats``).

Every query is an indexed count or a small aggregate; the API caches the result.
Top-5 journal coverage is read from the latest weekly run (``top5_coverage_runs``),
never computed here.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from backend.database.connection import fetch_all, fetch_one

logger = logging.getLogger(__name__)

WEEKS = 12
JOURNAL_NAMES = {
    "aer": "American Economic Review",
    "ecta": "Econometrica",
    "jpe": "Journal of Political Economy",
    "qje": "Quarterly Journal of Economics",
    "restud": "Review of Economic Studies",
}
_STATUS_GROUPS = {
    "published": "published",
    "accepted": "accepted",
    "revise_and_resubmit": "under_review",
    "reject_and_resubmit": "under_review",
    "working_paper": "working_paper",
    "work_in_progress": "working_paper",
}


def _iso_z(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat() + "Z"


def _int(row: dict | None, key: str) -> int:
    return int((row or {}).get(key) or 0)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_activity(now: datetime | None = None, weeks: int = WEEKS) -> list[dict]:
    """Feed events per ISO week (Monday 00:00 UTC), oldest first; the last week is partial."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    first = _week_start(now.date()) - timedelta(weeks=weeks - 1)
    rows = fetch_all(
        """SELECT DATE(created_at) AS day, event_type, COUNT(*) AS n
           FROM feed_events WHERE created_at >= %s
           GROUP BY DATE(created_at), event_type""",
        (datetime.combine(first, time.min),),
    )
    buckets = {first + timedelta(weeks=i): {"new_papers": 0, "status_changes": 0, "events": 0}
               for i in range(weeks)}
    for r in rows:
        day = r["day"]
        if isinstance(day, datetime):
            day = day.date()
        elif isinstance(day, str):
            day = date.fromisoformat(day[:10])
        b = buckets.get(_week_start(day))
        if b is None:
            continue
        n = int(r["n"] or 0)
        b["events"] += n
        if r["event_type"] == "new_paper":
            b["new_papers"] += n
        elif r["event_type"] == "status_change":
            b["status_changes"] += n
    current = _week_start(now.date())
    return [{"week_start": ws.isoformat(), "partial": ws == current, **counts}
            for ws, counts in sorted(buckets.items())]


def top5_coverage() -> dict | None:
    """Latest stored top-5 coverage run in the public shape, or None before the first run."""
    from backend.coverage.top5_job import latest_run

    try:
        row = latest_run()
    except Exception as exc:  # table missing before the first migration, etc.
        logger.warning("Could not read top5_coverage_runs (no run yet?): %s", exc)
        return None
    if not row:
        return None
    s = row["summary"]
    overall = s.get("overall") or {}
    by_journal = s.get("by_journal") or {}

    def pct(n: int, d: int) -> float | None:
        return round(100.0 * n / d, 1) if d else None

    return {
        "as_of": _iso_z(row["computed_at"]),
        "from_year": int(row["from_year"]),
        "until_year": int(row["until_year"]),
        "articles": int(row["articles"]),
        "found": int(row["observed"]),
        "coverage_pct": pct(int(row["observed"]), int(row["articles"])),
        "tracked_articles": int(row["tracked_articles"]),
        "tracked_found": int(row["tracked_observed"]),
        "coverage_tracked_pct": pct(int(row["tracked_observed"]), int(row["tracked_articles"])),
        "correct_record_pct": pct(int(overall.get("observed_correct") or 0), int(row["articles"])),
        # Exactly the five journals, in a fixed order, whatever else the summary holds.
        "by_journal": [
            {
                "journal": key,
                "name": name,
                "articles": int(b.get("articles") or 0),
                "found": int(b.get("observed") or 0),
                "coverage_pct": b.get("coverage_pct"),
                "coverage_tracked_pct": b.get("coverage_tracked_pct"),
            }
            for key, name in JOURNAL_NAMES.items()
            if (b := by_journal.get(key))
        ],
    }


def _fetch_interval_hours() -> int:
    from backend.pipeline.scheduler import SCRAPE_INTERVAL_HOURS

    return int(SCRAPE_INTERVAL_HOURS)


def get_site_stats(now: datetime | None = None) -> dict:
    """Numbers for the About page. Cheap enough to run every few minutes."""
    counts = fetch_one(
        """SELECT
               (SELECT COUNT(*) FROM researchers) AS researchers_total,
               (SELECT COUNT(DISTINCT researcher_id) FROM researcher_urls
                 WHERE is_active = TRUE) AS researchers_tracked,
               (SELECT COUNT(*) FROM researcher_urls WHERE is_active = TRUE) AS pages_tracked,
               (SELECT COUNT(*) FROM papers) AS papers_total"""
    )
    by_status: dict[str, int] = {"working_paper": 0, "under_review": 0, "accepted": 0,
                                 "published": 0, "unknown": 0}
    for r in fetch_all("SELECT status, COUNT(*) AS n FROM papers GROUP BY status"):
        by_status[_STATUS_GROUPS.get(r["status"], "unknown")] += int(r["n"] or 0)

    last_fetch = fetch_one(
        "SELECT MAX(finished_at) AS t FROM scrape_log WHERE status = 'completed'"
    )
    last_extraction = fetch_one("SELECT MAX(extracted_at) AS t FROM html_content")
    weeks = weekly_activity(now)
    full_weeks = [w for w in weeks if not w["partial"]]
    recent = full_weeks[-4:]
    return {
        "generated_at": _iso_z(now or datetime.now(timezone.utc)),
        "researchers": {
            "total": _int(counts, "researchers_total"),
            "tracked": _int(counts, "researchers_tracked"),
            "pages_tracked": _int(counts, "pages_tracked"),
        },
        "papers": {"total": _int(counts, "papers_total"), "by_status": by_status},
        "activity": {
            "weeks": weeks,
            "new_papers_per_week": round(sum(w["new_papers"] for w in recent) / len(recent)) if recent else 0,
            "events_per_week": round(sum(w["events"] for w in recent) / len(recent)) if recent else 0,
        },
        "top5": top5_coverage(),
        "fetch_interval_hours": _fetch_interval_hours(),
        "last_fetch_at": _iso_z((last_fetch or {}).get("t")),
        "last_extraction_at": _iso_z((last_extraction or {}).get("t")),
    }
