"""One-time migration: merge fuzzy duplicate papers missed by the old matching.

The improved _title_similarity (content overlap coefficient + hyphen
normalization) catches title revisions the old word-level SequenceMatcher
missed.  Dry-run by default; pass --apply to write.

    poetry run python scripts/merge_fuzzy_duplicates.py            # dry run
    poetry run python scripts/merge_fuzzy_duplicates.py --apply
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.enrichment.paper_merge import (  # noqa: E402
    find_fuzzy_duplicate_groups,
    merge_paper_group,
    _title_similarity,
)
from backend.database import fetch_all  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually merge (default: dry run)")
    args = parser.parse_args()

    groups = find_fuzzy_duplicate_groups()
    if not groups:
        logger.info("No fuzzy duplicate groups found.")
        return

    logger.info("Found %d fuzzy duplicate groups", len(groups))

    for group in groups:
        papers = fetch_all(
            f"""SELECT p.id, p.title, p.status, p.venue, p.discovered_at,
                       GROUP_CONCAT(a.researcher_id ORDER BY a.researcher_id) AS authors
                FROM papers p
                JOIN authorship a ON a.publication_id = p.id
                WHERE p.id IN ({','.join(['%s'] * len(group))})
                GROUP BY p.id, p.title, p.status, p.venue, p.discovered_at
                ORDER BY p.discovered_at""",
            tuple(group),
        )
        sim = _title_similarity(papers[0]["title"], papers[1]["title"]) if len(papers) >= 2 else 0
        print(f"\n--- Group (similarity={sim:.3f}) ---")
        for p in papers:
            print(f"  [{p['id']}] {p['title']}")
            print(f"       status={p['status']}  venue={p['venue']}  authors={p['authors']}  discovered={p['discovered_at']}")

        if args.apply:
            merge_paper_group(group)
            logger.info("Merged group %s → canonical %s", group, group[0])
        else:
            print("  (dry run — pass --apply to merge)")

    action = "Merged" if args.apply else "Would merge"
    logger.info("%s %d groups", action, len(groups))


if __name__ == "__main__":
    main()
