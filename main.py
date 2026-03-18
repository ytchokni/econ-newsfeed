import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from database import Database
from researcher import Researcher
from publication import Publication
from html_fetcher import HTMLFetcher

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def import_data():
    """Import data from a file into the database."""
    file_path = input("Enter the path to the file: ")
    Database.import_data_from_file(file_path)
    logging.info(f"Data imported from {file_path}")

def download_htmls():
    """Download HTML content for all URLs in the researcher_urls table."""
    researcher_urls = Researcher.get_all_researcher_urls()
    for id, researcher_id, url, page_type in researcher_urls:
        logging.info(f"Downloading HTML for URL ID: {id}, URL: {url}, Page Type: {page_type}")
        HTMLFetcher.fetch_and_save_if_changed(id, url, researcher_id)

def extract_data_from_htmls():
    """Extract publication data from downloaded HTML content."""
    researcher_urls = Researcher.get_all_researcher_urls()
    for id, researcher_id, url, page_type in researcher_urls:
        if not HTMLFetcher.needs_extraction(id):
            logging.info(f"Skipping extraction for URL ID: {id}, URL: {url} (content unchanged since last extraction)")
            continue
        logging.info(f"Extracting data from HTML for URL ID: {id}, URL: {url}, Page Type: {page_type}")
        html_content = HTMLFetcher.get_latest_text(id)
        if html_content:
            extracted_publications = Publication.extract_publications(html_content, url)
            if extracted_publications:
                Publication.save_publications(url, extracted_publications)
            else:
                logging.warning(f"No publications extracted for URL ID: {id}, URL: {url}")
            HTMLFetcher.mark_extracted(id)
        else:
            logging.error(f"No HTML content found for URL ID: {id}, URL: {url}")


def _process_one_url(url_id, researcher_id, url, page_type):
    """Process a single URL: check if extraction needed, extract, save, mark."""
    if not HTMLFetcher.needs_extraction(url_id):
        logging.info(f"Skipping URL ID {url_id} (unchanged): {url}")
        return url, 0, None
    html_content = HTMLFetcher.get_latest_text(url_id)
    if not html_content:
        return url, 0, "no HTML content"
    try:
        pubs = Publication.extract_publications(html_content, url)
        if pubs:
            Publication.save_publications(url, pubs)
        HTMLFetcher.mark_extracted(url_id)
        return url, len(pubs), None
    except Exception as e:
        return url, 0, str(e)


def extract_data_from_htmls_concurrent():
    """Extract publication data concurrently using ThreadPoolExecutor."""
    researcher_urls = Researcher.get_all_researcher_urls()
    workers = int(os.environ.get('PARSE_WORKERS', '8'))
    total = len(researcher_urls)
    logging.info(f"Starting concurrent extraction: {total} URLs, {workers} workers")

    success_count = 0
    error_count = 0
    total_pubs = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_process_one_url, url_id, researcher_id, url, page_type): url
            for url_id, researcher_id, url, page_type in researcher_urls
        }
        for future in as_completed(futures):
            url, num_pubs, error = future.result()
            if error:
                logging.error(f"Error processing {url}: {error}")
                error_count += 1
            else:
                total_pubs += num_pubs
                success_count += 1

    logging.info(
        f"Concurrent extraction done: {success_count} succeeded, {error_count} errors, "
        f"{total_pubs} publications extracted"
    )


def batch_submit():
    """Submit a batch job to the OpenAI Batch API for all URLs needing extraction."""
    from openai import OpenAI
    from publication import OPENAI_MODEL

    client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    researcher_urls = Researcher.get_all_researcher_urls()
    urls_to_process = [
        (url_id, researcher_id, url, page_type)
        for url_id, researcher_id, url, page_type in researcher_urls
        if HTMLFetcher.needs_extraction(url_id)
    ]

    if not urls_to_process:
        print("Nothing to extract")
        return

    # Warn if pending batches exist
    pending = Database.fetch_all(
        """SELECT openai_batch_id FROM batch_jobs
           WHERE status IN ('submitted','validating','in_progress','finalizing')"""
    )
    if pending:
        ids = ", ".join(r[0] for r in pending)
        print(f"Warning: {len(pending)} pending batch(es) already exist: {ids}")

    # Build JSONL
    lines = []
    for url_id, researcher_id, url, page_type in urls_to_process:
        text = HTMLFetcher.get_latest_text(url_id)
        if not text:
            continue
        prompt = Publication.build_extraction_prompt(text, url)
        request = {
            "custom_id": f"url_{url_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": OPENAI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
            },
        }
        lines.append(json.dumps(request))

    if not lines:
        print("No URLs with downloadable content to batch")
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

        print(f"Batch submitted: {batch.id} ({len(lines)} URLs)")
    finally:
        os.unlink(tmp_path)


def batch_check():
    """Check pending batch jobs and process completed results."""
    from openai import OpenAI
    from publication import PublicationExtraction, OPENAI_MODEL
    from pydantic import ValidationError

    client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    pending = Database.fetch_all(
        """SELECT id, openai_batch_id FROM batch_jobs
           WHERE status IN ('submitted','validating','in_progress','finalizing')"""
    )

    if not pending:
        print("No pending batches")
        return

    for db_id, openai_batch_id in pending:
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
                url = url_row[0]

                response_body = result.get("response", {}).get("body", {})
                usage = response_body.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens

                # Log per-request usage
                from database import _LLM_PRICING
                pricing = _LLM_PRICING.get(OPENAI_MODEL)
                estimated_cost = None
                if pricing:
                    p_rate, c_rate = pricing
                    estimated_cost = 0.5 * (
                        prompt_tokens * p_rate / 1_000_000
                        + completion_tokens * c_rate / 1_000_000
                    )
                Database.execute_query(
                    """INSERT INTO llm_usage
                       (called_at, call_type, model, prompt_tokens, completion_tokens,
                        total_tokens, estimated_cost_usd, is_batch, context_url, batch_job_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)""",
                    (datetime.now(timezone.utc), "publication_extraction", OPENAI_MODEL,
                     prompt_tokens, completion_tokens, total_tokens, estimated_cost, url, db_id),
                )

                choices = response_body.get("choices", [])
                if not choices:
                    continue
                raw_response = choices[0].get("message", {}).get("content", "")
                parsed = Publication.parse_openai_response(raw_response)
                if not parsed:
                    continue

                validated = []
                for item in parsed:
                    try:
                        pub = PublicationExtraction(**item)
                        validated.append(pub.model_dump())
                    except (ValidationError, TypeError) as e:
                        logging.warning(f"Rejected malformed batch publication: {e}")

                if validated:
                    Publication.save_publications(url, validated)
                    saved_pubs += len(validated)
                HTMLFetcher.mark_extracted(url_id)
                processed_urls += 1

            # Compute aggregate cost for batch_jobs
            pricing = _LLM_PRICING.get(OPENAI_MODEL)
            batch_cost = None
            if pricing:
                p_rate, c_rate = pricing
                batch_cost = 0.5 * (
                    total_prompt_tokens * p_rate / 1_000_000
                    + total_completion_tokens * c_rate / 1_000_000
                )

            Database.execute_query(
                """UPDATE batch_jobs
                   SET status = 'completed', output_file_id = %s, completed_at = %s,
                       prompt_tokens_total = %s, completion_tokens_total = %s,
                       estimated_cost_usd = %s
                   WHERE id = %s""",
                (output_file_id, datetime.now(timezone.utc),
                 total_prompt_tokens, total_completion_tokens, batch_cost, db_id),
            )
            print(
                f"Batch {openai_batch_id} completed: {processed_urls} URLs processed, "
                f"{saved_pubs} publications saved"
            )

        elif status in ("failed", "expired", "cancelled"):
            error_msg = getattr(batch, 'errors', None)
            Database.execute_query(
                "UPDATE batch_jobs SET status = %s, error_message = %s WHERE id = %s",
                (status, str(error_msg), db_id),
            )
            print(f"Batch {openai_batch_id} {status}: {error_msg}")

        else:
            req_counts = getattr(batch, 'request_counts', None)
            print(f"Batch {openai_batch_id} status: {status} — {req_counts}")


def main():
    """Main function to handle user input and execute the appropriate actions."""

    actions = {
        '1': ('Import data from a file', import_data),
        '2': ('Download HTML content', download_htmls),
        '3': ('Extract data from HTML content', extract_data_from_htmls),
        '4': ('Exit', lambda: "exit")
    }

    while True:
        print("\nChoose an action:")
        for key, (description, _) in actions.items():
            print(f"{key}: {description}")

        choice = input("Enter your choice: ")
        action = actions.get(choice)

        if action:
            result = action[1]()
            if result == "exit":
                print("Exiting the program.")
                break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
