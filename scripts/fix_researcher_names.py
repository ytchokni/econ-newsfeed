"""One-time fix: normalize capitalization of existing researcher names.

Dry-run by default. Pass --apply to write changes.

Usage:
    poetry run python scripts/fix_researcher_names.py          # dry-run
    poetry run python scripts/fix_researcher_names.py --apply  # write to DB
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database.connection import fetch_all, execute_query
from backend.database.researchers import normalize_name_case


def main():
    apply = "--apply" in sys.argv

    rows = fetch_all("SELECT id, first_name, last_name FROM researchers", ())
    fixes = []
    for r in rows:
        new_first = normalize_name_case(r["first_name"])
        new_last = normalize_name_case(r["last_name"])
        if new_first != r["first_name"] or new_last != r["last_name"]:
            fixes.append((r["id"], r["first_name"], r["last_name"], new_first, new_last))

    if not fixes:
        print("No names need fixing.")
        return

    print(f"{'APPLYING' if apply else 'DRY RUN'}: {len(fixes)} names to fix\n")
    for rid, old_first, old_last, new_first, new_last in fixes:
        print(f"  [{rid}] {old_first} {old_last}  →  {new_first} {new_last}")
        if apply:
            execute_query(
                "UPDATE researchers SET first_name = %s, last_name = %s WHERE id = %s",
                (new_first, new_last, rid),
            )

    if apply:
        print(f"\nDone — updated {len(fixes)} researchers.")
    else:
        print(f"\nDry run — pass --apply to write changes.")


if __name__ == "__main__":
    main()
