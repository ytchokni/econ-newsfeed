"""Recompute researchers.has_top5_pub under the exact five-journal rule.

A researcher "has a top-5" when they author a published or accepted paper in
American Economic Review, Econometrica, Journal of Political Economy, Quarterly
Journal of Economics or Review of Economic Studies (backend/database/top_presets.py).
Sister journals, look-alike titles and R&R/working papers no longer count.

Dry run by default: prints the before/after counts and examples. --apply writes.

    poetry run python scripts/recompute_has_top5.py
    poetry run python scripts/recompute_has_top5.py --apply

In production (read-only dry run first, then --apply):

    docker compose exec -T api python scripts/recompute_has_top5.py
    docker compose exec -T api python scripts/recompute_has_top5.py --apply

Idempotent. The scrape job also runs the same recompute after each paper merge.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.database.connection import fetch_all  # noqa: E402
from backend.database.researchers import (  # noqa: E402
    has_top5_flag_changes,
    refresh_all_has_top5,
)
from backend.database.top_presets import (  # noqa: E402
    top5_publication_clause,
    top5_venue_clause,
)

_TRACKED = "EXISTS (SELECT 1 FROM researcher_urls ru WHERE ru.researcher_id = r.id)"


def _cleared_breakdown(examples: int) -> tuple[dict, list[dict]]:
    """Why each flag would be cleared: no top-5 venue at all, or only R&R/WP."""
    pub_clause, pub_params = top5_publication_clause("p.venue", "p.status")
    venue_clause, venue_params = top5_venue_clause("p.venue")
    rows = fetch_all(
        f"""SELECT r.id, r.first_name, r.last_name, {_TRACKED} AS tracked,
                   EXISTS (SELECT 1 FROM authorship a JOIN papers p ON p.id = a.publication_id
                           WHERE a.researcher_id = r.id AND {venue_clause}) AS has_top5_venue
            FROM researchers r
            WHERE r.has_top5_pub
              AND NOT EXISTS (SELECT 1 FROM authorship a JOIN papers p ON p.id = a.publication_id
                              WHERE a.researcher_id = r.id AND {pub_clause})""",
        (*venue_params, *pub_params),
    )
    summary = {
        "sister_or_lookalike_venue_only": sum(1 for r in rows if not r["has_top5_venue"]),
        "sister_or_lookalike_venue_only_tracked": sum(
            1 for r in rows if not r["has_top5_venue"] and r["tracked"]),
        "top5_venue_but_not_published_or_accepted": sum(1 for r in rows if r["has_top5_venue"]),
        "top5_venue_but_not_published_or_accepted_tracked": sum(
            1 for r in rows if r["has_top5_venue"] and r["tracked"]),
    }
    tracked_rows = [r for r in rows if r["tracked"]][:examples]
    return summary, tracked_rows


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the recomputed flags")
    parser.add_argument("--examples", type=int, default=15,
                        help="tracked researchers to list whose flag is cleared")
    args = parser.parse_args(argv)

    changes = has_top5_flag_changes()
    breakdown, examples = _cleared_breakdown(args.examples)
    print("has_top5_pub, all researchers:     "
          f"{changes['flagged_before']} -> {changes['flagged_after']}")
    print("has_top5_pub, tracked researchers: "
          f"{changes['tracked_before']} -> {changes['tracked_after']}")
    print(f"cleared: {changes['cleared']}, newly set: {changes['newly_set']}")
    for key, value in breakdown.items():
        print(f"  {key}: {value}")
    for r in examples:
        reason = "R&R/WP only" if r["has_top5_venue"] else "no top-5 venue"
        print(f"  - {r['id']} {r['first_name']} {r['last_name']} ({reason})")

    result = {**changes, **breakdown, "applied": False}
    if args.apply:
        result["updated"] = refresh_all_has_top5()
        result["applied"] = True
        print(f"Applied: {result['updated']} researchers updated.")
    else:
        print("Dry run: nothing written. Re-run with --apply to update.")
    return result


if __name__ == "__main__":
    main()
