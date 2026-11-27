"""Preview or apply the duplicate-paper merges that merge_duplicate_papers runs.

Covers both identifier groups (shared DOI/OpenAlex ID) and title variants with
an identical author set: spelling variants (hyphen/space, accents, PDF spacing
artifacts) and a revised sample period ("..., 1957-2020" vs "..., 1957-2021").
The scheduler runs the same merge after every scrape job; this script is for
reviewing the backlog or merging it without waiting. Dry-run by default.

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
    _compatible_identity,
    _connected_groups,
    find_duplicate_groups,
    find_fuzzy_duplicate_groups,
    merge_paper_group,
)
from backend.database import fetch_all  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually merge (default: dry run)")
    args = parser.parse_args()

    groups = _connected_groups(find_duplicate_groups() + find_fuzzy_duplicate_groups())
    if not groups:
        logger.info("No duplicate paper groups found.")
        return

    logger.info("Found %d candidate duplicate groups", len(groups))
    mergeable = refused = 0
    for group in groups:
        papers = fetch_all(
            f"""SELECT p.id, p.title, p.doi, p.openalex_id, p.status, p.venue, p.discovered_at,
                       GROUP_CONCAT(a.researcher_id ORDER BY a.researcher_id) AS authors
                FROM papers p
                LEFT JOIN authorship a ON a.publication_id = p.id
                WHERE p.id IN ({','.join(['%s'] * len(group))})
                GROUP BY p.id, p.title, p.doi, p.openalex_id, p.status, p.venue, p.discovered_at
                ORDER BY p.discovered_at, p.id""",
            tuple(group),
        )
        for p in papers:
            p["author_ids"] = [int(x) for x in (p["authors"] or "").split(",") if x]
        ok = len(papers) == len(group) and all(
            _compatible_identity(a, b) for i, a in enumerate(papers) for b in papers[i + 1:]
        )
        mergeable += ok
        refused += not ok
        print(f"\n--- Group {group} ({'merge' if ok else 'REFUSED: identity not verified'}) ---")
        for p in papers:
            print(f"  [{p['id']}] {p['title']}")
            print(f"       status={p['status']}  venue={p['venue']}  doi={p['doi']}  "
                  f"authors={p['authors']}  discovered={p['discovered_at']}")

        if ok and args.apply:
            if merge_paper_group(group):
                logger.info("Merged group %s", group)

    action = "Merged" if args.apply else "Would merge"
    logger.info("%s %d groups; %d refused (left for review)", action, mergeable, refused)


if __name__ == "__main__":
    main()
