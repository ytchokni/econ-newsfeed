"""Re-check DOIs/OpenAlex IDs set before the #244 title check (issue #266).

Dry-run by default: fetches the works and prints what would change. Fetching
spends the shared OpenAlex daily budget (one request per 50 papers), so works
are cached in --cache and the following --apply run reuses them. Resumable:
every applied decision is stored in openalex_identifier_checks and skipped next
time. The enrichment worker usually spends the whole budget early in the UTC
day, so run shortly after 00:00 UTC and cap the share with --max-requests.

    poetry run python scripts/reverify_openalex_ids.py --status
    poetry run python scripts/reverify_openalex_ids.py --max-requests 20       # dry run
    poetry run python scripts/reverify_openalex_ids.py --max-requests 300 --apply
    poetry run python scripts/reverify_openalex_ids.py --refused-groups        # #251's refused merges
    poetry run python scripts/reverify_openalex_ids.py --paper-ids 135479,56037 --apply
"""
import argparse
import collections
import csv
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.database import fetch_all  # noqa: E402
from backend.enrichment import identifier_verification as iv  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _load_cache(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(path, cache):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(cache, fh)
    os.replace(tmp, path)


def _papers_by_ids(ids):
    if not ids:
        return []
    return fetch_all(
        f"""SELECT id, title, doi, openalex_id, abstract FROM papers
            WHERE openalex_id IS NOT NULL AND id IN ({','.join(['%s'] * len(ids))}) ORDER BY id""",
        tuple(ids),
    )


def _refused_group_ids():
    """Papers in shared-ID groups that #251's merge refuses (titles differ)."""
    from backend.enrichment.paper_merge import _compatible_identity, find_duplicate_groups

    ids = set()
    for group in find_duplicate_groups():
        papers = fetch_all(
            f"""SELECT p.id, p.title, p.doi, p.openalex_id,
                       GROUP_CONCAT(a.researcher_id) AS authors
                FROM papers p LEFT JOIN authorship a ON a.publication_id = p.id
                WHERE p.id IN ({','.join(['%s'] * len(group))})
                GROUP BY p.id, p.title, p.doi, p.openalex_id""",
            tuple(group),
        )
        for p in papers:
            p["author_ids"] = [int(x) for x in (p["authors"] or "").split(",") if x]
        if not all(_compatible_identity(a, b) for i, a in enumerate(papers) for b in papers[i + 1:]):
            ids.update(group)
    return sorted(ids)


def _status():
    rows = fetch_all(
        """SELECT verdict, reason, COUNT(*) AS n FROM openalex_identifier_checks
           GROUP BY verdict, reason ORDER BY verdict, n DESC"""
    )
    for row in rows:
        print(f"  {row['verdict']:9} {row['reason']:32} {row['n']}")
    print(f"  remaining pre-{iv.CUTOFF} identifiers to check: {iv.count_papers_to_verify()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Record decisions and clear wrong IDs (default: dry run)")
    parser.add_argument("--max-requests", type=int, default=100,
                        help="Budgeted OpenAlex requests to spend this run (50 papers each; default 100)")
    parser.add_argument("--cache", default=os.environ.get("REVERIFY_CACHE", "/tmp/reverify_openalex_works.json"),
                        help="JSON cache of fetched works shared by dry run and --apply")
    parser.add_argument("--report", help="Write a CSV of every cleared/review decision")
    parser.add_argument("--paper-ids", help="Comma-separated paper IDs to check regardless of date")
    parser.add_argument("--refused-groups", action="store_true",
                        help="Check the papers in shared-ID groups that #251's merge refuses")
    parser.add_argument("--status", action="store_true", help="Show recorded decisions and what is left")
    args = parser.parse_args()

    if args.status:
        _status()
        return

    if args.paper_ids:
        explicit = _papers_by_ids(sorted({int(x) for x in args.paper_ids.split(",") if x.strip()}))
    elif args.refused_groups:
        explicit = _papers_by_ids(_refused_group_ids())
    else:
        explicit = None

    cache = _load_cache(args.cache)
    counts = collections.Counter()
    report = []
    requests_left = args.max_requests
    after_id = 0
    while True:
        if explicit is not None:
            batch, explicit = explicit[:iv.BATCH_SIZE], explicit[iv.BATCH_SIZE:]
        else:
            batch = iv.get_papers_to_verify(iv.BATCH_SIZE, after_id)
        if not batch:
            break
        after_id = batch[-1]["id"]
        uncached = [p["openalex_id"] for p in batch if p["openalex_id"] not in cache]
        if uncached and requests_left <= 0:
            logger.info("Request cap reached; rerun to continue")
            break
        try:
            works = iv.fetch_works([p["openalex_id"] for p in batch], cache)
        except iv.BudgetExhausted:
            logger.info("OpenAlex daily budget exhausted; rerun after 00:00 UTC")
            break
        finally:
            _save_cache(args.cache, cache)
        requests_left -= 1 if uncached else 0
        for paper, work, verdict in iv.plan_batch(batch, works):
            counts[(verdict.verdict, verdict.reason)] += 1
            if verdict.verdict != "verified":
                title = iv.openalex._parse_work(work)["title"] if work else None
                report.append({
                    "paper_id": paper["id"], "verdict": verdict.verdict, "reason": verdict.reason,
                    "openalex_id": paper["openalex_id"], "doi": paper["doi"],
                    "title": paper["title"], "work_title": title,
                    "matched_paper_id": verdict.matched_paper_id,
                })
                print(f"{verdict.verdict.upper():8} {verdict.reason:30} [{paper['id']}] {paper['title'][:70]!r}"
                      f"  ->  {paper['openalex_id']} {paper['doi']} {str(title)[:70]!r}")
            if args.apply:
                iv.apply_verdict(paper, work, verdict)

    print()
    action = "Applied" if args.apply else "Would apply"
    for (verdict, reason), n in sorted(counts.items()):
        print(f"  {action}: {verdict:9} {reason:32} {n}")
    total_cleared = sum(n for (v, _), n in counts.items() if v == "cleared")
    print(f"  {sum(counts.values())} checked, {total_cleared} cleared, "
          f"{sum(n for (v, _), n in counts.items() if v == 'review')} for review")
    if args.report and report:
        with open(args.report, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(report[0]))
            writer.writeheader()
            writer.writerows(report)
        print(f"  Report written to {args.report}")


if __name__ == "__main__":
    main()
