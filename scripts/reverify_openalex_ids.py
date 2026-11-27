"""Re-check DOIs/OpenAlex IDs set before the #244 title check (issue #266).

Dry-run by default: fetches the works and prints what would change; --apply
records every decision in openalex_identifier_checks (resumable) and clears the
wrong identifiers, keeping a JSON backup that --undo restores.

Budget: about 900 filter requests (44,808 papers / 50 on 2026-11-27), charged to
the shared OpenAlex daily budget (OPENALEX_DAILY_BUDGET, 1000 in prod) that the
enrichment worker and #281's JEL topic fetches also spend. The worker uses the
whole budget early in each UTC day, so for the ~3 days of this job either set
ENRICHMENT_WORKER_ENABLED=false (and redeploy) or raise OPENALEX_DAILY_BUDGET,
then restore it. Fetched works are kept in the openalex_work_cache table, so the
dry run, --apply and reruns after a deploy pay once; --purge-cache when done.

    poetry run python scripts/reverify_openalex_ids.py --status
    poetry run python scripts/reverify_openalex_ids.py --max-requests 300 --report /tmp/r.csv  # dry run
    poetry run python scripts/reverify_openalex_ids.py --max-requests 300 --apply
    poetry run python scripts/reverify_openalex_ids.py --refused-groups        # #251's refused merges
    poetry run python scripts/reverify_openalex_ids.py --paper-ids 135479,56037 --apply
    poetry run python scripts/reverify_openalex_ids.py --undo --reason book_mismatch           # dry run
    poetry run python scripts/reverify_openalex_ids.py --undo --paper-ids 87182 --apply
    poetry run python scripts/reverify_openalex_ids.py --purge-cache
"""
import argparse
import collections
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.database import fetch_all  # noqa: E402
from backend.enrichment import identifier_verification as iv  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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


def _undo(args):
    if not args.paper_ids and not args.reason:
        raise SystemExit("--undo needs --paper-ids and/or --reason")
    ids = sorted({int(x) for x in (args.paper_ids or "").split(",") if x.strip()})
    candidates = iv.get_undo_candidates(ids or None, args.reason)
    outcomes = collections.Counter()
    for row in candidates:
        if args.apply:
            outcomes[iv.undo_clear(row["paper_id"], row["openalex_id"])] += 1
        else:
            print(f"  would restore [{row['paper_id']}] {row['openalex_id']} ({row['reason']})")
    if args.apply:
        print(f"  Undo: {dict(outcomes) or 'nothing to restore'}")
    else:
        print(f"  Would restore {len(candidates)} cleared identifiers (add --apply)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Record decisions and clear wrong IDs (default: dry run)")
    parser.add_argument("--max-requests", type=int, default=100,
                        help="Budgeted OpenAlex requests to spend this run (50 papers each; default 100)")
    parser.add_argument("--undo", action="store_true",
                        help="Restore cleared identifiers from their backup (with --paper-ids and/or --reason)")
    parser.add_argument("--reason", help="With --undo: only clears with this reason (e.g. book_mismatch)")
    parser.add_argument("--purge-cache", action="store_true", help="Delete the cached works (after the job is done)")
    parser.add_argument("--report", help="Write a CSV of every cleared/review decision")
    parser.add_argument("--paper-ids", help="Comma-separated paper IDs to check regardless of date")
    parser.add_argument("--refused-groups", action="store_true",
                        help="Check the papers in shared-ID groups that #251's merge refuses")
    parser.add_argument("--status", action="store_true", help="Show recorded decisions and what is left")
    args = parser.parse_args()

    if args.status:
        _status()
        return
    if args.purge_cache:
        print(f"  Deleted {iv.purge_work_cache()} cached works")
        return
    if args.undo:
        _undo(args)
        return

    if args.paper_ids:
        explicit = _papers_by_ids(sorted({int(x) for x in args.paper_ids.split(",") if x.strip()}))
    elif args.refused_groups:
        explicit = _papers_by_ids(_refused_group_ids())
    else:
        explicit = None

    cache = {}
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
        cache.update(iv.load_cached_works([p["openalex_id"] for p in batch if p["openalex_id"] not in cache]))
        uncached = [p["openalex_id"] for p in batch if p["openalex_id"] not in cache]
        if uncached and requests_left <= 0:
            logger.info("Request cap reached; rerun to continue")
            break
        try:
            works = iv.fetch_works([p["openalex_id"] for p in batch], cache, persist=True)
        except iv.BudgetExhausted:
            logger.info("OpenAlex daily budget exhausted; rerun after 00:00 UTC")
            break
        requests_left -= 1 if uncached else 0
        for paper, work, verdict in iv.plan_batch(batch, works):
            counts[(verdict.verdict, verdict.reason)] += 1
            if verdict.verdict != "verified":
                title = work["title"] if work else None
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
