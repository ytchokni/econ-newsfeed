"""Feed event creation — new_paper, status_change, title_change.

Consolidates event logic previously scattered across publication.py
(save_publications, reconcile_title_renames) and database/snapshots.py.
"""
from database import Database
from datetime import datetime, timezone
import logging
import zlib


def _url_has_baseline(cursor, url: str, min_snapshots: int = 2) -> bool:
    """Return True if the URL has accumulated at least min_snapshots archived HTML states.

    Guards against emitting new_paper events on first-ever extractions of a URL,
    where all papers would appear 'new' even though they may be years old."""
    cursor.execute(
        """SELECT COUNT(*) FROM html_snapshots
           WHERE url_id = (SELECT id FROM researcher_urls WHERE url = %s LIMIT 1)""",
        (url,),
    )
    row = cursor.fetchone()
    return (row[0] if row else 0) >= min_snapshots


def _get_previous_snapshot_html(cursor, url: str) -> str | None:
    """Fetch and decompress the previous HTML snapshot for a URL.

    Returns lowercased HTML text, or None if no previous snapshot exists.
    Designed to be called once per URL (before the publication loop),
    not once per publication.

    The query uses OFFSET 1 to skip the most-recent snapshot (the one just
    fetched in the current run) and return the second-most-recent *distinct*
    HTML state.  This is a proxy for "what the page looked like when the LLM
    last ran" — not an exact match, but sufficient because researcher pages
    rarely revert to a prior state.
    """
    cursor.execute(
        """SELECT hs.raw_html_compressed
           FROM html_snapshots hs
           JOIN researcher_urls ru ON ru.id = hs.url_id
           WHERE ru.url = %s
           ORDER BY hs.snapshot_at DESC
           LIMIT 1 OFFSET 1""",
        (url,),
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    try:
        return zlib.decompress(row[0]).decode("utf-8", errors="replace").lower()
    except Exception:
        return None


def _title_in_previous_snapshot(title: str, prev_html_lower: str | None) -> bool:
    """Return True if the paper title appears in the previous HTML snapshot text.

    If the full cleaned title is found in the pre-fetched HTML, the paper was
    already on the page and should not generate a new_paper feed event.
    """
    if not prev_html_lower:
        return False
    return title.lower() in prev_html_lower


class FeedEventEmitter:
    @staticmethod
    def emit_new_paper_events(results, url: str, is_seed: bool = False) -> int:
        """Create new_paper feed events for save results. Returns count of events created."""
        if is_seed or not results:
            return 0

        events_created = 0
        with Database.get_connection() as conn:
            cursor = conn.cursor(buffered=True)
            has_baseline = _url_has_baseline(cursor, url)
            prev_html_lower = _get_previous_snapshot_html(cursor, url)
            cursor.close()

            for r in results:
                if not r.status or r.status == 'published':
                    continue
                if not has_baseline:
                    logging.info("Suppressed new_paper event for '%s': source URL lacks baseline snapshots", r.title)
                    continue
                if _title_in_previous_snapshot(r.title, prev_html_lower):
                    logging.info("Suppressed new_paper event for '%s': title found in previous HTML snapshot", r.title)
                    continue

                cursor = conn.cursor(buffered=True)
                if r.is_new:
                    cursor.execute(
                        """INSERT INTO feed_events (paper_id, event_type, new_status, created_at)
                           VALUES (%s, 'new_paper', %s, %s)""",
                        (r.paper_id, r.status, datetime.now(timezone.utc)),
                    )
                    events_created += 1
                elif r.new_to_this_url:
                    cursor.execute(
                        "SELECT COUNT(*) FROM feed_events WHERE paper_id = %s AND event_type = 'new_paper'",
                        (r.paper_id,),
                    )
                    if cursor.fetchone()[0] == 0:
                        cursor.execute(
                            """INSERT INTO feed_events (paper_id, event_type, new_status, created_at)
                               VALUES (%s, 'new_paper', %s, %s)""",
                            (r.paper_id, r.status, datetime.now(timezone.utc)),
                        )
                        events_created += 1
                cursor.close()

            conn.commit()
        return events_created

    @staticmethod
    def emit_status_change(paper_id: int, old_status: str, new_status: str) -> None:
        """Create a status_change feed event."""
        with Database.get_connection() as conn:
            cursor = conn.cursor(buffered=True)
            cursor.execute(
                """INSERT INTO feed_events
                   (paper_id, event_type, old_status, new_status, created_at)
                   VALUES (%s, 'status_change', %s, %s, %s)""",
                (paper_id, old_status, new_status, datetime.now(timezone.utc)),
            )
            conn.commit()
            cursor.close()

    @staticmethod
    def emit_title_change(paper_id: int, old_title: str, new_title: str) -> None:
        """Create a title_change feed event."""
        with Database.get_connection() as conn:
            cursor = conn.cursor(buffered=True)
            cursor.execute(
                """INSERT INTO feed_events
                   (paper_id, event_type, old_title, new_title, created_at)
                   VALUES (%s, 'title_change', %s, %s, %s)""",
                (paper_id, old_title, new_title, datetime.now(timezone.utc)),
            )
            conn.commit()
            cursor.close()
