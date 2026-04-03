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
