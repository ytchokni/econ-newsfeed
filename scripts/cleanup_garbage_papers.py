"""One-time cleanup: delete garbage entries from the papers table.

Identifies papers matching known garbage patterns (website snippets,
LLM hallucinations, GitHub repos). Uses CASCADE deletion so authorship,
feed_events, paper_links, etc. are automatically cleaned up.

Usage: poetry run python scripts/cleanup_garbage_papers.py [--dry-run]
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def find_garbage_papers() -> list[dict]:
    """Find papers matching garbage patterns."""
    return Database.fetch_all("""
        SELECT p.id, p.title, p.source_url, p.venue
        FROM papers p
        WHERE
            -- Very short titles with no status/venue (website elements)
            (LENGTH(p.title) < 5 AND p.status IS NULL AND p.venue IS NULL)
            -- Known website noise words
            OR LOWER(TRIM(TRAILING '.' FROM p.title)) IN (
                'cv', 'feed', 'email', 'follow', 'sitemap', 'teaching',
                'publications', 'papers', 'research', 'home', 'contact',
                'about', 'links', 'news', 'jmp', 'bio', 'vita'
            )
            -- LLM hallucinations
            OR LOWER(p.title) LIKE '%no publications%'
            -- Website snippets
            OR LOWER(p.title) LIKE 'welcome to my%'
            OR LOWER(p.title) LIKE 'i will be on the job market%'
            OR LOWER(p.title) LIKE 'i am a %'
            OR LOWER(p.title) LIKE 'i am an %'
            OR LOWER(p.title) LIKE 'my research interests%'
            OR LOWER(p.title) LIKE 'site last updated%'
            OR LOWER(p.title) LIKE 'currently, i am%'
            OR LOWER(p.title) LIKE '%academic webpage%'
            OR LOWER(p.title) LIKE '%powered by%'
            -- Copyright notices
            OR p.title LIKE '©%'
            -- GitHub venue
            OR LOWER(p.venue) LIKE '%github%'
        ORDER BY LENGTH(p.title)
    """)


def main():
    dry_run = "--dry-run" in sys.argv

    garbage = find_garbage_papers()
    logger.info("Found %d garbage papers", len(garbage))

    for g in garbage:
        logger.info("  [%d] %s", g["id"], g["title"][:70])

    if dry_run:
        logger.info("\nDry run -- no deletions made. Remove --dry-run to delete.")
        return

    if not garbage:
        return

    ids = [g["id"] for g in garbage]
    placeholders = ",".join(["%s"] * len(ids))
    Database.execute_query(
        f"DELETE FROM papers WHERE id IN ({placeholders})",
        tuple(ids),
    )
    logger.info("\nDeleted %d garbage papers (CASCADE cleaned child rows)", len(ids))


if __name__ == "__main__":
    main()
