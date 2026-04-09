import argparse
import logging
import os

from database import Database
from researcher import Researcher
from publication import Publication, reconcile_title_renames
from html_fetcher import HTMLFetcher
from link_extractor import match_and_save_paper_links

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def import_data(file_path: str) -> None:
    """Import data from a file into the database."""
    Database.import_data_from_file(file_path)
    logging.info(f"Data imported from {file_path}")

def download_htmls() -> None:
    """Download HTML content for all URLs in the researcher_urls table."""
    from scheduler import create_scrape_log, update_scrape_log

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


def classify_jel() -> None:
    """Classify all researchers with descriptions into JEL codes."""
    from jel_classifier import classify_researcher

    researchers = Database.get_researchers_needing_classification()
    total = len(researchers)
    logging.info("JEL classification: %d researchers to classify", total)

    classified = 0
    for row in researchers:
        rid = row["id"]
        description = row["description"]
        first_name = row["first_name"]
        last_name = row["last_name"]

        codes = classify_researcher(rid, first_name, last_name, description)
        if codes:
            Database.save_researcher_jel_codes(rid, codes)
            classified += 1
            logging.info(
                "Saved JEL codes for %s %s (id=%d): %s",
                first_name, last_name, rid, ", ".join(codes),
            )

    logging.info("JEL classification done: %d/%d classified", classified, total)


def batch_submit() -> None:
    """Submit a batch job to the LLM provider's Batch API for all URLs needing extraction."""
    from llm_client import get_client, get_model
    import json
    import tempfile
    from datetime import datetime, timezone
    from publication import PublicationExtractionList

    client = get_client()
    model = get_model()

    researcher_urls = Researcher.get_all_researcher_urls()
    urls_to_process = [
        row for row in researcher_urls
        if HTMLFetcher.needs_extraction(row['id'])
    ]

    if not urls_to_process:
        logging.info("Nothing to extract")
        return

    # Warn if pending batches exist
    pending = Database.fetch_all(
        """SELECT openai_batch_id FROM batch_jobs
           WHERE status IN ('submitted','validating','in_progress','finalizing')"""
    )
    if pending:
        ids = ", ".join(r['openai_batch_id'] for r in pending)
        logging.warning("Warning: %d pending batch(es) already exist: %s", len(pending), ids)

    # Build JSONL
    lines = []
    for row in urls_to_process:
        url_id, researcher_id, url, page_type = row['id'], row['researcher_id'], row['url'], row['page_type']
        text = HTMLFetcher.get_latest_text(url_id)
        if not text:
            continue
        prompt = Publication.build_extraction_prompt(text, url)
        request = {
            "custom_id": f"url_{url_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "PublicationExtractionList",
                        "schema": PublicationExtractionList.model_json_schema(),
                        "strict": False,
                    },
                },
                "max_tokens": 8000,
            },
        }
        lines.append(json.dumps(request))

    if not lines:
        logging.info("No URLs with downloadable content to batch")
        return

    jsonl_content = "\n".join(lines).encode("utf-8")

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        tmp.write(jsonl_content)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            uploaded = client.files.create(file=f, purpose="batch")

        batch = client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )

        Database.execute_query(
            """INSERT INTO batch_jobs
               (openai_batch_id, input_file_id, status, url_count, created_at)
               VALUES (%s, %s, 'submitted', %s, %s)""",
            (batch.id, uploaded.id, len(lines), datetime.now(timezone.utc)),
        )

        logging.info("Batch submitted: %s (%d URLs)", batch.id, len(lines))
    finally:
        os.unlink(tmp_path)


class _UsageDict:
    """Thin wrapper so a dict from the Batch API response can be passed to log_llm_usage()."""
    def __init__(self, d: dict) -> None:
        self.prompt_tokens = d.get("prompt_tokens", 0)
        self.completion_tokens = d.get("completion_tokens", 0)
        self.total_tokens = d.get("total_tokens", self.prompt_tokens + self.completion_tokens)


def batch_check() -> None:
    """Check pending batch jobs and process completed results."""
    from llm_client import get_client, get_model
    from publication import PublicationExtraction, validate_publication
    from pydantic import ValidationError
    import json
    from datetime import datetime, timezone

    client = get_client()
    model = get_model()

    pending = Database.fetch_all(
        """SELECT id, openai_batch_id FROM batch_jobs
           WHERE status IN ('submitted','validating','in_progress','finalizing')"""
    )

    if not pending:
        logging.info("No pending batches")
        return

    for row in pending:
        db_id, openai_batch_id = row['id'], row['openai_batch_id']
        batch = client.batches.retrieve(openai_batch_id)
        status = batch.status

        if status == "completed":
            output_file_id = batch.output_file_id
            content = client.files.content(output_file_id).text

            total_prompt_tokens = 0
            total_completion_tokens = 0
            saved_pubs = 0
            processed_urls = 0

            for line in content.strip().splitlines():
                result = json.loads(line)
                custom_id = result.get("custom_id", "")
                url_id = int(custom_id.replace("url_", "")) if custom_id.startswith("url_") else None
                if url_id is None:
                    continue

                url_row = Database.fetch_one(
                    "SELECT url FROM researcher_urls WHERE id = %s", (url_id,)
                )
                if not url_row:
                    continue
                url = url_row['url']

                response_body = result.get("response", {}).get("body", {})
                usage_dict = response_body.get("usage", {})
                usage = _UsageDict(usage_dict)
                total_prompt_tokens += usage.prompt_tokens
                total_completion_tokens += usage.completion_tokens

                Database.log_llm_usage(
                    "publication_extraction", model, usage,
                    context_url=url, is_batch=True, batch_job_id=db_id,
                )

                choices = response_body.get("choices", [])
                if not choices:
                    continue
                raw_response = choices[0].get("message", {}).get("content", "")
                if not raw_response:
                    continue
                # Strip markdown fences if present (Gemma sometimes wraps output despite schema guidance)
                stripped = raw_response.strip()
                if stripped.startswith("```"):
                    stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
                    if stripped.endswith("```"):
                        stripped = stripped.rsplit("```", 1)[0]
                    stripped = stripped.strip()
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as e:
                    logging.warning(f"Batch result not valid JSON for url_id={url_id}: {e}")
                    continue
                # Schema produces {"publications": [...]} — unwrap to the bare list
                if isinstance(parsed, dict) and "publications" in parsed:
                    parsed = parsed["publications"]
                if not isinstance(parsed, list):
                    continue

                validated = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    try:
                        pub = PublicationExtraction(**item)
                        d = pub.model_dump()
                    except (ValidationError, TypeError) as e:
                        logging.warning(f"Rejected malformed batch publication: {e}")
                        continue
                    if validate_publication(d):
                        validated.append(d)
                    else:
                        logging.info("Batch validation dropped: %s", d.get("title", "<no title>"))

                if validated:
                    is_seed = HTMLFetcher.is_first_extraction(url_id)
                    Publication.save_publications(url, validated, is_seed=is_seed)
                    reconcile_title_renames(url, validated)
                    match_and_save_paper_links(url_id, validated)
                    saved_pubs += len(validated)
                HTMLFetcher.mark_extracted(url_id)
                processed_urls += 1

            # Aggregate cost from llm_usage rows logged above
            cost_row = Database.fetch_one(
                """SELECT SUM(estimated_cost_usd) AS total_cost FROM llm_usage
                   WHERE batch_job_id = %s""",
                (db_id,),
            )
            batch_cost = cost_row['total_cost'] if cost_row else None

            Database.execute_query(
                """UPDATE batch_jobs
                   SET status = 'completed', output_file_id = %s, completed_at = %s,
                       prompt_tokens_total = %s, completion_tokens_total = %s,
                       estimated_cost_usd = %s
                   WHERE id = %s""",
                (output_file_id, datetime.now(timezone.utc),
                 total_prompt_tokens, total_completion_tokens, batch_cost, db_id),
            )
            logging.info(
                "Batch %s completed: %d URLs processed, %d publications saved",
                openai_batch_id, processed_urls, saved_pubs,
            )

        elif status in ("failed", "expired", "cancelled"):
            error_msg = getattr(batch, 'errors', None)
            Database.execute_query(
                "UPDATE batch_jobs SET status = %s, error_message = %s WHERE id = %s",
                (status, str(error_msg), db_id),
            )
            logging.warning("Batch %s %s: %s", openai_batch_id, status, error_msg)

        else:
            req_counts = getattr(batch, 'request_counts', None)
            logging.info("Batch %s status: %s — %s", openai_batch_id, status, req_counts)


def discover_domains() -> None:
    """Scan all stored raw HTML to find untrusted domains that may host paper links."""
    from collections import Counter
    from link_extractor import discover_untrusted_domains

    # Fetch only url_ids first, then load raw HTML one at a time to avoid OOM
    url_ids = Database.fetch_all(
        "SELECT url_id FROM html_content WHERE raw_html IS NOT NULL"
    )
    if not url_ids:
        logging.info("No raw HTML stored yet. Run 'make fetch' first.")
        return

    totals = Counter()
    for row in url_ids:
        html_row = Database.fetch_one(
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
    subparsers.add_parser('classify-jel', help='Classify researchers into JEL codes from bios')
    subparsers.add_parser('enrich', help='Enrich publications with OpenAlex metadata')
    subparsers.add_parser('enrich-jel', help='Enrich researcher JEL codes from paper topics via OpenAlex')
    subparsers.add_parser('discover-domains', help='Scan stored HTML for untrusted domains with paper-title links')

    args = parser.parse_args()

    if args.command == 'import':
        import_data(args.file_path)
    elif args.command == 'download':
        download_htmls()
    elif args.command == 'classify-jel':
        classify_jel()
    elif args.command == 'enrich':
        Database.create_tables()
        from openalex import enrich_new_publications
        enrich_new_publications(limit=500)
    elif args.command == 'enrich-jel':
        Database.create_tables()
        from jel_enrichment import enrich_jel_from_papers
        enrich_jel_from_papers()
    elif args.command == 'discover-domains':
        discover_domains()

if __name__ == "__main__":
    main()
