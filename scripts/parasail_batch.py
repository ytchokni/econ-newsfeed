#!/usr/bin/env python3
"""One-time batch extraction via Parasail to clear the extraction queue.

Run: poetry run python scripts/parasail_batch.py
"""

import json
import logging
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Load .env before app imports (db_config.py validates at import time)
env_path = os.path.join(ROOT, '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())

os.environ.setdefault('SCRAPE_API_KEY', 'unused')

from openai import OpenAI
from backend.database import fetch_all, fetch_one
from backend.pipeline.html_fetcher import HTMLFetcher
from backend.pipeline.publication import Publication, PublicationExtraction, validate_publication
from backend.pipeline.extraction import persist_extraction
from pydantic import ValidationError

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

MODEL = "google/gemma-4-31b-it"
PARASAIL_BASE_URL = "https://api.parasail.io/v1"
BATCH_DIR = os.path.join(ROOT, 'parasail_batch_data')
INPUT_FILE = os.path.join(BATCH_DIR, 'input.jsonl')
OUTPUT_FILE = os.path.join(BATCH_DIR, 'output.jsonl')


def main():
    api_key = os.environ.get('PARASAIL_API_KEY')
    if not api_key:
        sys.exit("PARASAIL_API_KEY not set")

    client = OpenAI(base_url=PARASAIL_BASE_URL, api_key=api_key)

    # ── 1. Query pending URLs ─────────────────────────────────────────────
    pending = fetch_all("""
        SELECT ru.id, ru.researcher_id, ru.url, ru.page_type
        FROM researcher_urls ru
        JOIN html_content hc ON hc.url_id = ru.id
        WHERE ru.is_active = TRUE
          AND hc.content_hash IS NOT NULL
          AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)
        ORDER BY ru.id
    """)
    log.info("Queue: %d URLs", len(pending))
    if not pending:
        return

    # ── 2. Build JSONL ────────────────────────────────────────────────────
    os.makedirs(BATCH_DIR, exist_ok=True)
    lines = []
    skipped = 0
    for i, row in enumerate(pending):
        text = HTMLFetcher.get_latest_text(row['id'])
        if not text:
            skipped += 1
            continue
        prompt = Publication.build_extraction_prompt(text, row['url'])
        lines.append(json.dumps({
            "custom_id": f"url_{row['id']}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 8000,
                "temperature": 0.0,
            },
        }))
        if (i + 1) % 500 == 0:
            log.info("Built %d/%d", len(lines), i + 1)

    with open(INPUT_FILE, 'w') as f:
        f.write('\n'.join(lines))
    log.info("Input: %d requests, %d skipped, %.1f MB",
             len(lines), skipped, os.path.getsize(INPUT_FILE) / 1_048_576)

    # ── 3. Upload + submit ────────────────────────────────────────────────
    with open(INPUT_FILE, 'rb') as f:
        file_obj = client.files.create(file=f, purpose="batch")
    log.info("Uploaded: %s", file_obj.id)

    batch = client.batches.create(
        input_file_id=file_obj.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    log.info("Batch: %s", batch.id)

    # ── 4. Poll ───────────────────────────────────────────────────────────
    while True:
        batch = client.batches.retrieve(batch.id)
        rc = batch.request_counts
        status = f"{batch.status}"
        if rc:
            status += f" ({rc.completed}/{rc.total} done, {rc.failed} failed)"
        log.info(status)
        if batch.status in ("completed", "failed", "expired", "cancelled"):
            break
        time.sleep(60)

    if batch.status != "completed":
        sys.exit(f"Batch failed: {batch.status}")

    # ── 5. Download ───────────────────────────────────────────────────────
    client.files.content(batch.output_file_id).write_to_file(OUTPUT_FILE)
    log.info("Output saved: %s", OUTPUT_FILE)

    # ── 6. Process results ────────────────────────────────────────────────
    processed = errors = saved = 0
    with open(OUTPUT_FILE) as f:
        for line in f:
            result = json.loads(line)
            cid = result.get('custom_id', '')
            if not cid.startswith('url_'):
                errors += 1
                continue
            url_id = int(cid[4:])

            url_row = fetch_one("SELECT url FROM researcher_urls WHERE id = %s", (url_id,))
            if not url_row:
                errors += 1
                continue

            if result.get('error') or result.get('response', {}).get('status_code', 0) != 200:
                errors += 1
                continue

            choices = result.get('response', {}).get('body', {}).get('choices', [])
            raw = choices[0].get('message', {}).get('content', '') if choices else ''
            if not raw:
                errors += 1
                continue

            # Strip code fences
            s = raw.strip()
            if s.startswith('```'):
                s = s.split('\n', 1)[1] if '\n' in s else s
                if s.endswith('```'):
                    s = s.rsplit('```', 1)[0]
                s = s.strip()

            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                errors += 1
                continue

            if isinstance(parsed, dict) and 'publications' in parsed:
                parsed = parsed['publications']
            if not isinstance(parsed, list):
                errors += 1
                continue

            validated = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                try:
                    d = PublicationExtraction(**item).model_dump()
                except (ValidationError, TypeError):
                    continue
                if validate_publication(d):
                    validated.append(d)

            if validated:
                fetch_date = HTMLFetcher.get_fetch_timestamp(url_id)
                is_seed = HTMLFetcher.is_first_extraction(url_id)
                persist_extraction(url_row['url'], url_id, validated,
                                   is_seed=is_seed, event_date=fetch_date)
                saved += len(validated)

            payload = HTMLFetcher.get_extraction_payload(url_id)
            HTMLFetcher.mark_extracted(url_id, payload['content_hash'] if payload else None)
            processed += 1

            if processed % 200 == 0:
                log.info("Processed %d (%d pubs, %d errors)", processed, saved, errors)

    log.info("Done: %d processed, %d pubs saved, %d errors", processed, saved, errors)


if __name__ == '__main__':
    main()
