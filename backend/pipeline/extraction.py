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

Pages longer than CONTENT_MAX_CHARS are never truncated: the full path splits
them into chunks (backend.pipeline.chunking) and merges the results, and the
diff path sends only the changed sections. Before chunking, text past that
length was cut off, so long pages carry papers that were never extracted;
see _backfill_long_page.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.database import (
    fetch_one, fetch_all, compute_title_hash, append_paper_snapshot,
    append_researcher_snapshot,
)
from backend.pipeline.change_filter import is_insignificant_change
from backend.pipeline.feed_events import FeedEventEmitter
from backend.database.identity import SHARED_PAGE, page_ownership_problem
from backend.database.researchers import deactivate_unowned_url, flag_url_ownership, page_owners
from backend.database.papers import find_papers_by_titles
from backend.pipeline.chunking import chunk_page
from backend.pipeline.html_fetcher import CONTENT_MAX_CHARS, HTMLFetcher
from backend.enrichment.link_extractor import match_and_save_paper_links
from backend.pipeline.paper_saver import PaperSaver, validate_title_change
from backend.pipeline.publication import Publication
from backend.publication_rules import (
    BACKFILL_STATUS_CHANGE_MAX_AGE_YEARS, INTERESTING_STATUS_TRANSITIONS, status_change_suppression_reason,
)

logger = logging.getLogger(__name__)


@dataclass
class ExtractionOutcome:
    """Result of extracting one URL.

    status values:
      'extracted'  — publications found and saved; URL marked extracted
      'empty'      — no publications, or a decoded JPEG was skipped; URL marked extracted
      'failed'     — LLM call failed; URL NOT marked, will be retried
      'no_content' — no stored HTML/text; URL NOT marked
    """
    status: str
    pubs_count: int = 0
    retry_after: float | None = None
    # Failed on the provider's side (5xx/connection), not because of the page.
    provider_error: bool = False

    @property
    def ok(self) -> bool:
        return self.status in ("extracted", "empty")


# researcher_urls.page_type has mixed case in production: older imports wrote
# 'home', the site miner writes 'HOME'. MySQL compares them equal, Python does not.
_HOME_PAGE_TYPES = frozenset({"HOME", "HOMEPAGE"})


def is_home_page(page_type: str | None) -> bool:
    """True for a researcher's home page, whatever the stored case."""
    return (page_type or "").strip().upper() in _HOME_PAGE_TYPES


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
    raw_html = payload.get('raw_html')
    if not raw_html and not text:
        raw_html = HTMLFetcher.get_raw_html(url_id)
    if raw_html:
        # Re-clean caches written before boilerplate filtering was introduced.
        raw_text = HTMLFetcher.extract_text_content(raw_html)
        text = HTMLFetcher.normalize_text(raw_text)
        # Earlier extractions only saw raw_text[:CONTENT_MAX_CHARS].
        was_truncated = len(raw_text) > CONTENT_MAX_CHARS
    else:
        was_truncated = len(text or '') > CONTENT_MAX_CHARS
    content_hash = payload['content_hash']
    if not text:
        if raw_html:
            HTMLFetcher.mark_extracted(url_id, content_hash)
            return ExtractionOutcome("empty")
        return ExtractionOutcome("no_content")
    # Some researcher URLs serve a JPEG that was decoded and stored as text.
    # Recognize its explicit header, without treating non-ASCII prose as binary.
    header = text[:64]
    if 'JFIF\x00' in header or 'Exif\x00\x00' in header:
        logger.info("Skipping decoded JPEG for URL ID %s", url_id)
        HTMLFetcher.mark_extracted(url_id, content_hash)
        return ExtractionOutcome("empty")
    # Department, institute and news pages are not personal pages: their
    # items must not be credited to whoever tracks them (issue #262).
    # Shared pages stop being crawled. A page that never names its owner may
    # still be theirs (Google Sites, Cyrillic profiles): it is extracted, the
    # owner is not attached at position 0, and it is flagged for review.
    owners = page_owners(url)
    problem = page_ownership_problem(owners, text, raw_html, url, page_type) if owners else None
    if problem == SHARED_PAGE:
        deactivate_unowned_url(url, problem)
        HTMLFetcher.mark_extracted(url_id, content_hash)
        return ExtractionOutcome("empty")
    try:
        if owners:
            flag_url_ownership(url, problem)
    except Exception:
        logger.warning("Could not record ownership flag for %s", url, exc_info=True)
    is_seed = payload['extracted_at'] is None

    ts = payload.get('timestamp')
    if ts and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    fetch_date = ts

    previous_text = None if is_seed else HTMLFetcher.get_previous_text(url_id)
    # The last extraction ran before chunking and saw only the first
    # CONTENT_MAX_CHARS characters: the rest of the page was never extracted.
    tail_never_extracted = (was_truncated and not is_seed
                            and not payload.get('extracted_untruncated'))

    if previous_text is not None and is_insignificant_change(
            HTMLFetcher.normalize_text(previous_text), HTMLFetcher.normalize_text(text)):
        # Checked before the skip below: an unchanged long page whose tail was
        # never extracted still needs its backfill.
        if tail_never_extracted:
            return _backfill_long_page(url_id, url, text, raw_html, content_hash,
                                       scrape_log_id, fetch_date)
        # Cleaning/hash changes, spacing and counters alone must not cost a
        # call or create fresh publication events.
        HTMLFetcher.mark_extracted(url_id, content_hash)
        return ExtractionOutcome("empty")

    if previous_text is not None and tail_never_extracted:
        # A diff would send only the changed hunks and the old tail would
        # stay unextracted for good. Extract the whole page in chunks once,
        # as a non-seed extraction: new_paper events for tail papers are
        # suppressed because their titles are in the previous snapshot
        # (_title_in_previous_snapshot), and baseline-less URLs get none.
        logger.info("Long page %s changed before its tail was extracted; extracting in full", url)
        outcome = _extract_full(url_id, url, text, False, content_hash,
                                scrape_log_id, fetch_date, raw_html=raw_html)
    elif previous_text is not None:
        outcome = _extract_via_diff(url_id, url, text, previous_text, content_hash,
                                    scrape_log_id, fetch_date, raw_html=raw_html)
    else:
        outcome = _extract_full(url_id, url, text, is_seed, content_hash,
                                scrape_log_id, fetch_date, raw_html=raw_html)

    if outcome.ok and is_home_page(page_type):
        _update_home_description(researcher_id, text, url, scrape_log_id)

    return outcome


def persist_extraction(url: str, url_id: int, pubs: list[dict],
                       is_seed: bool = False, event_date=None,
                       reconcile_titles: bool = True,
                       existing_status_events: bool = False) -> None:
    """Persist extraction results in one call.

    Absorbs the full post-extraction sequence: save papers, emit feed events,
    reconcile title renames, match/save links, append snapshots.  Both
    extract_one_url() delegates here.

    existing_status_events: on a seed extraction, still emit status_change
    events for papers that were in the database before this extraction
    (the long-page backfill, see _backfill_long_page). Papers it creates stay
    silent, and new_paper / title_change events are never emitted for a seed.
    """
    results = PaperSaver.save_publications(url, pubs, is_seed=is_seed)
    FeedEventEmitter.emit_new_paper_events(results, url, is_seed=is_seed, event_date=event_date)

    if reconcile_titles:
        renames = PaperSaver.reconcile_title_renames(url, pubs)
        for r in renames:
            if not is_seed:
                FeedEventEmitter.emit_title_change(r.paper_id, r.old_title, r.new_title,
                                                  event_date=event_date, source_url=url)

    match_and_save_paper_links(url_id, pubs)
    if not is_seed:
        _append_snapshots(pubs, url, event_date)
    elif existing_status_events:
        _append_snapshots(pubs, url, event_date, event_paper_ids=_preexisting_paper_ids(results))
    else:
        _append_snapshots(pubs, url, event_date, emit_events=False)


def _preexisting_paper_ids(results) -> set[int]:
    """Ids of saved papers that existed before this extraction created any.

    A title listed twice is saved twice: the first save creates the paper
    (is_new) and the second finds it. It is still new, so ids created by any
    save are excluded.
    """
    created = {r.paper_id for r in results if r.is_new}
    return {r.paper_id for r in results if r.paper_id not in created}


def _append_snapshots(pubs: list[dict], source_url: str, event_date=None, *, emit_events=True,
                      event_paper_ids: set[int] | None = None) -> None:
    """Append paper snapshots and emit status_change events when detected.

    event_paper_ids, when given, limits events to those papers (the
    long-page backfill); every paper still gets its snapshot. Those events
    also need a recent year for every status
    (BACKFILL_STATUS_CHANGE_MAX_AGE_YEARS), not only for "published".
    """
    max_age_years = BACKFILL_STATUS_CHANGE_MAX_AGE_YEARS if event_paper_ids is not None else None
    title_to_pub = {}
    for pub in pubs:
        title = pub.get('title')
        if title:
            title_to_pub[title] = pub
    if not title_to_pub:
        return

    reference_year = (event_date or datetime.now(timezone.utc)).year

    for title, row in find_papers_by_titles(list(title_to_pub)).items():
        pub = title_to_pub[title]
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
        if (result.status_changed and emit_events
                and (event_paper_ids is None or row['id'] in event_paper_ids)):
            if (result.old_status, result.new_status) not in INTERESTING_STATUS_TRANSITIONS:
                logger.debug(
                    "Suppressed uninteresting transition %s→%s: %s",
                    result.old_status, result.new_status, pub.get('title', '')[:60],
                )
                continue
            # Blog posts, policy briefs and long-published items are not news (#264).
            reason = status_change_suppression_reason(
                result.new_status, result.venue,
                pub.get('year') or getattr(result, 'year', None), reference_year,
                max_age_years=max_age_years,
            )
            if reason:
                logger.info(
                    "Suppressed status_change %s→%s (%s): %s",
                    result.old_status, result.new_status, reason, pub.get('title', '')[:60],
                )
                continue
            FeedEventEmitter.emit_status_change(
                row['id'], result.old_status, result.new_status, event_date=event_date,
                venue=result.venue, source_url=source_url,
            )


def _failed(retry_after: float | None, provider_error: bool = False) -> ExtractionOutcome:
    """Failed outcome; a provider refusal is recorded so JEL classification yields."""
    if retry_after is not None:
        from backend.database.llm import record_llm_quota_limit
        record_llm_quota_limit("extraction", retry_after)
    return ExtractionOutcome("failed", retry_after=retry_after, provider_error=provider_error)


def _backfill_long_page(url_id: int, url: str, text: str, raw_html: str | None,
                        content_hash: str, scrape_log_id: int | None,
                        fetch_date=None) -> ExtractionOutcome:
    """Extract a long page whose text has not changed, as a seed extraction.

    The fetcher used to store and hash only the first CONTENT_MAX_CHARS
    characters of extracted text, and extraction saw no more than that.
    Hashing the full text re-queues every longer page once, with
    text identical to its last snapshot, and papers past that point have
    never been extracted. They have been on the page all along, so papers
    this extraction creates are saved as seed papers with no events, and no
    new_paper event is emitted. A paper the database already had whose
    status advanced (a working paper now accepted at a journal) still gets its
    status_change event under the usual rules (_append_snapshots), except
    that every status, not only "published", must be at most
    BACKFILL_STATUS_CHANGE_MAX_AGE_YEARS old: the backfill is the first
    extraction to see the page's current entry for it, which may be old.
    It runs once per page: mark_extracted sets
    html_content.extracted_untruncated.
    """
    logger.info("Backfilling long unchanged page %s (%d chars) as seed", url, len(text))
    return _extract_full(url_id, url, text, True, content_hash, scrape_log_id,
                         fetch_date, raw_html=raw_html, existing_status_events=True)


def _extract_full(url_id: int, url: str, text: str, is_seed: bool,
                  content_hash: str, scrape_log_id: int | None,
                  fetch_date=None, raw_html: str | None = None,
                  existing_status_events: bool = False) -> ExtractionOutcome:
    """Full-page extraction path, in chunks for pages over CONTENT_MAX_CHARS."""
    chunks = chunk_page(text, raw_html, CONTENT_MAX_CHARS)
    if len(chunks) > 1:
        result = Publication.try_extract_publications(text, url, scrape_log_id=scrape_log_id,
                                                      chunks=chunks)
    else:
        result = Publication.try_extract_publications(text, url, scrape_log_id=scrape_log_id)
    if result.pubs is None:
        return _failed(result.retry_after, result.provider_error)

    if result.pubs:
        persist_extraction(url, url_id, result.pubs, is_seed=is_seed, event_date=fetch_date,
                           existing_status_events=existing_status_events)

    HTMLFetcher.mark_extracted(url_id, content_hash)
    return ExtractionOutcome("extracted" if result.pubs else "empty", pubs_count=len(result.pubs))


def _extract_via_diff(url_id: int, url: str, new_text: str, old_text: str,
                      content_hash: str, scrape_log_id: int | None,
                      fetch_date=None, raw_html: str | None = None) -> ExtractionOutcome:
    """Diff-based extraction: compare old and new page, extract only changes.

    A rewritten page too long to send as old + new text is extracted in full,
    in chunks, as a non-seed extraction. Feed events stay guarded there:
    new_paper needs baseline snapshots and is suppressed for titles found in
    the previous snapshot (FeedEventEmitter.emit_new_paper_events), and
    status changes go through the same snapshot comparison as the diff path.
    """
    result = Publication.try_extract_changes(old_text, new_text, url, scrape_log_id=scrape_log_id)
    if result.needs_full_extraction:
        return _extract_full(url_id, url, new_text, False, content_hash, scrape_log_id,
                             fetch_date, raw_html=raw_html)
    if result.pubs is None:
        return _failed(result.retry_after, result.provider_error)

    try:
        count = _process_diff_changes(result.pubs, url, url_id, fetch_date,
                                      old_text=old_text, new_text=new_text)
    except Exception as e:
        # A failed outcome (not an exception) lets the worker persist the
        # failure (html_content.extraction_*), so the page backs off across
        # restarts instead of paying for the same LLM answer after each one.
        logger.exception("Saving diff changes for %s failed: %s: %s", url, type(e).__name__, e)
        return _failed(None)

    HTMLFetcher.mark_extracted(url_id, content_hash)
    return ExtractionOutcome("extracted" if count > 0 else "empty", pubs_count=count)


def _process_diff_changes(changes: list[dict], url: str, url_id: int,
                          fetch_date=None, *, old_text: str | None = None,
                          new_text: str | None = None) -> int:
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
        _apply_title_change(tc, url, fetch_date, old_text=old_text, new_text=new_text)

    return len(new_pubs) + len(status_changes) + len(title_changes)


def _rename_has_page_evidence(old_title: str, new_title: str,
                              old_text: str | None, new_text: str | None) -> bool:
    """Whether the page itself shows old_title replaced by new_title.

    The LLM sometimes reports a "rename" between two papers the page lists
    side by side. Only a page where the new title was absent before and the
    old title is gone now may merge two existing papers under the new title.
    """
    from backend.database.papers import normalize_title
    if not old_text or not new_text:
        return False
    old_page = f" {normalize_title(old_text)} "
    new_page = f" {normalize_title(new_text)} "
    return (f" {normalize_title(new_title)} " not in old_page
            and f" {normalize_title(old_title)} " not in new_page)


def _apply_title_change(change: dict, source_url: str, event_date, *,
                        old_text: str | None = None, new_text: str | None = None) -> None:
    """Apply a title change detected by diff extraction."""
    old_title = change.get('old_title')
    new_title = change.get('title')
    if not old_title or not new_title:
        return

    if not validate_title_change(old_title, new_title):
        logger.info("Suppressed spurious diff title change: '%s' → '%s'",
                     old_title[:50], new_title[:50])
        return

    row = find_papers_by_titles([old_title]).get(old_title)
    if not row:
        logger.warning("Title change for unknown paper: %s -> %s", old_title, new_title)
        return

    paper_id = row['id']
    # A collision with another paper's title is resolved inside (merge or
    # skip) and never raises; other errors fail the page with backoff.
    outcome = PaperSaver.apply_title_rename(
        paper_id, old_title, new_title, change, source_url,
        allow_merge=_rename_has_page_evidence(old_title, new_title, old_text, new_text),
    )
    if outcome == PaperSaver.RENAMED:
        FeedEventEmitter.emit_title_change(paper_id, old_title, new_title,
                                          event_date=event_date, source_url=source_url)


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
