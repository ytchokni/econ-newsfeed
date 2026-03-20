"""Paper-related data access: title normalization, dedup hashing, draft URL operations."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from database.connection import execute_query, fetch_all, get_connection


def normalize_title(title: str | None) -> str:
    """Normalize a title for dedup: lowercase, strip punctuation, collapse whitespace."""
    if not title:
        return ''
    t = title.lower().strip()
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def compute_title_hash(title: str | None) -> str:
    """SHA-256 hash of normalized title for cross-researcher dedup."""
    normalized = normalize_title(title)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def update_draft_url_status(paper_id: int, status: str) -> None:
    """Update draft URL validation status."""
    execute_query(
        "UPDATE papers SET draft_url_status = %s, draft_url_checked_at = %s WHERE id = %s",
        (status, datetime.now(timezone.utc), paper_id),
    )


def get_unchecked_draft_urls(limit: int = 100) -> list[dict]:
    """Get papers with unchecked draft URLs for validation."""
    return fetch_all(
        """SELECT id, draft_url FROM papers
           WHERE draft_url IS NOT NULL AND draft_url_status = 'unchecked'
           LIMIT %s""",
        (limit,),
    )


def update_openalex_data(paper_id, doi, openalex_id, coauthors, abstract=None):
    """Store OpenAlex enrichment data for a paper.

    Updates doi and openalex_id on the papers row. Inserts co-authors into
    openalex_coauthors. Optionally sets abstract (fallback only).
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if abstract:
                cursor.execute(
                    "UPDATE papers SET doi = %s, openalex_id = %s, abstract = %s WHERE id = %s",
                    (doi, openalex_id, abstract, paper_id),
                )
            else:
                cursor.execute(
                    "UPDATE papers SET doi = %s, openalex_id = %s WHERE id = %s",
                    (doi, openalex_id, paper_id),
                )
            # Replace coauthors (handles re-enrichment cleanly)
            cursor.execute(
                "DELETE FROM openalex_coauthors WHERE paper_id = %s", (paper_id,)
            )
            for ca in coauthors:
                cursor.execute(
                    "INSERT INTO openalex_coauthors (paper_id, display_name, openalex_author_id) "
                    "VALUES (%s, %s, %s)",
                    (paper_id, ca["display_name"], ca.get("openalex_author_id")),
                )
            conn.commit()


def get_unenriched_papers(limit=50):
    """Get papers that haven't been enriched via OpenAlex yet.

    Returns list of dicts with keys: id, title, abstract, author_name.
    Each row includes one author name (for OpenAlex search matching).
    """
    return fetch_all(
        """
        SELECT p.id, p.title, p.abstract,
               CONCAT(r.first_name, ' ', r.last_name) AS author_name
        FROM papers p
        JOIN authorship a ON a.publication_id = p.id
        JOIN researchers r ON r.id = a.researcher_id
        WHERE p.openalex_id IS NULL
        GROUP BY p.id, p.title, p.abstract
        LIMIT %s
        """,
        (limit,),
    )
