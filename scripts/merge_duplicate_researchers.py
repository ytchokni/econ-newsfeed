"""One-time script to find and merge duplicate researchers with initial-matching names.

Dry-run by default. Pass --execute to actually merge.

Usage:
    poetry run python scripts/merge_duplicate_researchers.py           # dry-run
    poetry run python scripts/merge_duplicate_researchers.py --execute  # merge
"""
import argparse
import logging
import sys

# Ensure project root is importable
sys.path.insert(0, ".")

from database.researchers import first_name_is_initial_match, merge_researchers
from database.connection import fetch_all, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def find_initial_match_pairs() -> list[tuple[dict, dict]]:
    """Find researcher pairs where first names are initial matches within the same last name."""
    researchers = fetch_all(
        "SELECT r.id, r.first_name, r.last_name, "
        "EXISTS(SELECT 1 FROM researcher_urls WHERE researcher_id = r.id) AS has_urls "
        "FROM researchers r ORDER BY r.last_name, r.id",
        (),
    )

    # Group by last_name
    groups: dict[str, list[dict]] = {}
    for r in researchers:
        groups.setdefault(r['last_name'], []).append(r)

    pairs = []
    for last_name, members in groups.items():
        if len(members) < 2:
            continue
        # Check all pairs within the group
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if first_name_is_initial_match(a['first_name'], b['first_name']):
                    # Canonical = has URLs, else lower ID
                    if b['has_urls'] and not a['has_urls']:
                        canonical, duplicate = b, a
                    elif a['has_urls'] and not b['has_urls']:
                        canonical, duplicate = a, b
                    else:
                        canonical, duplicate = (a, b) if a['id'] < b['id'] else (b, a)
                    pairs.append((canonical, duplicate))

    return pairs


def main():
    parser = argparse.ArgumentParser(description="Merge duplicate researchers with initial-matching names.")
    parser.add_argument("--execute", action="store_true", help="Actually merge (default is dry-run)")
    args = parser.parse_args()

    pairs = find_initial_match_pairs()

    if not pairs:
        logging.info("No duplicate researchers found.")
        return

    for canonical, duplicate in pairs:
        label = "MERGING" if args.execute else "WOULD MERGE"
        logging.info(
            f"{label}: #{duplicate['id']} ({duplicate['first_name']} {duplicate['last_name']}) "
            f"-> #{canonical['id']} ({canonical['first_name']} {canonical['last_name']})"
        )

    if not args.execute:
        logging.info(f"Dry run: {len(pairs)} pair(s) found. Re-run with --execute to merge.")
        return

    conn = get_connection()
    try:
        for canonical, duplicate in pairs:
            merge_researchers(canonical['id'], duplicate['id'], conn)
        logging.info(f"Merged {len(pairs)} duplicate researcher(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
