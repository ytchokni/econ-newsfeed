"""Orchestrates one batch of URL discovery: search → classify → crawl → save."""
import logging
import os
import time

from backend.database.discoveries import get_discovery_candidates, insert_discovery
from backend.discovery.web_search import search_researcher, QuotaExhaustedError
from backend.discovery.classifier import classify_search_results
from backend.discovery.subpage_crawler import crawl_subpages

logger = logging.getLogger(__name__)

_SEARCH_DELAY_SECONDS = float(os.environ.get("DISCOVERY_SEARCH_DELAY", "6"))


def run_discovery_batch(limit: int | None = None) -> dict:
    """Run one batch of URL discovery.

    1. Pick candidates from the DB
    2. Search each one via Searlo
    3. Gemma classifies search results
    4. HTML crawl for subpages if URL found
    5. Save to url_discoveries

    Returns summary: {"searched": int, "found": int, "no_result": int, "errors": int}
    """
    daily_limit = limit or int(os.environ.get("DISCOVERY_DAILY_LIMIT", "100"))
    candidates = get_discovery_candidates(daily_limit)

    if not candidates:
        logger.info("No discovery candidates remaining")
        return {"searched": 0, "found": 0, "no_result": 0, "errors": 0}

    logger.info("Starting URL discovery batch: %d candidates", len(candidates))

    stats = {"searched": 0, "found": 0, "no_result": 0, "errors": 0}

    for i, candidate in enumerate(candidates):
        rid = candidate["id"]
        first = candidate["first_name"]
        last = candidate["last_name"]
        affiliation = candidate.get("affiliation")

        try:
            try:
                query, results = search_researcher(first, last, affiliation)
            except QuotaExhaustedError:
                logger.warning("Search quota exhausted after %d searches — stopping batch", stats["searched"])
                break

            if not results:
                insert_discovery(rid, None, None, None, query or f"{first} {last}")
                stats["no_result"] += 1
                stats["searched"] += 1
                time.sleep(_SEARCH_DELAY_SECONDS)
                continue

            classification = classify_search_results(first, last, affiliation, results)
            if classification is None or classification.url is None:
                insert_discovery(rid, None, None, None, query)
                stats["no_result"] += 1
            else:
                subpages = crawl_subpages(classification.url)
                insert_discovery(
                    rid,
                    classification.url,
                    subpages if subpages else None,
                    classification.confidence,
                    query,
                )
                stats["found"] += 1
                logger.info(
                    "Discovered URL for %s %s: %s (confidence=%.2f, subpages=%d)",
                    first, last, classification.url, classification.confidence, len(subpages),
                )

            stats["searched"] += 1
            time.sleep(_SEARCH_DELAY_SECONDS)

        except Exception as e:
            logger.warning("Discovery failed for %s %s (id=%d): %s", first, last, rid, e)
            stats["errors"] += 1
            stats["searched"] += 1

        if (i + 1) % 20 == 0:
            logger.info("Discovery progress: %d/%d", i + 1, len(candidates))

    logger.info(
        "Discovery batch complete: searched=%d found=%d no_result=%d errors=%d",
        stats["searched"], stats["found"], stats["no_result"], stats["errors"],
    )
    return stats
