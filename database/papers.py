"""Paper-related data access: title normalization, dedup hashing, draft URL operations."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from database.connection import execute_query, fetch_all, get_connection
from encoding_guard import fix_encoding


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


def update_openalex_data(paper_id, doi, openalex_id, coauthors, abstract=None, year=None):
    """Store OpenAlex enrichment data for a paper."""
    # Guard abstract against mojibake
    if abstract:
        abstract, _ = fix_encoding(abstract)

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE papers SET doi = %s, openalex_id = %s, "
                "abstract = COALESCE(%s, abstract), "
                "year = COALESCE(%s, year) "
                "WHERE id = %s",
                (doi, openalex_id, abstract, year, paper_id),
            )
            cursor.execute(
                "DELETE FROM openalex_coauthors WHERE paper_id = %s", (paper_id,)
            )
            if coauthors:
                # Guard coauthor display names
                for ca in coauthors:
                    ca["display_name"], _ = fix_encoding(ca["display_name"])
                cursor.executemany(
                    "INSERT IGNORE INTO openalex_coauthors (paper_id, display_name, openalex_author_id) "
                    "VALUES (%s, %s, %s)",
                    [(paper_id, ca["display_name"], ca.get("openalex_author_id")) for ca in coauthors],
                )
            conn.commit()


def get_unenriched_papers(limit=50):
    """Get papers that haven't been enriched via OpenAlex yet.

    Returns list of dicts with keys: id, title, abstract, author_name, status, link_doi.
    Papers with links get priority. Only published papers without links are included.
    """
    return fetch_all(
        """
        SELECT p.id, p.title, p.abstract, p.status,
               MIN(CONCAT(r.first_name, ' ', r.last_name)) AS author_name,
               MAX(pl.doi) AS link_doi
        FROM papers p
        JOIN authorship a ON a.publication_id = p.id
        JOIN researchers r ON r.id = a.researcher_id
        LEFT JOIN paper_links pl ON pl.paper_id = p.id AND pl.doi IS NOT NULL
        WHERE p.openalex_id IS NULL
          AND (
            EXISTS (SELECT 1 FROM paper_links pl2 WHERE pl2.paper_id = p.id)
            OR p.status = 'published'
          )
        GROUP BY p.id, p.title, p.abstract, p.status
        ORDER BY link_doi IS NOT NULL DESC, p.id
        LIMIT %s
        """,
        (limit,),
    )
