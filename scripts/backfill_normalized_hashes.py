# scripts/backfill_normalized_hashes.py
"""One-time backfill: re-normalize and re-hash existing html_content rows.

After deploying normalize_text(), existing content_hash values are stale
(computed on un-normalized text). This script re-normalizes stored text,
recomputes hashes, and updates both content_hash and extracted_hash to
prevent a false-positive burst on the next scrape cycle.

Run: poetry run python scripts/backfill_normalized_hashes.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from database import Database
from html_fetcher import HTMLFetcher


def backfill_normalized_hashes():
    """Re-normalize and re-hash all html_content rows."""
    rows = Database.fetch_all(
        "SELECT id, url_id, content, content_hash, extracted_hash FROM html_content WHERE content IS NOT NULL"
    )
    logger.info("Found %d html_content rows to re-normalize", len(rows))

    updated = 0
    unchanged = 0

    for row in rows:
        content = row['content']
        old_hash = row['content_hash']

        normalized = HTMLFetcher.normalize_text(content)
        new_hash = HTMLFetcher.hash_text_content(normalized)

        if new_hash == old_hash:
            unchanged += 1
            continue

        # Update content (normalized), content_hash, and extracted_hash
        # Set extracted_hash = new_hash so pages aren't re-extracted unnecessarily.
        # Only update extracted_hash if it was previously equal to old content_hash
        # (meaning extraction was up-to-date before normalization).
        new_extracted_hash = new_hash if row['extracted_hash'] == old_hash else row['extracted_hash']

        Database.execute_query(
            """UPDATE html_content
               SET content = %s, content_hash = %s, extracted_hash = %s
               WHERE id = %s""",
            (normalized, new_hash, new_extracted_hash, row['id']),
        )
        updated += 1
        if updated % 100 == 0:
            logger.info("Progress: %d updated, %d unchanged", updated, unchanged)

    logger.info("Backfill complete: %d updated, %d unchanged (already normalized)", updated, unchanged)


if __name__ == "__main__":
    backfill_normalized_hashes()
