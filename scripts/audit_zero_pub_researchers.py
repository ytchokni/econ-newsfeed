"""Audit researchers with URLs but 0 publications.

Diagnoses why each researcher has no papers: never scraped, HTML but
no extraction, extraction returned 0 papers, etc.

Usage: poetry run python scripts/audit_zero_pub_researchers.py
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main():
    rows = Database.fetch_all("""
        SELECT r.id, r.first_name, r.last_name,
               ru.url,
               hc.id AS html_id,
               hc.timestamp AS fetched_at,
               hc.extracted_at,
               hc.content_hash,
               hc.extracted_hash
        FROM researchers r
        JOIN researcher_urls ru ON ru.researcher_id = r.id
        LEFT JOIN authorship a ON a.researcher_id = r.id
        LEFT JOIN html_content hc ON hc.url_id = ru.id
        WHERE a.researcher_id IS NULL
        ORDER BY r.last_name, r.first_name
    """)

    # Categorize
    never_fetched = []
    fetched_never_extracted = []
    extracted_zero_pubs = []

    for r in rows:
        name = f"{r['first_name']} {r['last_name']}"
        if r["html_id"] is None:
            never_fetched.append((r["id"], name, r["url"]))
        elif r["extracted_at"] is None:
            fetched_never_extracted.append((r["id"], name, r["url"], r["fetched_at"]))
        else:
            extracted_zero_pubs.append((r["id"], name, r["url"], r["extracted_at"]))

    logger.info("=== Zero-Publication Researcher Audit ===\n")

    logger.info("--- Never fetched (%d) ---", len(never_fetched))
    for rid, name, url in never_fetched:
        logger.info("  [%d] %s  %s", rid, name, url)

    logger.info("\n--- Fetched but never extracted (%d) ---", len(fetched_never_extracted))
    for rid, name, url, fetched in fetched_never_extracted:
        logger.info("  [%d] %s  %s  (fetched: %s)", rid, name, url, fetched)

    logger.info("\n--- Extracted but 0 papers found (%d) ---", len(extracted_zero_pubs))
    for rid, name, url, extracted in extracted_zero_pubs:
        logger.info("  [%d] %s  %s  (extracted: %s)", rid, name, url, extracted)

    logger.info("\n--- Summary ---")
    logger.info("  Never fetched:            %d", len(never_fetched))
    logger.info("  Fetched, never extracted:  %d", len(fetched_never_extracted))
    logger.info("  Extracted, 0 papers:       %d", len(extracted_zero_pubs))
    logger.info("  Total:                     %d", len(rows))


if __name__ == "__main__":
    main()
