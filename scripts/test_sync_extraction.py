"""Diagnostic: test synchronous Gemini extraction on a sample of unextracted URLs.

Usage:
    poetry run python scripts/test_sync_extraction.py                    # 10 random unextracted URLs
    poetry run python scripts/test_sync_extraction.py --limit 20         # 20 random
    poetry run python scripts/test_sync_extraction.py --url-ids 1,23,25  # specific URL IDs (batch failures)
"""

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import fetch_all
from backend.pipeline.html_fetcher import HTMLFetcher
from backend.pipeline.publication import Publication, PublicationExtractionList
from backend.llm.client import extract_json, get_model

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")


def get_urls_to_test(url_ids: list[int] | None, limit: int) -> list[dict]:
    if url_ids:
        placeholders = ",".join(["%s"] * len(url_ids))
        return fetch_all(
            f"""SELECT ru.id, ru.url, ru.researcher_id
                FROM researcher_urls ru
                JOIN html_content hc ON hc.url_id = ru.id
                WHERE ru.id IN ({placeholders})
                  AND hc.content_hash IS NOT NULL""",
            tuple(url_ids),
        )
    return fetch_all(
        """SELECT ru.id, ru.url, ru.researcher_id
           FROM researcher_urls ru
           JOIN html_content hc ON hc.url_id = ru.id
           WHERE hc.content_hash IS NOT NULL
             AND (hc.extracted_hash IS NULL OR hc.content_hash != hc.extracted_hash)
           LIMIT %s""",
        (limit,),
    )


def main():
    parser = argparse.ArgumentParser(description="Test synchronous Gemini extraction")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--url-ids", type=str, default=None, help="Comma-separated URL IDs")
    args = parser.parse_args()

    url_ids = [int(x) for x in args.url_ids.split(",")] if args.url_ids else None
    rows = get_urls_to_test(url_ids, args.limit)

    if not rows:
        print("No URLs to test.")
        return

    model = get_model()
    print(f"Model: {model}")
    print(f"Testing {len(rows)} URLs via synchronous API\n")
    print(f"{'url_id':>6} | {'prompt_ch':>9} | {'p_tok':>6} | {'c_tok':>6} | {'resp_ch':>7} | {'pubs':>4} | {'result':>6} | url")
    print("-" * 110)

    results = []
    for row in rows:
        url_id = row["id"]
        url = row["url"]
        text = HTMLFetcher.get_latest_text(url_id)
        if not text:
            print(f"{url_id:>6} | {'—':>9} | {'—':>6} | {'—':>6} | {'—':>7} | {'—':>4} | {'SKIP':>6} | {url[:60]}")
            continue

        prompt = Publication.build_extraction_prompt(text, url)
        prompt_chars = len(prompt)

        start = time.time()
        result = extract_json(prompt, PublicationExtractionList)
        elapsed = time.time() - start

        p_tok = result.usage.prompt_tokens if result.usage else 0
        c_tok = result.usage.completion_tokens if result.usage else 0

        if result.parsed is not None:
            pub_count = len(result.parsed.publications)
            status = "OK"
        else:
            pub_count = 0
            status = "FAIL"

        print(f"{url_id:>6} | {prompt_chars:>9} | {p_tok:>6} | {c_tok:>6} | {elapsed:>6.1f}s | {pub_count:>4} | {status:>6} | {url[:60]}")
        results.append({"url_id": url_id, "status": status, "p_tok": p_tok, "c_tok": c_tok, "pubs": pub_count})

    print("\n" + "=" * 60)
    ok = [r for r in results if r["status"] == "OK"]
    fail = [r for r in results if r["status"] == "FAIL"]
    print(f"Total: {len(results)} | OK: {len(ok)} | FAIL: {len(fail)} | Success rate: {len(ok)/len(results)*100:.0f}%")
    if ok:
        avg_c = sum(r["c_tok"] for r in ok) / len(ok)
        max_c = max(r["c_tok"] for r in ok)
        total_pubs = sum(r["pubs"] for r in ok)
        print(f"Completion tokens — avg: {avg_c:.0f}, max: {max_c} | Total pubs extracted: {total_pubs}")
    if fail:
        print(f"Failed URL IDs: {', '.join(str(r['url_id']) for r in fail)}")
    truncated = [r for r in results if 300 <= r["c_tok"] <= 315]
    if truncated:
        print(f"Possible truncation (~307 tok): url_ids {', '.join(str(r['url_id']) for r in truncated)}")
    else:
        print("No truncation detected (no responses capped at ~307 tokens)")


if __name__ == "__main__":
    main()
