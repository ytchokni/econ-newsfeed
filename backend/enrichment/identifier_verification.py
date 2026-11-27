"""Re-verify OpenAlex identifiers set before the title check (#244, #266).

Before 16 Oct 2026, enrich_publication accepted a DOI from the page's links
without checking the work's title, and the link extractor attaches a link to
the nearest title. Some papers therefore carry the DOI/OpenAlex ID of a
neighbouring entry. get_unenriched_papers never revisits a paper once it has
an openalex_id, so those rows need a one-off re-check.

Each candidate's work is fetched by ID (50 per filter request, charged to the
shared daily OpenAlex budget) and classified:

- verified: the work's title and an author match the paper.
- cleared: clear evidence the work is another one: no shared author surname,
  a book whose title is not the paper's, or a title that exactly matches a
  different paper in the database. The DOI, OpenAlex ID, OpenAlex coauthors
  and topics, an abstract copied from the work, and the matching
  paper_links.doi are removed; the paper becomes eligible for enrichment again.
- review: anything else (OpenAlex often keeps a working-paper title).

Every decision is recorded in openalex_identifier_checks, which doubles as the
review list and makes the job resumable.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass

import requests

from backend.database import fetch_all, get_connection
from backend.database.papers import find_papers_by_titles, has_title_identity, normalize_title
from backend.database.researchers import matches_author_name
from backend.enrichment import openalex
from backend.enrichment.doi_resolver import normalize_doi

logger = logging.getLogger(__name__)

# #244 (title check before accepting a DOI) deployed on 15 Oct 2026.
CUTOFF = '2026-10-16'
BATCH_SIZE = 50
_SELECT = 'id,doi,title,display_name,type,authorships,abstract_inverted_index,publication_year'
_BOOK_TYPES = {'book', 'edited-book', 'monograph', 'reference-book', 'book-set', 'book-series'}


@dataclass
class Verdict:
    verdict: str  # 'verified' | 'cleared' | 'review'
    reason: str
    matched_paper_id: int | None = None
    clear_abstract: bool = False


def _fold(text: str) -> str:
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).casefold()


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.split(r'[^\w]+', _fold(name)) if len(t) > 1}


def _surname_overlap(authors: list[dict], work_names: list[str]) -> bool:
    """Any of our authors' surname tokens in any OpenAlex author name."""
    work_tokens = set().union(*(_name_tokens(n) for n in work_names)) if work_names else set()
    for author in authors:
        surname = _name_tokens(author.get('last_name') or '')
        if surname and surname <= work_tokens:
            return True
        if surname & work_tokens and len(surname) > 1:
            return True  # compound surnames: "García López" vs "Garcia"
    return False


def _full_name_match(authors: list[dict], work_names: list[str]) -> bool:
    return any(matches_author_name(a.get('first_name') or '', a.get('last_name') or '', name)
               for a in authors for name in work_names if name)


def titles_match(title: str | None, work_title: str | None) -> bool:
    """The same comparison work_matches_paper applies to new enrichments."""
    if not has_title_identity(title) or not has_title_identity(work_title):
        return False
    clean = lambda value: normalize_title(value.replace('&', ' and ')).replace(' ', '')
    return clean(title) == clean(work_title)


def _near_identical(a: str | None, b: str | None) -> bool:
    """Typo-level difference (a dropped or swapped letter, a split word)."""
    from difflib import SequenceMatcher
    na, nb = normalize_title(a), normalize_title(b)
    return bool(na and nb) and SequenceMatcher(None, na, nb).ratio() >= 0.9


def _same_text(a: str | None, b: str | None) -> bool:
    norm = lambda t: re.sub(r'\s+', ' ', t or '').strip().casefold()
    return bool(a and b) and norm(a) == norm(b)


def classify(paper: dict, work: dict | None, authors: list[dict],
             title_owner_id: int | None = None) -> Verdict:
    """Decide what to do with a paper's identifier, given the work it points to.

    `work` is a raw OpenAlex work (or None when OpenAlex no longer has it);
    `authors` are the paper's researchers ({first_name, last_name});
    `title_owner_id` is another paper in our database whose title equals the
    work's title.
    """
    if work is None:
        return Verdict('review', 'work_not_found')
    parsed = openalex._parse_work(work)
    work_title = parsed['title']
    work_names = [n for n in parsed['author_names'] if n]
    title_ok = titles_match(paper.get('title'), work_title)
    abstract_from_work = _same_text(paper.get('abstract'), parsed.get('abstract'))

    if title_ok and _full_name_match(authors, work_names):
        return Verdict('verified', 'title_and_author_match')
    if not title_ok and (work.get('type') or '').lower() in _BOOK_TYPES:
        return Verdict('cleared', 'book_mismatch', clear_abstract=abstract_from_work)
    if not title_ok and title_owner_id and title_owner_id != paper['id']:
        if _near_identical(paper.get('title'), work_title):
            # A typo of the owner's title: a duplicate for the merge, not a wrong ID.
            return Verdict('review', 'near_duplicate_of_other_paper', title_owner_id)
        return Verdict('cleared', 'belongs_to_other_paper', title_owner_id, abstract_from_work)
    if not work_names:
        return Verdict('review', 'work_has_no_authors')
    if not authors:
        return Verdict('review', 'paper_has_no_authors')
    if not _surname_overlap(authors, work_names):
        if title_ok:
            # Same title, no shared surname: a namesake work or a name we cannot match.
            return Verdict('review', 'title_match_no_author_overlap')
        return Verdict('cleared', 'no_author_overlap', clear_abstract=abstract_from_work)
    if title_ok:
        return Verdict('verified', 'title_match_surname_overlap')
    return Verdict('review', 'title_differs')


# --- data access ----------------------------------------------------------------

def get_papers_to_verify(limit: int, after_id: int = 0) -> list[dict]:
    """Papers whose OpenAlex ID predates the title check and was never re-checked."""
    return fetch_all(
        """SELECT p.id, p.title, p.doi, p.openalex_id, p.abstract
           FROM papers p
           WHERE p.openalex_id IS NOT NULL AND p.id > %s
             AND (p.enrichment_attempted_at IS NULL OR p.enrichment_attempted_at < %s)
             AND NOT EXISTS (
                 SELECT 1 FROM openalex_identifier_checks c
                 WHERE c.paper_id = p.id AND c.openalex_id = p.openalex_id)
           ORDER BY p.id
           LIMIT %s""",
        (after_id, CUTOFF, limit),
    )


def count_papers_to_verify() -> int:
    rows = fetch_all(
        """SELECT COUNT(*) AS n FROM papers p
           WHERE p.openalex_id IS NOT NULL
             AND (p.enrichment_attempted_at IS NULL OR p.enrichment_attempted_at < %s)
             AND NOT EXISTS (
                 SELECT 1 FROM openalex_identifier_checks c
                 WHERE c.paper_id = p.id AND c.openalex_id = p.openalex_id)""",
        (CUTOFF,),
    )
    return int(rows[0]['n']) if rows else 0


def _authors_for(paper_ids: list[int]) -> dict[int, list[dict]]:
    if not paper_ids:
        return {}
    rows = fetch_all(
        f"""SELECT a.publication_id, r.first_name, r.last_name
            FROM authorship a JOIN researchers r ON r.id = a.researcher_id
            WHERE a.publication_id IN ({','.join(['%s'] * len(paper_ids))})""",
        tuple(paper_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in paper_ids}
    for row in rows:
        result[row['publication_id']].append(row)
    return result


def _title_owners(work_titles: list[str]) -> dict[str, int]:
    """Map a work title to the one paper in our database with exactly that title."""
    return {title: row['id'] for title, row in find_papers_by_titles(work_titles).items()}


# --- OpenAlex -------------------------------------------------------------------

class BudgetExhausted(Exception):
    pass


def fetch_works(openalex_ids: list[str], cache: dict | None = None) -> dict[str, dict | None]:
    """Fetch works by ID, 50 per budgeted filter request.

    IDs the filter does not return (merged or deleted works) fall back to a
    singleton lookup, which follows OpenAlex's merge redirect and, like
    lookup_by_doi, is not charged to the daily budget. `cache` maps ID -> work
    (or None) and is filled in place so a dry run and the following --apply
    run spend the budget once.
    """
    cache = {} if cache is None else cache
    wanted = [oid for oid in dict.fromkeys(openalex_ids) if oid not in cache]
    session = openalex._get_session()
    for start in range(0, len(wanted), BATCH_SIZE):
        chunk = wanted[start:start + BATCH_SIZE]
        params = {
            'filter': 'openalex:' + '|'.join(chunk),
            'per_page': BATCH_SIZE,
            'select': _SELECT,
        }
        if openalex.MAILTO:
            params['mailto'] = openalex.MAILTO
        try:
            resp = openalex._get_with_retry(session, f'{openalex.OPENALEX_BASE_URL}/works', params)
            resp.raise_for_status()
            results = resp.json().get('results', [])
        except openalex._BudgetExhausted as exc:
            raise BudgetExhausted from exc
        found = {}
        for work in results:
            oid = openalex._strip_prefix(work.get('id'), openalex._OPENALEX_PREFIX)
            if oid:
                found[oid.upper()] = work
        for oid in chunk:
            work = found.get(oid.upper())
            if work is None:
                work = _fetch_single(session, oid)
            if work is not False:
                cache[oid] = work
        time.sleep(0.2)
    return {oid: cache.get(oid) for oid in openalex_ids if oid in cache}


def _fetch_single(session, oid: str):
    """Work dict, None when OpenAlex has no such work, False on a transient error."""
    try:
        resp = session.get(f'{openalex.OPENALEX_BASE_URL}/works/{oid}',
                           params={'select': _SELECT}, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning('OpenAlex lookup failed for %s: %s', oid, exc)
        return False


# --- planning and applying ------------------------------------------------------

def plan_batch(papers: list[dict], works: dict[str, dict | None]) -> list[tuple[dict, dict | None, Verdict]]:
    """Classify a batch of papers against their fetched works."""
    authors = _authors_for([p['id'] for p in papers])
    titles = [openalex._parse_work(w)['title'] for w in works.values() if w]
    owners = _title_owners([t for t in titles if has_title_identity(t)])
    plan = []
    for paper in papers:
        if paper['openalex_id'] not in works:
            continue  # transient fetch failure: retry on the next run
        work = works[paper['openalex_id']]
        owner = None
        if work:
            owner = owners.get(openalex._parse_work(work)['title'])
        plan.append((paper, work, classify(paper, work, authors.get(paper['id'], []), owner)))
    return plan


def apply_verdict(paper: dict, work: dict | None, verdict: Verdict) -> bool:
    """Record the decision; on 'cleared', remove the identifier and what came with it.

    Skips (returns False) when the paper's identifier changed since planning.
    """
    work_title = openalex._parse_work(work)['title'] if work else None
    work_doi = openalex._strip_prefix((work or {}).get('doi'), openalex._DOI_PREFIX)
    paper_doi = normalize_doi(paper.get('doi'))
    # Clear the paper's DOI only when it is this work's DOI (it came from this match).
    work_doi = normalize_doi(work_doi)
    clear_doi = bool(paper_doi and work_doi and paper_doi.casefold() == work_doi.casefold())
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT openalex_id, doi, abstract FROM papers WHERE id = %s FOR UPDATE', (paper['id'],))
            row = cursor.fetchone()
            if not row or row['openalex_id'] != paper['openalex_id']:
                conn.rollback()
                return False
            cleared_doi = row['doi'] if verdict.verdict == 'cleared' and clear_doi else None
            if verdict.verdict == 'cleared':
                cursor.execute(
                    """UPDATE papers SET openalex_id = NULL, doi = IF(%s, NULL, doi),
                           abstract = IF(%s, NULL, abstract), enrichment_attempted_at = NULL
                       WHERE id = %s""",
                    (bool(cleared_doi), verdict.clear_abstract, paper['id']),
                )
                cursor.execute('DELETE FROM openalex_coauthors WHERE paper_id = %s', (paper['id'],))
                cursor.execute('DELETE FROM paper_topics WHERE paper_id = %s', (paper['id'],))
                wrong = {d.casefold() for d in (normalize_doi(row['doi']), normalize_doi(work_doi)) if d}
                if wrong:
                    cursor.execute('SELECT id, doi FROM paper_links WHERE paper_id = %s AND doi IS NOT NULL FOR UPDATE',
                                   (paper['id'],))
                    for link in cursor.fetchall():
                        if (normalize_doi(link['doi']) or '').casefold() in wrong:
                            cursor.execute('UPDATE paper_links SET doi = NULL WHERE id = %s', (link['id'],))
            cursor.execute(
                """INSERT INTO openalex_identifier_checks
                       (paper_id, openalex_id, doi, verdict, reason, work_title, matched_paper_id,
                        cleared_abstract, checked_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                   ON DUPLICATE KEY UPDATE doi = VALUES(doi), verdict = VALUES(verdict),
                       reason = VALUES(reason), work_title = VALUES(work_title),
                       matched_paper_id = VALUES(matched_paper_id),
                       cleared_abstract = VALUES(cleared_abstract), checked_at = VALUES(checked_at)""",
                # doi names the work's DOI: for a cleared row it is no longer this
                # paper's evidence (rename_merge excludes it).
                (paper['id'], paper['openalex_id'], work_doi, verdict.verdict, verdict.reason,
                 (work_title or '')[:1000] or None, verdict.matched_paper_id,
                 verdict.verdict == 'cleared' and verdict.clear_abstract),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
