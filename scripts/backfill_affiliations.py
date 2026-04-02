"""Backfill researcher affiliations from OpenAlex author API.

For researchers with openalex_author_id but null affiliation, fetches
last_known_institution from OpenAlex and updates the record.

Usage:
    poetry run python scripts/backfill_affiliations.py [--dry-run]
"""
import logging
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from database import Database

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")


def fetch_author_affiliation(openalex_author_id: str) -> str | None:
    """Fetch last_known_institution.display_name from OpenAlex author API."""
    params = {}
    if MAILTO:
        params["mailto"] = MAILTO
    try:
        resp = requests.get(
            f"{OPENALEX_BASE}/authors/{openalex_author_id}",
            params=params,
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        institution = data.get("last_known_institutions") or []
        if institution:
            return institution[0].get("display_name")
        # Fallback to legacy field
        legacy = data.get("last_known_institution") or {}
        return legacy.get("display_name")
    except (requests.RequestException, ValueError) as e:
        logger.warning("Failed to fetch author A%s: %s", openalex_author_id, e)
        return None


def main():
    dry_run = "--dry-run" in sys.argv

    rows = Database.fetch_all("""
        SELECT id, first_name, last_name, openalex_author_id
        FROM researchers
        WHERE openalex_author_id IS NOT NULL
          AND (affiliation IS NULL OR TRIM(affiliation) = '')
        ORDER BY id
    """)

    logger.info("Found %d researchers with openalex_author_id but no affiliation", len(rows))

    updated = 0
    for i, r in enumerate(rows):
        affiliation = fetch_author_affiliation(r["openalex_author_id"])
        if affiliation:
            logger.info(
                "  [%d] %s %s → %s",
                r["id"], r["first_name"], r["last_name"], affiliation,
            )
            if not dry_run:
                Database.execute_query(
                    "UPDATE researchers SET affiliation = %s WHERE id = %s AND affiliation IS NULL",
                    (affiliation, r["id"]),
                )
            updated += 1
        else:
            logger.info(
                "  [%d] %s %s → no affiliation found",
                r["id"], r["first_name"], r["last_name"],
            )

        # Polite rate limiting for OpenAlex
        if (i + 1) % 10 == 0:
            time.sleep(1)

    logger.info(
        "\n%s %d/%d researcher affiliations",
        "Would update" if dry_run else "Updated",
        updated,
        len(rows),
    )


if __name__ == "__main__":
    main()
