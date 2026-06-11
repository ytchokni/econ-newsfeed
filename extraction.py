"""Per-URL publication extraction, shared by the background worker and CLI.

Decoupled from fetching: reads stored HTML, runs LLM extraction, persists
papers/links/snapshots, and marks the URL extracted with the content hash
read *before* the LLM call — so a fetch that updates the page mid-extraction
leaves the URL in the needs-extraction queue.

Two extraction paths:
  - **Diff path** (non-seed, previous snapshot available): compares old and new
    page text side-by-side, extracting only new/changed publications.
  - **Full path** (seed or no previous snapshot): extracts all publications from
    the full page text (original behaviour).
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

    Uses diff-based extraction when a previous snapshot exists (non-seed),
    falling back to full extraction for seed URLs or when no snapshot is
    available.
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

    previous_text = None if is_seed else HTMLFetcher.get_previous_text(url_id)

    if previous_text is not None:
        outcome = _extract_via_diff(url_id, url, text, previous_text, content_hash, scrape_log_id)
    else:
        outcome = _extract_full(url_id, url, text, is_seed, content_hash, scrape_log_id)

    if outcome.ok and page_type == "HOME":
        _update_home_description(researcher_id, text, url, scrape_log_id)

    return outcome


def _extract_full(url_id: int, url: str, text: str, is_seed: bool,
                  content_hash: str, scrape_log_id: int | None) -> ExtractionOutcome:
    """Original full-page extraction path."""
    pubs = Publication.try_extract_publications(text, url, scrape_log_id=scrape_log_id)
    if pubs is None:
        return ExtractionOutcome("failed")

    if pubs:
        fetch_date = HTMLFetcher.get_fetch_timestamp(url_id)
        Publication.save_publications(url, pubs, is_seed=is_seed, event_date=fetch_date)
        reconcile_title_renames(url, pubs, event_date=fetch_date)
        match_and_save_paper_links(url_id, pubs)
        append_snapshots_for_pubs(pubs, url, event_date=fetch_date)

    HTMLFetcher.mark_extracted(url_id, content_hash)
    return ExtractionOutcome("extracted" if pubs else "empty", pubs_count=len(pubs))


def _extract_via_diff(url_id: int, url: str, new_text: str, old_text: str,
                      content_hash: str, scrape_log_id: int | None) -> ExtractionOutcome:
    """Diff-based extraction: compare old and new page, extract only changes."""
    changes = Publication.try_extract_changes(old_text, new_text, url, scrape_log_id=scrape_log_id)
    if changes is None:
        return ExtractionOutcome("failed")

    count = _process_diff_changes(changes, url, url_id)

    HTMLFetcher.mark_extracted(url_id, content_hash)
    return ExtractionOutcome("extracted" if count > 0 else "empty", pubs_count=count)


def _process_diff_changes(changes: list[dict], url: str, url_id: int) -> int:
    """Process structured change events from diff extraction.

    Handles new_paper, status_change, and title_change events. Returns
    the number of actionable changes processed.
    """
    from feed_events import FeedEventEmitter

    fetch_date = HTMLFetcher.get_fetch_timestamp(url_id)

    new_pubs = [c for c in changes if c['change_type'] == 'new_paper']
    status_changes = [c for c in changes if c['change_type'] == 'status_change']
    title_changes = [c for c in changes if c['change_type'] == 'title_change']

    if new_pubs:
        Publication.save_publications(url, new_pubs, is_seed=False, event_date=fetch_date)
        match_and_save_paper_links(url_id, new_pubs)
        append_snapshots_for_pubs(new_pubs, url, event_date=fetch_date)

    for sc in status_changes:
        _apply_status_change(sc, url, fetch_date)

    for tc in title_changes:
        _apply_title_change(tc, url, fetch_date)

    return len(new_pubs) + len(status_changes) + len(title_changes)


def _apply_status_change(change: dict, source_url: str, event_date) -> None:
    """Apply a status change detected by diff extraction."""
    from feed_events import FeedEventEmitter

    title_hash = Database.compute_title_hash(change['title'])
    row = Database.fetch_one(
        "SELECT id FROM papers WHERE title_hash = %s", (title_hash,),
    )
    if not row:
        logger.warning("Status change for unknown paper: %s", change['title'])
        return

    paper_id = row['id']
    result = Database.append_paper_snapshot(
        paper_id=paper_id,
        status=change.get('status'),
        venue=change.get('venue'),
        abstract=change.get('abstract'),
        draft_url=change.get('draft_url'),
        year=change.get('year'),
        source_url=source_url,
        title=change.get('title'),
    )
    if result.status_changed:
        FeedEventEmitter.emit_status_change(
            paper_id, result.old_status, result.new_status, event_date=event_date,
        )


def _apply_title_change(change: dict, source_url: str, event_date) -> None:
    """Apply a title change detected by diff extraction."""
    from feed_events import FeedEventEmitter

    old_title = change.get('old_title')
    new_title = change.get('title')
    if not old_title or not new_title:
        return

    old_hash = Database.compute_title_hash(old_title)
    row = Database.fetch_one("SELECT id FROM papers WHERE title_hash = %s", (old_hash,))
    if not row:
        logger.warning("Title change for unknown paper: %s -> %s", old_title, new_title)
        return

    paper_id = row['id']
    new_hash = Database.compute_title_hash(new_title)

    Database.append_paper_snapshot(
        paper_id=paper_id,
        status=change.get('status'),
        venue=change.get('venue'),
        abstract=change.get('abstract'),
        draft_url=change.get('draft_url'),
        year=change.get('year'),
        source_url=source_url,
        title=old_title,
    )

    Database.execute_query(
        "UPDATE papers SET title = %s, title_hash = %s WHERE id = %s",
        (new_title, new_hash, paper_id),
    )
    FeedEventEmitter.emit_title_change(paper_id, old_title, new_title, event_date=event_date)


def _update_home_description(researcher_id: int, text: str, url: str,
                             scrape_log_id: int | None = None) -> None:
    """Best-effort researcher description refresh from a HOME page."""
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
