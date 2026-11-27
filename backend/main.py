import argparse
import json
import logging

from backend.database import (
    create_tables,
    fetch_all,
    fetch_one,
    get_urls_needing_extraction,
    import_data_from_file,
)
from backend.researcher import Researcher


from backend.pipeline.html_fetcher import HTMLFetcher

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def import_data(file_path: str) -> None:
    """Import data from a file into the database."""
    import_data_from_file(file_path)
    logging.info(f"Data imported from {file_path}")

def download_htmls() -> None:
    """Download HTML content for all URLs in the researcher_urls table."""
    from backend.pipeline.scheduler import create_scrape_log, update_scrape_log, _acquire_db_lock, _release_db_lock

    # Hold the scrape advisory lock: it prevents collisions with a scheduled
    # scrape, and the zombie cleanup treats lock-less 'running' rows as dead.
    lock_conn = _acquire_db_lock()
    if lock_conn is None:
        logging.warning("Another scrape is running (advisory lock held) — aborting")
        return

    log_id = create_scrape_log()
    researcher_urls = Researcher.get_all_researcher_urls()
    urls_checked = 0
    urls_changed = 0

    try:
        for row in researcher_urls:
            id, researcher_id, url, page_type = row['id'], row['researcher_id'], row['url'], row['page_type']
            urls_checked += 1
            changed = HTMLFetcher.fetch_and_save_if_changed(id, url, researcher_id)
            if changed:
                urls_changed += 1
        update_scrape_log(log_id, "completed", urls_checked, urls_changed)
    except Exception as e:
        logging.error(f"Download failed: {e}")
        update_scrape_log(log_id, "failed", urls_checked, urls_changed, error_message=str(e))
    finally:
        _release_db_lock(lock_conn)


def classify_jel(
    max_calls: int | None = None,
    daily_limit: int | None = None,
    delay: float | None = None,
    researcher_ids: list[int] | None = None,
    force: bool = False,
    until_done: bool = False,
    yield_to_extraction: bool = True,
) -> None:
    """Classify tracked researchers into JEL codes (bio + papers + OpenAlex topics).

    Uses the same incremental selection and budget as the scheduled job.
    ``until_done`` repeats batches until nothing is due or a limit is hit.
    """
    from backend.database import get_jel_coverage_stats
    from backend.enrichment.jel_job import run_jel_classification_batch

    create_tables()
    totals: dict[str, int] = {}
    while True:
        stats = run_jel_classification_batch(
            max_calls=max_calls, daily_limit=daily_limit, delay_seconds=delay,
            researcher_ids=researcher_ids, force=force,
            yield_to_extraction=yield_to_extraction,
        )
        for key, value in stats.items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
        # Every considered researcher leaves the due set (classified, marked
        # checked, or queued behind the call budget), so repeating converges.
        # Daily limit, quota refusals, failures and an empty queue end the run.
        if (not until_done or force or researcher_ids or stats["considered"] == 0
                or stats["stopped"] not in ("done", "call_budget")):
            break
    totals.pop("calls_today_before", None)
    totals.pop("extraction_backlog", None)
    logging.info("JEL classification finished (%s): %s", stats["stopped"], totals)
    logging.info("JEL coverage now: %s", get_jel_coverage_stats())


def extract_only(limit: int | None = None) -> None:
    """Run LLM extraction for URLs with pending content changes (skips fetching)."""
    from backend.pipeline.scheduler import (
        create_scrape_log, update_scrape_log, _update_progress,
        _acquire_db_lock, _release_db_lock,
    )
    from backend.pipeline.extraction import extract_one_url

    pending = get_urls_needing_extraction()
    if not pending:
        logging.info("No URLs need extraction")
        return

    if limit:
        logging.info("Extracting %d of %d pending URLs", limit, len(pending))
        pending = pending[:limit]
    else:
        logging.info("Extracting %d pending URLs", len(pending))

    # Hold the scrape advisory lock: the zombie cleanup treats lock-less
    # 'running' rows as dead, and a concurrent fetch would race extraction.
    lock_conn = _acquire_db_lock()
    if lock_conn is None:
        logging.warning("Another scrape is running (advisory lock held) — aborting")
        return

    log_id = create_scrape_log()
    pubs_extracted = 0
    extraction_errors = 0
    consecutive_failures = 0

    try:
        for idx, row in enumerate(pending):
            try:
                outcome = extract_one_url(row, scrape_log_id=log_id)
            except Exception as e:
                logging.error("Error extracting %s: %s", row['url'], e)
                extraction_errors += 1
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    logging.warning("Circuit breaker: 10 consecutive failures")
                    break
                continue

            logging.info("[%d/%d] extract %s — %s (%d pubs)",
                         idx + 1, len(pending), row['url'], outcome.status, outcome.pubs_count)

            if outcome.ok:
                consecutive_failures = 0
                pubs_extracted += outcome.pubs_count
            else:
                consecutive_failures += 1
                extraction_errors += 1
                if consecutive_failures >= 10:
                    logging.warning("Circuit breaker: 10 consecutive extraction failures")
                    break

            _update_progress(log_id, pubs_extracted=pubs_extracted, extraction_errors=extraction_errors)

        update_scrape_log(log_id, "completed", urls_checked=0, urls_changed=len(pending),
                          pubs_extracted=pubs_extracted, extraction_errors=extraction_errors)
        logging.info("Extraction done: %d pubs from %d URLs (%d errors)",
                     pubs_extracted, len(pending), extraction_errors)
    except Exception as e:
        logging.error("Extraction failed: %s", e)
        update_scrape_log(log_id, "failed", error_message=str(e))
    finally:
        _release_db_lock(lock_conn)


def discover_domains() -> None:
    """Scan all stored raw HTML to find untrusted domains that may host paper links."""
    from collections import Counter
    from backend.enrichment.link_extractor import discover_untrusted_domains

    # Fetch only url_ids first, then load raw HTML one at a time to avoid OOM
    url_ids = fetch_all(
        "SELECT url_id FROM html_content WHERE raw_html IS NOT NULL"
    )
    if not url_ids:
        logging.info("No raw HTML stored yet. Run 'python -m backend.main download' first.")
        return

    totals = Counter()
    for row in url_ids:
        html_row = fetch_one(
            "SELECT raw_html FROM html_content WHERE url_id = %s", (row['url_id'],)
        )
        if html_row and html_row['raw_html']:
            domains = discover_untrusted_domains(html_row['raw_html'])
            totals.update(domains)

    if not totals:
        logging.info("No untrusted domains with paper-title-length anchors found.")
        return

    print(f"\nUntrusted domains with paper-title-length anchor text ({len(totals)} domains):\n")
    for domain, count in totals.most_common(30):
        print(f"  {count:4d}x  {domain}")
    print(f"\nTo add a domain, append it to TRUSTED_LINK_DOMAINS in link_extractor.py")

def main() -> None:
    """CLI entrypoint — non-interactive, safe for cloud/container environments."""
    parser = argparse.ArgumentParser(description='Econ Newsfeed scraper CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)

    import_parser = subparsers.add_parser('import', help='Import researcher URLs from a CSV file')
    import_parser.add_argument('file_path', help='Path to the CSV file to import')

    subparsers.add_parser('download', help='Download HTML content for all researcher URLs')
    jel_parser = subparsers.add_parser('classify-jel', help='Classify tracked researchers into JEL codes (bio, papers, OpenAlex topics)')
    jel_parser.add_argument('--max-calls', type=int, default=None, help='Max LLM calls per batch (default: JEL_CLASSIFICATION_BATCH_CALLS)')
    jel_parser.add_argument('--daily-limit', type=int, default=None, help='Max JEL LLM calls per UTC day (default: JEL_CLASSIFICATION_DAILY_LIMIT)')
    jel_parser.add_argument('--delay', type=float, default=None, help='Seconds between LLM calls (default: JEL_CLASSIFICATION_DELAY_SECONDS)')
    jel_parser.add_argument('--researcher-id', type=int, action='append', default=None, help='Only these researchers (repeatable)')
    jel_parser.add_argument('--force', action='store_true', help='Ignore the due checks: reclassify every selected researcher')
    jel_parser.add_argument('--no-yield', action='store_true', help='Run even if extraction was quota-limited recently or its backlog is large')
    jel_parser.add_argument('--until-done', action='store_true', help='Repeat batches until nothing is due or the daily limit is reached')
    bio_parser = subparsers.add_parser('backfill-bios', help='Regenerate missing researcher bios from stored HOME pages (paced)')
    bio_parser.add_argument('--max-calls', type=int, default=None, help='Max LLM calls (default: BIO_BACKFILL_BATCH_CALLS)')
    bio_parser.add_argument('--daily-limit', type=int, default=None, help='Max backfill calls per UTC day (default: BIO_BACKFILL_DAILY_LIMIT)')
    bio_parser.add_argument('--delay', type=float, default=None, help='Seconds between LLM calls (default: BIO_BACKFILL_DELAY_SECONDS)')
    bio_parser.add_argument('--max-backlog', type=int, default=None, help='Skip while more pages wait for extraction (default: BIO_BACKFILL_MAX_BACKLOG; -1 disables)')
    subparsers.add_parser('enrich', help='Enrich publications with OpenAlex metadata')
    subparsers.add_parser('enrich-jel', help='Enrich researcher JEL codes from paper topics via OpenAlex')
    subparsers.add_parser('discover-domains', help='Scan stored HTML for untrusted domains with paper-title links')
    extract_parser = subparsers.add_parser('extract', help='Run LLM extraction for URLs with pending changes (no fetching)')
    extract_parser.add_argument('--limit', type=int, default=None, help='Max URLs to extract')
    newsletter_parser = subparsers.add_parser('ingest-newsletters', help='Ingest papers from newsletter emails')
    newsletter_parser.add_argument('--max-emails', type=int, default=50, help='Max emails to process')
    discover_parser = subparsers.add_parser('discover-urls', help='Search for personal websites for researchers without URLs')
    discover_parser.add_argument('--limit', type=int, default=None, help='Max researchers to search (default: DISCOVERY_DAILY_LIMIT)')
    mining_parser = subparsers.add_parser('mine-urls', help='Mine cached links and department directories without paid search')
    mining_parser.add_argument('--apply', action='store_true', help='Persist queue/checkpoints and add verified crawl URLs (default: dry run)')
    mining_parser.add_argument('--mode', choices=['all', 'cached', 'directories'], default='all')
    mining_parser.add_argument('--source-limit', type=int, default=None)
    mining_parser.add_argument('--limit', type=int, default=None, help='Maximum candidates to verify')
    mining_parser.add_argument('--max-requests', type=int, default=None)
    mining_parser.add_argument('--max-seconds', type=int, default=None)
    mining_parser.add_argument('--status', action='store_true', help='Read recent runs, queue counts and held candidates; no network requests')

    top5_parser = subparsers.add_parser('top5-coverage', help='Compare top-5 journal articles (Crossref) with our papers; store the result for /about')
    top5_parser.add_argument('--dry-run', action='store_true', help='Print the summary without storing it')
    top5_parser.add_argument('--force', action='store_true', help='Run even if a result from the last week is stored')

    review_parser = subparsers.add_parser('review', help='LLM quality review of feed events (GPT 5.4 Mini)')
    review_parser.add_argument('--limit', type=int, default=None, help='Max events to review')
    review_parser.add_argument('--batch-size', type=int, default=100, help='Batch size (default: 100)')
    review_parser.add_argument('--dry-run', action='store_true', help='Show corrections without applying')

    args = parser.parse_args()

    if args.command == 'import':
        import_data(args.file_path)
    elif args.command == 'download':
        download_htmls()
    elif args.command == 'classify-jel':
        classify_jel(max_calls=args.max_calls, daily_limit=args.daily_limit,
                     delay=args.delay, researcher_ids=args.researcher_id,
                     force=args.force, until_done=args.until_done,
                     yield_to_extraction=not args.no_yield)
    elif args.command == 'backfill-bios':
        create_tables()
        from backend.pipeline.bio_backfill import run_bio_backfill_batch
        print(run_bio_backfill_batch(max_calls=args.max_calls, daily_limit=args.daily_limit,
                                     delay_seconds=args.delay, max_backlog=args.max_backlog))
    elif args.command == 'enrich':
        create_tables()
        from backend.enrichment.openalex import enrich_new_publications
        enrich_new_publications(limit=500)
    elif args.command == 'enrich-jel':
        create_tables()
        from backend.enrichment.jel_enrichment import enrich_jel_from_papers
        enrich_jel_from_papers()
    elif args.command == 'extract':
        extract_only(limit=args.limit)
    elif args.command == 'discover-domains':
        discover_domains()
    elif args.command == 'ingest-newsletters':
        from backend.pipeline.newsletter_ingest import ingest_newsletters
        ingest_newsletters(max_emails=args.max_emails)
    elif args.command == 'discover-urls':
        create_tables()
        from backend.discovery.engine import run_discovery_batch
        result = run_discovery_batch(limit=args.limit)
        logging.info("Discovery results: %s", result)
    elif args.command == 'mine-urls':
        if args.status:
            from backend.discovery.mining_store import MiningStore
            with MiningStore(apply=False) as store:
                result = store.status(limit=args.limit or 30)
        else:
            from backend.discovery.mining import run_site_mining_batch
            result = run_site_mining_batch(apply=args.apply, mode=args.mode,
                source_limit=args.source_limit, candidate_limit=args.limit,
                max_requests=args.max_requests, max_seconds=args.max_seconds)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    elif args.command == 'top5-coverage':
        create_tables()
        from backend.coverage.top5_job import run_top5_coverage_job
        summary = run_top5_coverage_job(force=args.force or args.dry_run, store=not args.dry_run)
        print(json.dumps((summary or {}).get('overall'), indent=2))
    elif args.command == 'review':
        create_tables()
        from backend.enrichment.quality_review import review_events
        result = review_events(
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        logging.info(
            "Review done: %d events, %d issues, %d corrections (%s)",
            result["reviewed"], result["issues"], result["corrections"],
            "dry-run" if result["dry_run"] else "applied",
        )

if __name__ == "__main__":
    main()
