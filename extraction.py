"""Per-URL publication extraction, shared by the background worker and CLI.

Decoupled from fetching: reads stored HTML, runs LLM extraction on the full
text, persists papers/links/snapshots, and marks the URL extracted with the
content hash read *before* the LLM call — so a fetch that updates the page
mid-extraction leaves the URL in the needs-extraction queue.
"""
import logging
from dataclasses import dataclass

from database import Database
from html_fetcher import HTMLFetcher
from link_extractor import match_and_save_paper_links
from publication import Publication, append_snapshots_for_pubs, reconcile_title_renames

logger = logging.getLogger(__name__)


@dataclass
class ExtractionOutcome:
    """Result of extracting one URL.

    status values:
      'extracted'  — publications found and saved; URL marked extracted
      'empty'      — LLM succeeded, page has no publications; URL marked extracted
      'failed'     — LLM call failed; URL NOT marked, will be retried
      'no_content' — no stored HTML/text; URL NOT marked
    """
    status: str
    pubs_count: int = 0

    @property
    def ok(self) -> bool:
        return self.status in ("extracted", "empty")


def extract_one_url(url_row: dict, scrape_log_id: int | None = None) -> ExtractionOutcome:
    """Extract publications for one researcher URL from stored HTML.

    is_seed deliberately uses extracted_at IS NULL (first *extraction*), not
    fetch-time state — decoupled from fetching, that is the only reliable
    seed signal and it stays correct when a URL is fetched repeatedly before
    its first extraction.

    A crash between save_publications and mark_extracted re-extracts the URL
    on the next pass; saves dedup via title_hash, but a duplicate paper
    snapshot / status_change event is possible in that window — accepted
    tradeoff of the non-transactional persist-then-mark sequence.
    """
    url_id = url_row['id']
    url = url_row['url']
    researcher_id = url_row['researcher_id']
    page_type = url_row['page_type']

    payload = HTMLFetcher.get_extraction_payload(url_id)
    if not payload:
        return ExtractionOutcome("no_content")

    text = payload['content']
    if not text and payload['raw_html']:
        text = HTMLFetcher.extract_text_content(payload['raw_html'])
    if not text:
        return ExtractionOutcome("no_content")
    content_hash = payload['content_hash']
    is_seed = payload['extracted_at'] is None

    pubs = Publication.try_extract_publications(text, url, scrape_log_id=scrape_log_id)
    if pubs is None:
        return ExtractionOutcome("failed")

    if pubs:
        fetch_date = HTMLFetcher.get_fetch_timestamp(url_id)
        Publication.save_publications(url, pubs, is_seed=is_seed, event_date=fetch_date)
        reconcile_title_renames(url, pubs, event_date=fetch_date)
        match_and_save_paper_links(url_id, pubs)
        append_snapshots_for_pubs(pubs, url, event_date=fetch_date)

    if page_type == "HOME":
        _update_home_description(researcher_id, text, url, scrape_log_id)

    HTMLFetcher.mark_extracted(url_id, content_hash)
    return ExtractionOutcome("extracted" if pubs else "empty", pubs_count=len(pubs))


def _update_home_description(researcher_id: int, text: str, url: str,
                             scrape_log_id: int | None = None) -> None:
    """Best-effort researcher description refresh from a HOME page.

    Failures are logged, never propagated — a description glitch must not
    block publication extraction.
    """
    try:
        description = HTMLFetcher.extract_description(text, url, scrape_log_id=scrape_log_id)
        if not description:
            return
        r_row = Database.fetch_one(
            "SELECT position, affiliation FROM researchers WHERE id = %s",
            (researcher_id,),
        )
        position = r_row['position'] if r_row else None
        affiliation = r_row['affiliation'] if r_row else None
        Database.append_researcher_snapshot(
            researcher_id, position, affiliation, description, source_url=url,
        )
    except Exception as e:
        logger.error("Description update failed for %s: %s: %s", url, type(e).__name__, e)
