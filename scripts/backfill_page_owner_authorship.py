#!/usr/bin/env python3
"""Backfill: add page owner as author on papers scraped from their page.

Finds papers linked to a researcher's URL (via paper_urls + researcher_urls)
where that researcher is not in the authorship table, and inserts them
with author_order=0.

Safe to run multiple times — uses INSERT IGNORE.
"""
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FIND_MISSING_OWNERS_SQL = """
    SELECT DISTINCT r_owner.id AS owner_id, p.id AS paper_id
    FROM papers p
    JOIN paper_urls pu ON pu.paper_id = p.id
    JOIN researcher_urls ru ON ru.url = pu.url
    JOIN researchers r_owner ON r_owner.id = ru.researcher_id
    WHERE r_owner.id NOT IN (
        SELECT a.researcher_id FROM authorship a WHERE a.publication_id = p.id
    )
"""

INSERT_AUTHORSHIP_SQL = """
    INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order)
    VALUES (%s, %s, 0)
"""


def main():
    parser = argparse.ArgumentParser(description="Backfill page owner authorship")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be inserted without writing")
    args = parser.parse_args()

    conn = Database.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(FIND_MISSING_OWNERS_SQL)
        rows = cursor.fetchall()
        logger.info(f"Found {len(rows)} missing page-owner authorship records")

        if args.dry_run:
            for owner_id, paper_id in rows[:20]:
                logger.info(f"  Would insert: researcher_id={owner_id}, paper_id={paper_id}")
            if len(rows) > 20:
                logger.info(f"  ... and {len(rows) - 20} more")
            return

        inserted = 0
        for owner_id, paper_id in rows:
            cursor.execute(INSERT_AUTHORSHIP_SQL, (owner_id, paper_id))
            inserted += cursor.rowcount

        conn.commit()
        logger.info(f"Inserted {inserted} authorship records ({len(rows) - inserted} already existed)")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
