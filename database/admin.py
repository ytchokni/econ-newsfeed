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
    if last_scrape_row and "started_at" in last_scrape_row:
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
    total_urls = url_row.get("total", 0) if url_row else 0

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
        "total_papers": counts.get("total_papers", 0) if counts else 0,
        "total_researchers": counts.get("total_researchers", 0) if counts else 0,
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
        "total_cost_usd": float(totals.get("total_cost_usd", 0)) if totals else 0,
        "total_tokens": int(totals.get("total_tokens", 0)) if totals else 0,
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
            "batch_cost": float(batch_totals.get("batch_cost", 0)) if batch_totals else 0,
            "realtime_cost": float(batch_totals.get("realtime_cost", 0)) if batch_totals else 0,
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
            "total_scrapes": totals.get("total_scrapes", 0) if totals else 0,
            "total_pubs_extracted": int(totals.get("total_pubs_extracted", 0)) if totals else 0,
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
