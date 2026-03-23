# scripts/backfill_paper_links.py
"""One-time backfill: extract links from stored HTML, resolve DOIs, enrich papers.

Processes all stored HTML pages to populate the paper_links table,
then enriches papers that gained DOIs.

Run: poetry run python scripts/backfill_paper_links.py
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from database import Database
from html_fetcher import HTMLFetcher
from link_extractor import match_and_save_paper_links


def backfill_links():
    """Extract links from all stored HTML and save to paper_links.

    Reuses match_and_save_paper_links() — the same function used during scraping.
    For backfill, we pass an empty publications list so it relies on DOI-based
    matching and falls back to anchor text matching against all researcher papers.
    """
    all_urls = Database.fetch_all(
        "SELECT hc.url_id, ru.url, ru.researcher_id "
        "FROM html_content hc "
        "JOIN researcher_urls ru ON ru.id = hc.url_id "
        "WHERE hc.raw_html IS NOT NULL"
    )
    logger.info("Processing %d HTML pages for link extraction", len(all_urls))

    for i, row in enumerate(all_urls):
        # Get all papers for this researcher to pass as publications
        papers = Database.fetch_all(
            "SELECT p.title FROM papers p "
            "JOIN authorship a ON a.publication_id = p.id "
            "WHERE a.researcher_id = %s",
            (row['researcher_id'],),
        )
        pubs = [{'title': p['title']} for p in papers]

        match_and_save_paper_links(row['url_id'], pubs)

        if (i + 1) % 50 == 0:
            logger.info("Progress: %d/%d pages", i + 1, len(all_urls))

    count = Database.fetch_one("SELECT COUNT(*) AS c FROM paper_links")
    logger.info("Backfill complete: %d paper_links rows", count['c'])


def enrich_from_links():
    """Enrich papers that now have DOIs from paper_links."""
    from openalex import enrich_new_publications
    count = enrich_new_publications(limit=500)
    logger.info("Enriched %d papers from DOI-resolved links", count)


if __name__ == "__main__":
    print("=== Phase 1: Backfill paper_links from stored HTML ===")
    backfill_links()

    count = Database.fetch_one("SELECT COUNT(*) AS c FROM paper_links")
    print(f"\npaper_links rows: {count['c']}")

    doi_count = Database.fetch_one("SELECT COUNT(*) AS c FROM paper_links WHERE doi IS NOT NULL")
    print(f"paper_links with DOI: {doi_count['c']}")

    print("\n=== Phase 2: Enrich papers with DOIs ===")
    enrich_from_links()

    enriched = Database.fetch_one("SELECT COUNT(*) AS c FROM papers WHERE openalex_id IS NOT NULL")
    print(f"\nTotal enriched papers: {enriched['c']}")
