"""Queries behind the public XML sitemap (ids plus last-modified times).

Only pages worth indexing are listed:

* researchers we track (at least one row in ``researcher_urls``) who have at
  least one paper. Coauthor-only stubs and empty profiles are thin pages; the
  frontend marks them ``noindex`` with the same rule
  (``isIndexableResearcher`` in ``app/src/lib/seo.ts``).
* papers with at least one author.

Last-modified is the newest of: when the paper was found, its latest feed
event, and (for researchers) when their bio was last refreshed.

Opted-out researchers: the opt-out flow deletes the researcher row (and papers
nobody else lists) and keeps only hashed tombstones, so they drop out of these
queries on their own. If opt-out ever switches to a soft flag instead of a
delete, add the filter to ``SITEMAP_RESEARCHER_EXCLUSION_SQL`` below; the
researcher detail endpoint must then 404 for the same rows.
"""
from __future__ import annotations

from backend.database.connection import fetch_all, fetch_one

# Hook for researchers that must never be listed (see module docstring).
# Appended to the WHERE clause of the researcher queries; ``r`` is the
# researchers alias.
SITEMAP_RESEARCHER_EXCLUSION_SQL = "TRUE"

_EPOCH = "'1970-01-01'"

_RESEARCHER_WHERE = (
    "EXISTS (SELECT 1 FROM researcher_urls ru WHERE ru.researcher_id = r.id) "
    f"AND ({SITEMAP_RESEARCHER_EXCLUSION_SQL})"
)

_PAPER_WHERE = "EXISTS (SELECT 1 FROM authorship a WHERE a.publication_id = p.id)"


def count_sitemap_researchers() -> int:
    row = fetch_one(
        "SELECT COUNT(DISTINCT r.id) AS n FROM researchers r "
        "JOIN authorship a ON a.researcher_id = r.id "
        f"WHERE {_RESEARCHER_WHERE}"
    )
    return int(row["n"]) if row else 0


def count_sitemap_papers() -> int:
    row = fetch_one(f"SELECT COUNT(*) AS n FROM papers p WHERE {_PAPER_WHERE}")
    return int(row["n"]) if row else 0


def get_sitemap_researchers(offset: int, limit: int) -> list[dict]:
    """[{id, lastmod}] ordered by id; lastmod may be None."""
    return fetch_all(
        "SELECT r.id, NULLIF(GREATEST("
        f"  COALESCE(r.description_updated_at, {_EPOCH}),"
        f"  COALESCE(MAX(p.discovered_at), {_EPOCH}),"
        f"  COALESCE(MAX(fe.created_at), {_EPOCH})"
        f"), {_EPOCH}) AS lastmod "
        "FROM researchers r "
        "JOIN authorship a ON a.researcher_id = r.id "
        "JOIN papers p ON p.id = a.publication_id "
        "LEFT JOIN feed_events fe ON fe.paper_id = p.id "
        f"WHERE {_RESEARCHER_WHERE} "
        "GROUP BY r.id ORDER BY r.id LIMIT %s OFFSET %s",
        (limit, offset),
    )


def get_sitemap_papers(offset: int, limit: int) -> list[dict]:
    """[{id, lastmod}] ordered by id; lastmod may be None."""
    return fetch_all(
        "SELECT p.id, NULLIF(GREATEST("
        f"  COALESCE(p.discovered_at, {_EPOCH}),"
        f"  COALESCE(MAX(fe.created_at), {_EPOCH})"
        f"), {_EPOCH}) AS lastmod "
        "FROM papers p "
        "LEFT JOIN feed_events fe ON fe.paper_id = p.id "
        f"WHERE {_PAPER_WHERE} "
        "GROUP BY p.id ORDER BY p.id LIMIT %s OFFSET %s",
        (limit, offset),
    )
