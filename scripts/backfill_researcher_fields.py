#!/usr/bin/env python3
"""Backfill researcher_fields from existing researcher_jel_codes.

Run once after deploying the JEL-to-field sync feature to populate
fields for all researchers who already have JEL codes assigned.

Usage: poetry run python scripts/backfill_researcher_fields.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from database import Database
from database.jel import sync_researcher_fields_from_jel

logger = logging.getLogger(__name__)


def backfill() -> int:
    Database.create_tables()

    rows = Database.fetch_all(
        """SELECT r.id, GROUP_CONCAT(rjc.jel_code) AS codes
           FROM researchers r
           JOIN researcher_jel_codes rjc ON rjc.researcher_id = r.id
           GROUP BY r.id"""
    )
    updated = 0
    for row in rows:
        codes = row["codes"].split(",") if row["codes"] else []
        if codes:
            sync_researcher_fields_from_jel(row["id"], codes)
            updated += 1
            if updated % 50 == 0:
                logger.info("Processed %d/%d researchers", updated, len(rows))

    logger.info("Backfilled researcher_fields for %d researchers", updated)
    return updated


if __name__ == "__main__":
    backfill()
