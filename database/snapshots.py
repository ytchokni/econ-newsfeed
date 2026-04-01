"""Append-only snapshot versioning for researchers and papers."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from database.connection import get_connection, fetch_all


# ── Researcher snapshots ──

def _compute_researcher_content_hash(position: str | None, affiliation: str | None,
                                     description: str | None) -> str:
    """Compute content hash for researcher change detection."""
    parts = '||'.join(str(v or '') for v in (position, affiliation, description))
    return hashlib.sha256(parts.encode('utf-8')).hexdigest()


def append_researcher_snapshot(researcher_id: int, position: str | None, affiliation: str | None,
                               description: str | None, source_url: str | None = None) -> bool:
    """Append a snapshot if profile changed. Updates denormalized researchers table.
    Hash check and insert run in a single transaction to prevent race conditions.
    Returns True if a new snapshot was inserted, False if no change."""
    content_hash = _compute_researcher_content_hash(position, affiliation, description)
    now = datetime.now(timezone.utc)

    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT content_hash FROM researcher_snapshots "
                "WHERE researcher_id = %s ORDER BY scraped_at DESC LIMIT 1",
                (researcher_id,),
            )
            prev = cursor.fetchone()
            if prev and prev['content_hash'] == content_hash:
                return False

            cursor.execute(
                """INSERT INTO researcher_snapshots
                   (researcher_id, position, affiliation, description, scraped_at, source_url, content_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (researcher_id, position, affiliation, description, now, source_url, content_hash),
            )
            cursor.execute(
                """UPDATE researchers
                   SET position = %s, affiliation = %s, description = %s, description_updated_at = %s
                   WHERE id = %s""",
                (position, affiliation, description, now, researcher_id),
            )
            conn.commit()
    logging.info(f"Researcher snapshot appended for id={researcher_id}")
    return True


def get_researcher_snapshots(researcher_id: int, limit: int = 20) -> list[dict]:
    """Return recent snapshots for a researcher, newest first."""
    return fetch_all(
        """SELECT position, affiliation, description, scraped_at, source_url
           FROM researcher_snapshots WHERE researcher_id = %s
           ORDER BY scraped_at DESC LIMIT %s""",
        (researcher_id, limit),
    )


# ── Paper snapshots ──

def _compute_paper_content_hash(status: str | None, venue: str | None, abstract: str | None,
                                draft_url: str | None, year: str | None,
                                title: str | None = None) -> str:
    """Compute content hash for paper change detection."""
    parts = '||'.join(str(v or '') for v in (title, status, venue, abstract, draft_url, year))
    return hashlib.sha256(parts.encode('utf-8')).hexdigest()


def append_paper_snapshot(paper_id: int, status: str | None, venue: str | None,
                          abstract: str | None, draft_url: str | None, year: str | None,
                          source_url: str | None = None, title: str | None = None) -> bool:
    """Append a paper snapshot if metadata changed. Updates denormalized papers table.
    Creates a feed_event if status changed.
    Hash check and insert run in a single transaction to prevent race conditions.
    Returns True if a new snapshot was inserted, False if no change."""
    content_hash = _compute_paper_content_hash(status, venue, abstract, draft_url, year, title=title)
    now = datetime.now(timezone.utc)

    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            # Check if content has changed
            cursor.execute(
                "SELECT content_hash, status FROM paper_snapshots "
                "WHERE paper_id = %s ORDER BY scraped_at DESC LIMIT 1",
                (paper_id,),
            )
            prev = cursor.fetchone()
            if prev and prev['content_hash'] == content_hash:
                return False

            old_status = prev['status'] if prev else None

            cursor.execute(
                """INSERT INTO paper_snapshots
                   (paper_id, title, status, venue, abstract, draft_url, draft_url_status, year,
                    scraped_at, source_url, content_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, 'unchecked', %s, %s, %s, %s)""",
                (paper_id, title, status, venue, abstract, draft_url, year, now, source_url, content_hash),
            )
            cursor.execute(
                """UPDATE papers
                   SET status = %s, venue = %s, abstract = %s, draft_url = %s,
                       draft_url_status = 'unchecked', year = %s
                   WHERE id = %s""",
                (status, venue, abstract, draft_url, year, paper_id),
            )

            # Create status_change feed event if status actually changed
            if (old_status != status
                    and old_status is not None
                    and status is not None):
                cursor.execute(
                    """INSERT INTO feed_events
                       (paper_id, event_type, old_status, new_status, created_at)
                       VALUES (%s, 'status_change', %s, %s, %s)""",
                    (paper_id, old_status, status, now),
                )

            conn.commit()
    logging.info(f"Paper snapshot appended for id={paper_id}")
    return True


def get_paper_snapshots(paper_id: int, limit: int = 20) -> list[dict]:
    """Return recent snapshots for a paper, newest first."""
    return fetch_all(
        """SELECT status, venue, abstract, draft_url, draft_url_status, year, scraped_at, source_url
           FROM paper_snapshots WHERE paper_id = %s
           ORDER BY scraped_at DESC LIMIT %s""",
        (paper_id, limit),
    )
