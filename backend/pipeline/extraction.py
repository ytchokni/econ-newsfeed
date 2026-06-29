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
from datetime import datetime, timezone

from backend.database import (
    fetch_one, fetch_all, compute_title_hash, append_paper_snapshot,
    append_researcher_snapshot,
)
from backend.pipeline.feed_events import FeedEventEmitter
from backend.pipeline.html_fetcher import HTMLFetcher
from backend.enrichment.link_extractor import match_and_save_paper_links
from backend.pipeline.paper_saver import PaperSaver, validate_title_change
from backend.pipeline.publication import Publication

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
    retry_after: float | None = None

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
    if not text:
        raw_html = HTMLFetcher.get_raw_html(url_id)
        if raw_html:
            text = HTMLFetcher.extract_text_content(raw_html)
    if not text:
        return ExtractionOutcome("no_content")
    content_hash = payload['content_hash']
    is_seed = payload['extracted_at'] is None

    ts = payload.get('timestamp')
    if ts and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    fetch_date = ts

    previous_text = None if is_seed else HTMLFetcher.get_previous_text(url_id)

    if previous_text is not None:
        outcome = _extract_via_diff(url_id, url, text, previous_text, content_hash,
                                    scrape_log_id, fetch_date)
    else:
        outcome = _extract_full(url_id, url, text, is_seed, content_hash,
                                scrape_log_id, fetch_date)

    if outcome.ok and page_type == "HOME":
        _update_home_description(researcher_id, text, url, scrape_log_id)

    return outcome


def persist_extraction(url: str, url_id: int, pubs: list[dict],
                       is_seed: bool = False, event_date=None,
                       reconcile_titles: bool = True) -> None:
    """Persist extraction results in one call.

    Absorbs the full post-extraction sequence: save papers, emit feed events,
    reconcile title renames, match/save links, append snapshots.  Both
    extract_one_url() and main.batch_check() delegate here.
    """
    results = PaperSaver.save_publications(url, pubs, is_seed=is_seed)
    FeedEventEmitter.emit_new_paper_events(results, url, is_seed=is_seed, event_date=event_date)

    if reconcile_titles:
        renames = PaperSaver.reconcile_title_renames(url, pubs)
        for r in renames:
            FeedEventEmitter.emit_title_change(r.paper_id, r.old_title, r.new_title, event_date=event_date)

    match_and_save_paper_links(url_id, pubs)
    _append_snapshots(pubs, url, event_date)


_STALENESS_YEARS = 10

_INTERESTING_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("working_paper", "revise_and_resubmit"),
    ("working_paper", "accepted"),
    ("working_paper", "published"),
    ("work_in_progress", "revise_and_resubmit"),
    ("work_in_progress", "accepted"),
    ("work_in_progress", "published"),
    ("revise_and_resubmit", "accepted"),
    ("revise_and_resubmit", "published"),
})


def _append_snapshots(pubs: list[dict], source_url: str, event_date=None) -> None:
    """Append paper snapshots and emit status_change events when detected."""
    hash_to_pub = {}
    for pub in pubs:
        title = pub.get('title')
        if title:
            hash_to_pub[compute_title_hash(title)] = pub
    if not hash_to_pub:
        return

    current_year = datetime.now(timezone.utc).year

    placeholders = ",".join(["%s"] * len(hash_to_pub))
    rows = fetch_all(
        f"SELECT id, title_hash FROM papers WHERE title_hash IN ({placeholders})",
        list(hash_to_pub.keys()),
    )
    for row in rows:
        pub = hash_to_pub[row['title_hash']]
        result = append_paper_snapshot(
            paper_id=row['id'],
            status=pub.get('status'),
            venue=pub.get('venue'),
            abstract=pub.get('abstract'),
            draft_url=pub.get('draft_url'),
            year=pub.get('year'),
            source_url=source_url,
            title=pub.get('title'),
        )
        if result.status_changed:
            pub_year = pub.get('year')
            if pub_year and pub_year.isdigit() and (current_year - int(pub_year)) > _STALENESS_YEARS:
                logger.info(
                    "Suppressed status_change for old paper (year=%s): %s",
                    pub_year, pub.get('title', '')[:60],
                )
                continue
            if (result.old_status, result.new_status) not in _INTERESTING_TRANSITIONS:
                logger.debug(
                    "Suppressed uninteresting transition %s→%s: %s",
                    result.old_status, result.new_status, pub.get('title', '')[:60],
                )
                continue
            FeedEventEmitter.emit_status_change(
                row['id'], result.old_status, result.new_status, event_date=event_date,
            )


def _extract_full(url_id: int, url: str, text: str, is_seed: bool,
                  content_hash: str, scrape_log_id: int | None,
                  fetch_date=None) -> ExtractionOutcome:
    """Original full-page extraction path."""
    result = Publication.try_extract_publications(text, url, scrape_log_id=scrape_log_id)
    if result.pubs is None:
        return ExtractionOutcome("failed", retry_after=result.retry_after)

    if result.pubs:
        persist_extraction(url, url_id, result.pubs, is_seed=is_seed, event_date=fetch_date)

    HTMLFetcher.mark_extracted(url_id, content_hash)
    return ExtractionOutcome("extracted" if result.pubs else "empty", pubs_count=len(result.pubs))


def _extract_via_diff(url_id: int, url: str, new_text: str, old_text: str,
                      content_hash: str, scrape_log_id: int | None,
                      fetch_date=None) -> ExtractionOutcome:
    """Diff-based extraction: compare old and new page, extract only changes."""
    result = Publication.try_extract_changes(old_text, new_text, url, scrape_log_id=scrape_log_id)
    if result.pubs is None:
        return ExtractionOutcome("failed", retry_after=result.retry_after)

    count = _process_diff_changes(result.pubs, url, url_id, fetch_date)

    HTMLFetcher.mark_extracted(url_id, content_hash)
    return ExtractionOutcome("extracted" if count > 0 else "empty", pubs_count=count)


def _process_diff_changes(changes: list[dict], url: str, url_id: int,
                          fetch_date=None) -> int:
    """Process structured change events from diff extraction.

    Handles new_paper, status_change, and title_change events. Returns
    the number of actionable changes processed.
    """
    new_pubs = [c for c in changes if c['change_type'] == 'new_paper']
    status_changes = [c for c in changes if c['change_type'] == 'status_change']
    title_changes = [c for c in changes if c['change_type'] == 'title_change']

    if new_pubs:
        persist_extraction(
            url, url_id, new_pubs, is_seed=False, event_date=fetch_date,
            reconcile_titles=False,
        )

    if status_changes:
        _append_snapshots(status_changes, url, event_date=fetch_date)

    for tc in title_changes:
        _apply_title_change(tc, url, fetch_date)

    return len(new_pubs) + len(status_changes) + len(title_changes)


def _apply_title_change(change: dict, source_url: str, event_date) -> None:
    """Apply a title change detected by diff extraction."""
    old_title = change.get('old_title')
    new_title = change.get('title')
    if not old_title or not new_title:
        return

    if not validate_title_change(old_title, new_title):
        logger.info("Suppressed spurious diff title change: '%s' → '%s'",
                     old_title[:50], new_title[:50])
        return

    old_hash = compute_title_hash(old_title)
    row = fetch_one("SELECT id FROM papers WHERE title_hash = %s", (old_hash,))
    if not row:
        logger.warning("Title change for unknown paper: %s -> %s", old_title, new_title)
        return

    paper_id = row['id']
    PaperSaver.apply_title_rename(paper_id, old_title, new_title, change, source_url)
    FeedEventEmitter.emit_title_change(paper_id, old_title, new_title, event_date=event_date)


def _update_home_description(researcher_id: int, text: str, url: str,
                             scrape_log_id: int | None = None) -> None:
    """Best-effort researcher description refresh from a HOME page."""
    try:
        description = HTMLFetcher.extract_description(text, url, scrape_log_id=scrape_log_id)
        if not description:
            return
        r_row = fetch_one(
            "SELECT position, affiliation FROM researchers WHERE id = %s",
            (researcher_id,),
        )
        position = r_row['position'] if r_row else None
        affiliation = r_row['affiliation'] if r_row else None
        append_researcher_snapshot(
            researcher_id, position, affiliation, description, source_url=url,
        )
    except Exception as e:
        logger.error("Description update failed for %s: %s: %s", url, type(e).__name__, e)
