"""Paper-related data access: title normalization, dedup hashing, draft URL operations."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from database.connection import execute_query, fetch_all, fetch_one, get_connection
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


from database.search_helpers import (
    escape_like as _escape_like,
    escape_fulltext as _escape_fulltext,
    FT_MIN_TOKEN_SIZE as _FT_MIN_TOKEN_SIZE,
    TOP20_DEPT_KEYWORDS as _TOP20_DEPT_KEYWORDS,
    TOP5_JOURNAL_KEYWORDS as _TOP5_JOURNAL_KEYWORDS,
)


# ---------------------------------------------------------------------------
# Batch-fetch helpers
# ---------------------------------------------------------------------------

def get_authors_for_papers(paper_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch authors for multiple papers via authorship+researchers JOIN.

    Returns {paper_id: [{id, first_name, last_name}, ...]}.
    Empty input returns {}.
    """
    if not paper_ids:
        return {}
    placeholders = ",".join(["%s"] * len(paper_ids))
    rows = fetch_all(
        f"""
        SELECT a.publication_id, r.id AS researcher_id, r.first_name, r.last_name
        FROM authorship a
        JOIN researchers r ON r.id = a.researcher_id
        WHERE a.publication_id IN ({placeholders})
        ORDER BY a.publication_id, a.author_order
        """,
        tuple(paper_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in paper_ids}
    for row in rows:
        result[row['publication_id']].append({
            "id": row['researcher_id'],
            "first_name": row['first_name'],
            "last_name": row['last_name'],
        })
    return result


def get_coauthors_for_papers(paper_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch OpenAlex coauthors from openalex_coauthors.

    Returns {paper_id: [{display_name, openalex_author_id}, ...]}.
    Empty input returns {}.
    """
    if not paper_ids:
        return {}
    placeholders = ",".join(["%s"] * len(paper_ids))
    rows = fetch_all(
        f"""
        SELECT paper_id, display_name, openalex_author_id
        FROM openalex_coauthors
        WHERE paper_id IN ({placeholders})
        ORDER BY paper_id, id
        """,
        tuple(paper_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in paper_ids}
    for row in rows:
        result[row['paper_id']].append({
            "display_name": row['display_name'],
            "openalex_author_id": row['openalex_author_id'],
        })
    return result


def get_links_for_papers(paper_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch paper links from paper_links.

    Returns {paper_id: [{url, link_type}, ...]}.
    Empty input returns {}.
    """
    if not paper_ids:
        return {}
    placeholders = ",".join(["%s"] * len(paper_ids))
    rows = fetch_all(
        f"SELECT paper_id, url, link_type FROM paper_links WHERE paper_id IN ({placeholders})",
        tuple(paper_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in paper_ids}
    for row in rows:
        result[row['paper_id']].append({"url": row['url'], "link_type": row['link_type']})
    return result


# ---------------------------------------------------------------------------
# Single-paper queries
# ---------------------------------------------------------------------------

def get_paper_detail(paper_id: int) -> dict | None:
    """Fetch a single paper by ID.

    Returns dict with columns: id, title, year, venue, source_url, discovered_at,
    status, draft_url, abstract, draft_url_status, doi, is_seed, title_hash, openalex_id.
    Returns None if not found.
    """
    return fetch_one(
        "SELECT id, title, year, venue, source_url, discovered_at, status, draft_url, "
        "abstract, draft_url_status, doi, is_seed, title_hash, openalex_id "
        "FROM papers WHERE id = %s",
        (paper_id,),
    )


def get_paper_history(paper_id: int) -> list[dict]:
    """Fetch feed events for a paper ordered by created_at DESC.

    Returns list of dicts with keys: id, event_type, old_status, new_status, created_at.
    """
    return fetch_all(
        "SELECT id, event_type, old_status, new_status, created_at "
        "FROM feed_events WHERE paper_id = %s ORDER BY created_at DESC",
        (paper_id,),
    )


# ---------------------------------------------------------------------------
# Feed event search
# ---------------------------------------------------------------------------

def search_feed_events(
    *,
    year=None,
    researcher_id=None,
    status_list=None,
    since: datetime | None = None,
    until: datetime | None = None,
    institution_list=None,
    preset=None,
    search=None,
    event_type=None,
    jel_code=None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """Search feed_events with dynamic filters.

    Returns (rows, total_count). All filter params are optional.

    Filters:
    - year: match p.year
    - researcher_id: EXISTS subquery on authorship
    - status_list: p.status IN (...)
    - since: fe.created_at >= datetime value
    - institution_list: affiliation LIKE match (ignored when preset is set)
    - preset: 'top20' matches top-20 economics departments
    - search: FULLTEXT for >= _FT_MIN_TOKEN_SIZE chars, LIKE fallback for shorter
    - event_type: fe.event_type = ...
    - jel_code: comma-split, EXISTS on researcher_jel_codes
    """
    conditions: list[str] = []
    params: list = []

    if year:
        conditions.append("p.year = %s")
        params.append(year)

    if researcher_id:
        conditions.append(
            "EXISTS (SELECT 1 FROM authorship WHERE publication_id = p.id AND researcher_id = %s)"
        )
        params.append(researcher_id)

    if status_list:
        if len(status_list) == 1:
            conditions.append("p.status = %s")
            params.append(status_list[0])
        else:
            placeholders = ",".join(["%s"] * len(status_list))
            conditions.append(f"p.status IN ({placeholders})")
            params.extend(status_list)

    if since:
        conditions.append("fe.created_at >= %s")
        params.append(since)

    if until:
        conditions.append("fe.created_at <= %s")
        params.append(until)

    if institution_list and not preset:
        if len(institution_list) == 1:
            conditions.append(
                "EXISTS (SELECT 1 FROM authorship a "
                "JOIN researchers r ON r.id = a.researcher_id "
                "WHERE a.publication_id = p.id AND r.affiliation LIKE %s)"
            )
            params.append(f"%{_escape_like(institution_list[0])}%")
        else:
            inst_likes = " OR ".join(["r.affiliation LIKE %s"] * len(institution_list))
            conditions.append(
                f"EXISTS (SELECT 1 FROM authorship a "
                f"JOIN researchers r ON r.id = a.researcher_id "
                f"WHERE a.publication_id = p.id AND ({inst_likes}))"
            )
            params.extend(f"%{_escape_like(i)}%" for i in institution_list)

    if preset == "top20":
        dept_likes = " OR ".join(["r.affiliation LIKE %s"] * len(_TOP20_DEPT_KEYWORDS))
        conditions.append(
            f"EXISTS (SELECT 1 FROM authorship a "
            f"JOIN researchers r ON r.id = a.researcher_id "
            f"WHERE a.publication_id = p.id AND ({dept_likes}))"
        )
        params.extend(f"%{_escape_like(kw)}%" for kw in _TOP20_DEPT_KEYWORDS)

    if preset == "top5_rr_accepted":
        conditions.append("p.status IN ('accepted', 'revise_and_resubmit')")
        venue_likes = " OR ".join(["p.venue LIKE %s"] * len(_TOP5_JOURNAL_KEYWORDS))
        conditions.append(f"({venue_likes})")
        params.extend(f"%{_escape_like(kw)}%" for kw in _TOP5_JOURNAL_KEYWORDS)

    if preset == "has_top5":
        venue_likes = " OR ".join(["p2.venue LIKE %s"] * len(_TOP5_JOURNAL_KEYWORDS))
        conditions.append(
            f"EXISTS (SELECT 1 FROM authorship a2 "
            f"JOIN authorship a3 ON a3.researcher_id = a2.researcher_id "
            f"JOIN papers p2 ON p2.id = a3.publication_id "
            f"WHERE a2.publication_id = p.id AND ({venue_likes}))"
        )
        params.extend(f"%{_escape_like(kw)}%" for kw in _TOP5_JOURNAL_KEYWORDS)

    search_term = search.strip() if search else ""
    if search_term:
        if len(search_term) >= _FT_MIN_TOKEN_SIZE:
            conditions.append("MATCH(p.title, p.abstract) AGAINST (%s IN BOOLEAN MODE)")
            params.append(_escape_fulltext(search_term))
        else:
            escaped = f"%{_escape_like(search_term)}%"
            conditions.append("(p.title LIKE %s ESCAPE '\\\\' OR p.abstract LIKE %s ESCAPE '\\\\')")
            params.extend([escaped, escaped])

    if event_type:
        conditions.append("fe.event_type = %s")
        params.append(event_type)

    jel_list = [j.strip().upper() for j in jel_code.split(",") if j.strip()] if jel_code else []
    if jel_list:
        placeholders = ",".join(["%s"] * len(jel_list))
        conditions.append(
            f"EXISTS (SELECT 1 FROM authorship a "
            f"JOIN researcher_jel_codes rjc ON rjc.researcher_id = a.researcher_id "
            f"WHERE a.publication_id = p.id AND rjc.jel_code IN ({placeholders}))"
        )
        params.extend(jel_list)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_row = fetch_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        {where}
        """,
        tuple(params),
    )
    total = count_row['cnt'] if count_row else 0

    researcher_count_row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT a.researcher_id) AS cnt
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        JOIN authorship a ON a.publication_id = p.id
        {where}
        """,
        tuple(params),
    )
    researcher_count = researcher_count_row['cnt'] if researcher_count_row else 0

    rows = fetch_all(
        f"""
        SELECT fe.id AS event_id, fe.event_type, fe.old_status, fe.new_status,
               fe.old_title, fe.new_title, fe.created_at,
               p.id AS paper_id, p.title, p.year, p.venue, p.source_url, p.discovered_at,
               p.status, p.draft_url, p.abstract, p.draft_url_status, p.doi
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        {where}
        ORDER BY fe.created_at DESC
        LIMIT %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    return rows, total, researcher_count
