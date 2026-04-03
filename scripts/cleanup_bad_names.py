"""One-time cleanup: delete researcher records with bad names.

Identifies researchers with empty first names or initial-only last names
(from OpenAlex coauthors or LLM misparsing). Uses CASCADE deletion so
authorship, researcher_jel_codes, researcher_urls, etc. are cleaned up.

Usage: poetry run python scripts/cleanup_bad_names.py [--dry-run]
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def find_bad_name_researchers() -> list[dict]:
    """Find researchers with empty first names or initial-only last names."""
    return Database.fetch_all("""
        SELECT r.id, r.first_name, r.last_name,
               COUNT(a.publication_id) AS pub_count
        FROM researchers r
        LEFT JOIN authorship a ON a.researcher_id = r.id
        WHERE
            TRIM(r.first_name) = ''
            OR r.first_name IS NULL
            OR TRIM(r.last_name) = ''
            OR r.last_name IS NULL
            OR r.last_name REGEXP '^[A-Za-z][.]?$'
        GROUP BY r.id
        ORDER BY r.last_name, r.first_name
    """)


def find_suspicious_researchers() -> list[dict]:
    """Find researchers with other suspicious name patterns for manual review."""
    return Database.fetch_all("""
        SELECT r.id, r.first_name, r.last_name,
               COUNT(a.publication_id) AS pub_count
        FROM researchers r
        LEFT JOIN authorship a ON a.researcher_id = r.id
        WHERE
            LENGTH(TRIM(r.first_name)) = 1
            OR r.first_name REGEXP '^[^a-zA-Z]'
            OR r.last_name REGEXP '^[^a-zA-Z]'
        GROUP BY r.id
        ORDER BY r.last_name, r.first_name
    """)


def main():
    dry_run = "--dry-run" in sys.argv

    # Auto-delete: clearly bad records
    bad = find_bad_name_researchers()
    logger.info("Found %d researchers with bad names:", len(bad))
    for r in bad:
        logger.info("  [%d] '%s' '%s' (%d pubs)", r["id"], r["first_name"], r["last_name"], r["pub_count"])

    # Manual review: suspicious but not auto-deleted
    suspicious = find_suspicious_researchers()
    # Exclude already-found bad records
    bad_ids = {r["id"] for r in bad}
    suspicious = [r for r in suspicious if r["id"] not in bad_ids]
    if suspicious:
        logger.info("\nSuspicious names (manual review, NOT auto-deleted):")
        for r in suspicious:
            logger.info("  [%d] '%s' '%s' (%d pubs)", r["id"], r["first_name"], r["last_name"], r["pub_count"])

    if dry_run:
        logger.info("\nDry run -- no deletions made. Remove --dry-run to delete.")
        return

    if not bad:
        logger.info("No bad name researchers to delete.")
        return

    ids = [r["id"] for r in bad]
    placeholders = ",".join(["%s"] * len(ids))
    Database.execute_query(
        f"DELETE FROM researchers WHERE id IN ({placeholders})",
        tuple(ids),
    )
    logger.info("\nDeleted %d researchers with bad names (CASCADE cleaned child rows)", len(ids))


if __name__ == "__main__":
    main()
