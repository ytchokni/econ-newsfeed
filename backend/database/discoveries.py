"""URL discovery operations — candidates, results, review queue."""
from __future__ import annotations

import json
import logging

from backend.database.connection import execute_query, fetch_all, fetch_one
from backend.database.researchers import add_researcher_url
from backend.database.search_helpers import top5_venue_clause

logger = logging.getLogger(__name__)


def get_discovery_candidates(limit: int = 100) -> list[dict]:
    """Return researchers eligible for URL discovery, in random order.

    Eligible: coauthor (via authorship) of someone with 2+ top-5 publications,
    has no active researcher_urls, and not already in url_discoveries.
    """
    venue_clause, venue_params = top5_venue_clause("p.venue")

    query = f"""
        SELECT DISTINCT r.id, r.first_name, r.last_name, r.affiliation
        FROM researchers r
        JOIN authorship a1 ON r.id = a1.researcher_id
        JOIN authorship a2 ON a1.publication_id = a2.publication_id
            AND a1.researcher_id != a2.researcher_id
        WHERE NOT EXISTS (
            SELECT 1 FROM researcher_urls ru
            WHERE ru.researcher_id = r.id AND ru.is_active = 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM url_discoveries ud
            WHERE ud.researcher_id = r.id
        )
        AND a2.researcher_id IN (
            SELECT a3.researcher_id
            FROM authorship a3
            JOIN papers p ON a3.publication_id = p.id
            WHERE {venue_clause}
            GROUP BY a3.researcher_id
            HAVING COUNT(DISTINCT p.id) >= 2
        )
        ORDER BY RAND()
        LIMIT %s
    """
    return fetch_all(query, [*venue_params, limit])


def insert_discovery(
    researcher_id: int,
    url: str | None,
    subpages: list[dict] | None,
    confidence: float | None,
    search_query: str,
) -> None:
    """Insert a discovery result. url=None means no personal website found."""
    status = "pending" if url else "no_result"
    execute_query(
        """INSERT INTO url_discoveries
           (researcher_id, url, subpages, confidence, search_query, status, searched_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
        (
            researcher_id,
            url,
            json.dumps(subpages) if subpages else None,
            confidence,
            search_query,
            status,
        ),
    )


def get_pending_discoveries() -> list[dict]:
    """Return all pending discoveries for admin review."""
    return fetch_all("""
        SELECT ud.id, ud.researcher_id, ud.url, ud.subpages, ud.confidence,
               ud.search_query, ud.searched_at,
               r.first_name, r.last_name, r.affiliation
        FROM url_discoveries ud
        JOIN researchers r ON ud.researcher_id = r.id
        WHERE ud.status = 'pending'
        ORDER BY ud.confidence DESC, ud.searched_at DESC
    """)


def approve_discovery(discovery_id: int) -> None:
    """Approve a discovery: copy URL + subpages to researcher_urls."""
    row = fetch_one(
        "SELECT researcher_id, url, subpages FROM url_discoveries WHERE id = %s",
        (discovery_id,),
    )
    if not row or not row["url"]:
        return

    add_researcher_url(row["researcher_id"], "personal", row["url"])

    if row["subpages"]:
        subs = json.loads(row["subpages"]) if isinstance(row["subpages"], str) else row["subpages"]
        for sp in subs:
            add_researcher_url(row["researcher_id"], sp["page_type"], sp["url"])

    execute_query(
        "UPDATE url_discoveries SET status = 'approved', reviewed_at = NOW() WHERE id = %s",
        (discovery_id,),
    )


def reject_discovery(discovery_id: int) -> None:
    """Reject a discovery."""
    execute_query(
        "UPDATE url_discoveries SET status = 'rejected', reviewed_at = NOW() WHERE id = %s",
        (discovery_id,),
    )


def bulk_approve_discoveries(min_confidence: float = 0.8) -> int:
    """Approve all pending discoveries above the confidence threshold. Returns count."""
    rows = fetch_all(
        "SELECT id FROM url_discoveries WHERE status = 'pending' AND confidence >= %s",
        (min_confidence,),
    )
    for row in rows:
        approve_discovery(row["id"])
    return len(rows)


def get_discovery_stats() -> dict:
    """Aggregate stats for the admin dashboard."""
    row = fetch_one("""
        SELECT
            (SELECT COUNT(*) FROM url_discoveries) AS total_searched,
            (SELECT COUNT(*) FROM url_discoveries WHERE status = 'pending') AS pending_review,
            (SELECT COUNT(*) FROM url_discoveries WHERE status = 'approved') AS approved,
            (SELECT COUNT(*) FROM url_discoveries WHERE status = 'rejected') AS rejected,
            (SELECT COUNT(*) FROM url_discoveries WHERE status = 'no_result') AS no_result
    """)
    if not row:
        return {"total_searched": 0, "pending_review": 0, "approved": 0, "rejected": 0, "no_result": 0, "pool_remaining": 0}

    stats = dict(row)

    # Count remaining pool (expensive query — cache in admin dashboard)
    venue_clause, venue_params = top5_venue_clause("p.venue")
    pool_row = fetch_one(f"""
        SELECT COUNT(DISTINCT r.id) AS cnt
        FROM researchers r
        JOIN authorship a1 ON r.id = a1.researcher_id
        JOIN authorship a2 ON a1.publication_id = a2.publication_id
            AND a1.researcher_id != a2.researcher_id
        WHERE NOT EXISTS (
            SELECT 1 FROM researcher_urls ru
            WHERE ru.researcher_id = r.id AND ru.is_active = 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM url_discoveries ud
            WHERE ud.researcher_id = r.id
        )
        AND a2.researcher_id IN (
            SELECT a3.researcher_id
            FROM authorship a3
            JOIN papers p ON a3.publication_id = p.id
            WHERE {venue_clause}
            GROUP BY a3.researcher_id
            HAVING COUNT(DISTINCT p.id) >= 2
        )
    """, venue_params)
    stats["pool_remaining"] = pool_row["cnt"] if pool_row else 0
    return stats


def get_recent_discoveries(limit: int = 20) -> list[dict]:
    """Return recently reviewed discoveries for history view."""
    return fetch_all("""
        SELECT ud.id, ud.researcher_id, ud.url, ud.subpages, ud.confidence,
               ud.status, ud.searched_at, ud.reviewed_at,
               r.first_name, r.last_name, r.affiliation
        FROM url_discoveries ud
        JOIN researchers r ON ud.researcher_id = r.id
        WHERE ud.status IN ('approved', 'rejected')
        ORDER BY ud.reviewed_at DESC
        LIMIT %s
    """, (limit,))
