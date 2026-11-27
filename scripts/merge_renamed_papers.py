"""Merge renamed papers that share an SSRN/NBER/arXiv/DOI link (issue #265).

Dry-run by default. A pair merges only when both papers link the same strong
identifier, the author sets are equal or nested, no identifier conflicts, the
titles are not translations, and no stored page (current or archived) lists
both titles as separate entries. The canonical row keeps the oldest ID, its
first new_paper event, the highest status and the most recent title. Refused
pairs are printed for review.

Run scripts/reverify_openalex_ids.py --apply first: identifiers it clears are
not used as evidence here.

    poetry run python scripts/merge_renamed_papers.py            # dry run
    poetry run python scripts/merge_renamed_papers.py --apply
"""
import argparse
import collections
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.database import fetch_all  # noqa: E402
from backend.enrichment.paper_merge import merge_paper_group  # noqa: E402
from backend.enrichment.rename_merge import find_rename_groups  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _titles(ids):
    rows = fetch_all(
        f"SELECT id, title, status, discovered_at FROM papers WHERE id IN ({','.join(['%s'] * len(ids))})",
        tuple(ids),
    )
    return {r["id"]: r for r in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually merge (default: dry run)")
    parser.add_argument("--show-refused", action="store_true", help="List every refused pair, not only counts")
    args = parser.parse_args()

    try:
        from backend.enrichment.identifier_verification import count_papers_to_verify
        remaining = count_papers_to_verify()
        if remaining:
            logger.warning("%d pre-#244 OpenAlex identifiers are not re-verified yet "
                           "(scripts/reverify_openalex_ids.py); their links still count as evidence", remaining)
    except Exception:
        logger.warning("openalex_identifier_checks is missing; run create_tables() first")

    groups, refused = find_rename_groups()
    ids = sorted({pid for g in groups for pid in g} | {pid for r in refused for pid in r["paper_ids"]})
    papers = _titles(ids) if ids else {}

    merged = 0
    for group in groups:
        print(f"\n--- Rename group {group} ---")
        for pid in group:
            p = papers.get(pid, {})
            print(f"  [{pid}] {p.get('title')!r}  status={p.get('status')}  discovered={p.get('discovered_at')}")
        if args.apply:
            try:
                if merge_paper_group(group, rename=True):
                    merged += 1
                else:
                    logger.warning("Merge refused at write time for %s", group)
            except Exception:
                logger.exception("Merge failed for %s", group)

    reasons = collections.Counter(r["reason"] for r in refused)
    if args.show_refused:
        for r in refused:
            a, b = r["paper_ids"][0], r["paper_ids"][-1]
            print(f"REFUSED {r['reason']:26} {r['key']}  [{a}] {papers.get(a, {}).get('title')!r}"
                  f"  /  [{b}] {papers.get(b, {}).get('title')!r}")
    print()
    if args.apply:
        logger.info("Merged %d of %d rename groups", merged, len(groups))
    else:
        logger.info("Would merge %d rename groups (%d papers -> %d)", len(groups),
                    sum(len(g) for g in groups), len(groups))
    logger.info("Refused pairs (left for review): %s", dict(reasons) or 0)


if __name__ == "__main__":
    main()
