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
from link_extractor import extract_trusted_links, match_link_to_paper
from doi_resolver import resolve_doi
from openalex import lookup_by_doi


def backfill_links():
    """Extract links from all stored HTML and save to paper_links."""
    all_urls = Database.fetch_all(
        "SELECT hc.url_id, ru.url, ru.researcher_id "
        "FROM html_content hc "
        "JOIN researcher_urls ru ON ru.id = hc.url_id "
        "WHERE hc.raw_html IS NOT NULL"
    )
    logger.info("Processing %d HTML pages for link extraction", len(all_urls))

    total_links = 0
    total_matched = 0
    total_doi_resolved = 0

    for i, row in enumerate(all_urls):
        raw_html = HTMLFetcher.get_raw_html(row['url_id'])
        if not raw_html:
            continue

        page_links = extract_trusted_links(raw_html)
        if not page_links:
            continue

        # Get all papers for this researcher
        papers = Database.fetch_all(
            "SELECT p.id, p.title, p.title_hash FROM papers p "
            "JOIN authorship a ON a.publication_id = p.id "
            "WHERE a.researcher_id = %s",
            (row['researcher_id'],),
        )
        paper_titles = {p['title']: p['id'] for p in papers}

        for link in page_links:
            if link['link_type'] not in ('journal', 'doi', 'ssrn', 'nber', 'arxiv', 'repository'):
                continue

            total_links += 1
            paper_id = None
            link_doi = None

            # Try DOI resolution
            link_doi = resolve_doi(link['url'])
            if link_doi:
                total_doi_resolved += 1
                # Match by canonical title
                openalex_data = lookup_by_doi(link_doi)
                if openalex_data and openalex_data.get('title'):
                    canonical_hash = Database.compute_title_hash(openalex_data['title'])
                    paper_row = Database.fetch_one(
                        "SELECT id FROM papers WHERE title_hash = %s", (canonical_hash,)
                    )
                    if paper_row:
                        paper_id = paper_row['id']
                time.sleep(0.2)  # Rate limit OpenAlex

            # Fallback: anchor text matching
            if not paper_id and paper_titles:
                matched_title, _ = match_link_to_paper(link['anchor_text'], list(paper_titles.keys()))
                if matched_title:
                    paper_id = paper_titles[matched_title]

            if paper_id:
                total_matched += 1
                try:
                    from datetime import datetime, timezone
                    Database.execute_query(
                        """INSERT IGNORE INTO paper_links (paper_id, url, link_type, doi, discovered_at)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (paper_id, link['url'], link['link_type'], link_doi,
                         datetime.now(timezone.utc)),
                    )
                except Exception as e:
                    logger.warning("Error saving link: %s", e)

        if (i + 1) % 50 == 0:
            logger.info("Progress: %d/%d pages, %d links found, %d matched, %d DOIs resolved",
                        i + 1, len(all_urls), total_links, total_matched, total_doi_resolved)

    logger.info("Backfill complete: %d links found, %d matched to papers, %d DOIs resolved",
                total_links, total_matched, total_doi_resolved)


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
