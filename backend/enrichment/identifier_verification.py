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


_GERMAN = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', 'Ä': 'Ae', 'Ö': 'Oe', 'Ü': 'Ue'})


def _name_tokens(name: str) -> set[str]:
    """Tokens under both foldings: Müller -> {muller, mueller}."""
    tokens = set()
    for text in (name or '', (name or '').translate(_GERMAN)):
        tokens |= {t for t in re.split(r'[^\w]+', _fold(text)) if len(t) > 1}
    return tokens


def _romanisation_ambiguous(authors: list[dict], work_names: list[str]) -> bool:
    """A work author with our author's first name, or initial plus a similar
    surname (Chou/Zhou), may be the same person under another spelling."""
    from difflib import SequenceMatcher
    for author in authors:
        first = sorted(_name_tokens((author.get('first_name') or '').split(' ')[0]) or {''})[0]
        last = _fold(author.get('last_name') or '')
        for name in work_names:
            parts = _fold(name).replace('.', ' ').split()
            if len(parts) < 2 or not first:
                continue
            if parts[0] == first:
                return True
            if parts[0][0] == first[0] and SequenceMatcher(None, parts[-1], last).ratio() >= 0.5:
                return True
    return False


def _book_title_variant(title: str | None, book_title: str | None) -> bool:
    """The paper names the book itself: equal heads before ':'/',' or one a prefix."""
    head = lambda t: normalize_title(re.split(r'[:,]', t or '', maxsplit=1)[0])
    a, b = head(title), head(book_title)
    if len(a.split()) < 2 or len(b.split()) < 2:
        return False
    return a.startswith(b) or b.startswith(a)


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


def abstract_hash(text: str | None) -> str | None:
    """Whitespace- and case-insensitive fingerprint of an abstract."""
    import hashlib
    norm = re.sub(r'\s+', ' ', text or '').strip().casefold()
    return hashlib.sha256(norm.encode('utf-8')).hexdigest() if norm else None


def compact_work(work: dict) -> dict:
    """The fields re-verification needs (cached in openalex_work_cache).

    The abstract is kept only as a hash: it is compared, never stored.
    """
    if 'author_names' in work and 'authorships' not in work:
        return work
    parsed = openalex._parse_work(work)
    return {
        'id': parsed['openalex_id'],
        'doi': parsed['doi'],
        'title': parsed['title'],
        'type': (work.get('type') or '').lower() or None,
        'author_names': [n for n in parsed['author_names'] if n],
        'abstract_hash': abstract_hash(parsed.get('abstract')),
    }


def classify(paper: dict, work: dict | None, authors: list[dict],
             title_owner_id: int | None = None) -> Verdict:
    """Decide what to do with a paper's identifier, given the work it points to.

    `work` is a raw or compact OpenAlex work (None when OpenAlex has no such work);
    `authors` are the paper's researchers ({first_name, last_name});
    `title_owner_id` is another paper in our database whose title equals the
    work's title.
    """
    if work is None:
        return Verdict('review', 'work_not_found')
    work = compact_work(work)
    work_title = work['title']
    work_names = [n for n in work['author_names'] if n]
    title_ok = titles_match(paper.get('title'), work_title)
    abstract_from_work = bool(work.get('abstract_hash')) and \
        abstract_hash(paper.get('abstract')) == work['abstract_hash']

    if title_ok and _full_name_match(authors, work_names):
        return Verdict('verified', 'title_and_author_match')
    if not title_ok and (work.get('type') or '') in _BOOK_TYPES:
        if _surname_overlap(authors, work_names) and _book_title_variant(paper.get('title'), work_title):
            # The author's own book or volume with a subtitle/volume number.
            return Verdict('review', 'book_title_variant')
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
        if _romanisation_ambiguous(authors, work_names):
            return Verdict('review', 'possible_name_variant')
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


# --- OpenAlex and the work cache ------------------------------------------------

class BudgetExhausted(Exception):
    pass


def load_cached_works(openalex_ids: list[str]) -> dict[str, dict | None]:
    """Works already fetched (None = OpenAlex has no such work)."""
    import json
    ids = list(dict.fromkeys(openalex_ids))
    found: dict[str, dict | None] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = fetch_all(
            f"SELECT openalex_id, work FROM openalex_work_cache WHERE openalex_id IN ({','.join(['%s'] * len(chunk))})",
            tuple(chunk),
        )
        for row in rows:
            found[row['openalex_id']] = json.loads(row['work']) if row['work'] else None
    return found


def store_cached_works(works: dict[str, dict | None]) -> None:
    import json
    if not works:
        return
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO openalex_work_cache (openalex_id, work, fetched_at)
                   VALUES (%s, %s, UTC_TIMESTAMP())
                   ON DUPLICATE KEY UPDATE work = VALUES(work), fetched_at = VALUES(fetched_at)""",
                [(oid, json.dumps(work) if work else None) for oid, work in works.items()],
            )
        conn.commit()


def purge_work_cache() -> int:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM openalex_work_cache')
            deleted = cursor.rowcount
        conn.commit()
    return deleted


def fetch_works(openalex_ids: list[str], cache: dict | None = None,
                persist: bool = False) -> dict[str, dict | None]:
    """Compact works by ID, 50 per budgeted filter request.

    IDs the filter does not return (merged or deleted works) fall back to a
    singleton lookup, which follows OpenAlex's merge redirect and, like
    lookup_by_doi, is not charged to the daily budget. `cache` maps ID -> work
    (or None) and is filled in place. With persist=True the cache is also read
    from and written to openalex_work_cache after every request, so budget spent
    survives a crash, a deploy, and the dry run before --apply.
    """
    cache = {} if cache is None else cache
    if persist:
        cache.update(load_cached_works([oid for oid in openalex_ids if oid not in cache]))
    wanted = [oid for oid in dict.fromkeys(openalex_ids) if oid not in cache]
    session = openalex._get_session() if wanted else None
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
        fetched = {}
        for oid in chunk:
            work = found.get(oid.upper())
            if work is None:
                work = _fetch_single(session, oid)
            if work is not False:
                fetched[oid] = compact_work(work) if work else None
        cache.update(fetched)
        if persist:
            store_cached_works(fetched)
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
    works = {oid: compact_work(w) if w else None for oid, w in works.items()}
    authors = _authors_for([p['id'] for p in papers])
    owners = _title_owners([w['title'] for w in works.values() if w and has_title_identity(w['title'])])
    plan = []
    for paper in papers:
        if paper['openalex_id'] not in works:
            continue  # transient fetch failure: retry on the next run
        work = works[paper['openalex_id']]
        owner = owners.get(work['title']) if work else None
        plan.append((paper, work, classify(paper, work, authors.get(paper['id'], []), owner)))
    return plan


def _json(value) -> str:
    import json
    return json.dumps(value, default=str)


def apply_verdict(paper: dict, work: dict | None, verdict: Verdict) -> bool:
    """Record the decision; on 'cleared', remove the identifier and what came with it.

    Everything a clear removes (DOI, abstract, enrichment_attempted_at, the
    OpenAlex coauthor and topic rows, nulled link DOIs) is saved as JSON in the
    check row's `backup`, which undo_clear restores. Skips (returns False) when
    the paper's identifier changed since planning.
    """
    work = compact_work(work) if work else None
    work_title = work['title'] if work else None
    work_doi = normalize_doi(work['doi']) if work else None
    paper_doi = normalize_doi(paper.get('doi'))
    # Clear the paper's DOI only when it is this work's DOI (it came from this match).
    clear_doi = bool(paper_doi and work_doi and paper_doi.casefold() == work_doi.casefold())
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute('SELECT openalex_id, doi, abstract, enrichment_attempted_at FROM papers '
                           'WHERE id = %s FOR UPDATE', (paper['id'],))
            row = cursor.fetchone()
            if not row or row['openalex_id'] != paper['openalex_id']:
                conn.rollback()
                return False
            backup = None
            if verdict.verdict == 'cleared':
                cursor.execute('SELECT display_name, openalex_author_id FROM openalex_coauthors '
                               'WHERE paper_id = %s ORDER BY id FOR UPDATE', (paper['id'],))
                coauthors = cursor.fetchall()
                cursor.execute('SELECT openalex_topic_id, topic_name, subfield_name, field_name, domain_name, score '
                               'FROM paper_topics WHERE paper_id = %s ORDER BY id FOR UPDATE', (paper['id'],))
                topics = cursor.fetchall()
                nulled_links = []
                if work_doi:
                    # Only the work's (cleared) DOI: the paper's own kept DOI stays on its links.
                    cursor.execute('SELECT id, doi FROM paper_links WHERE paper_id = %s AND doi IS NOT NULL FOR UPDATE',
                                   (paper['id'],))
                    nulled_links = [link for link in cursor.fetchall()
                                    if (normalize_doi(link['doi']) or '').casefold() == work_doi.casefold()]
                backup = {
                    'doi': row['doi'], 'abstract': row['abstract'],
                    'enrichment_attempted_at': row.get('enrichment_attempted_at'),
                    'doi_cleared': clear_doi, 'abstract_cleared': verdict.clear_abstract,
                    'coauthors': coauthors, 'topics': topics, 'links': nulled_links,
                }
                cursor.execute(
                    """UPDATE papers SET openalex_id = NULL, doi = IF(%s, NULL, doi),
                           abstract = IF(%s, NULL, abstract), enrichment_attempted_at = NULL
                       WHERE id = %s""",
                    (clear_doi, verdict.clear_abstract, paper['id']),
                )
                cursor.execute('DELETE FROM openalex_coauthors WHERE paper_id = %s', (paper['id'],))
                cursor.execute('DELETE FROM paper_topics WHERE paper_id = %s', (paper['id'],))
                for link in nulled_links:
                    cursor.execute('UPDATE paper_links SET doi = NULL WHERE id = %s', (link['id'],))
            cursor.execute(
                """INSERT INTO openalex_identifier_checks
                       (paper_id, openalex_id, doi, verdict, reason, work_title, matched_paper_id,
                        cleared_abstract, backup, restored_at, checked_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, UTC_TIMESTAMP())
                   ON DUPLICATE KEY UPDATE doi = VALUES(doi), verdict = VALUES(verdict),
                       reason = VALUES(reason), work_title = VALUES(work_title),
                       matched_paper_id = VALUES(matched_paper_id),
                       cleared_abstract = VALUES(cleared_abstract), backup = VALUES(backup),
                       restored_at = NULL, checked_at = VALUES(checked_at)""",
                # doi names the work's DOI: for a cleared row it is no longer this
                # paper's evidence (rename_merge excludes it).
                (paper['id'], paper['openalex_id'], work_doi, verdict.verdict, verdict.reason,
                 (work_title or '')[:1000] or None, verdict.matched_paper_id,
                 verdict.verdict == 'cleared' and verdict.clear_abstract,
                 _json(backup) if backup is not None else None),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def get_undo_candidates(paper_ids: list[int] | None = None, reason: str | None = None) -> list[dict]:
    """Cleared, not yet restored checks, optionally narrowed by paper or reason."""
    where, params = ["verdict = 'cleared'", 'restored_at IS NULL', 'backup IS NOT NULL'], []
    if paper_ids:
        where.append(f"paper_id IN ({','.join(['%s'] * len(paper_ids))})")
        params.extend(paper_ids)
    if reason:
        where.append('reason = %s')
        params.append(reason)
    return fetch_all(
        f"SELECT paper_id, openalex_id, reason FROM openalex_identifier_checks WHERE {' AND '.join(where)} "
        'ORDER BY paper_id', tuple(params),
    )


def undo_clear(paper_id: int, openalex_id: str) -> str:
    """Restore a clear from its backup. Returns 'restored' or why it was skipped.

    Only while the paper is still unenriched; the check becomes review/undone so
    later runs skip it and it stays on the review list.
    """
    import json
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT backup FROM openalex_identifier_checks
                   WHERE paper_id = %s AND openalex_id = %s AND verdict = 'cleared'
                     AND restored_at IS NULL FOR UPDATE""", (paper_id, openalex_id))
            check = cursor.fetchone()
            if not check or not check['backup']:
                conn.rollback()
                return 'no_backup'
            cursor.execute('SELECT openalex_id FROM papers WHERE id = %s FOR UPDATE', (paper_id,))
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                return 'paper_gone'
            if row['openalex_id']:
                conn.rollback()
                return 're_enriched'
            backup = json.loads(check['backup']) if isinstance(check['backup'], (str, bytes)) else check['backup']
            cursor.execute(
                """UPDATE papers SET openalex_id = %s, doi = COALESCE(doi, %s),
                       abstract = COALESCE(abstract, %s), enrichment_attempted_at = %s
                   WHERE id = %s""",
                (openalex_id, backup.get('doi'), backup.get('abstract'),
                 backup.get('enrichment_attempted_at'), paper_id),
            )
            if backup.get('coauthors'):
                cursor.executemany(
                    'INSERT IGNORE INTO openalex_coauthors (paper_id, display_name, openalex_author_id) '
                    'VALUES (%s, %s, %s)',
                    [(paper_id, c['display_name'], c.get('openalex_author_id')) for c in backup['coauthors']],
                )
            if backup.get('topics'):
                cursor.executemany(
                    """INSERT IGNORE INTO paper_topics (paper_id, openalex_topic_id, topic_name, subfield_name,
                           field_name, domain_name, score) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [(paper_id, t['openalex_topic_id'], t['topic_name'], t.get('subfield_name'),
                      t.get('field_name'), t.get('domain_name'), t.get('score')) for t in backup['topics']],
                )
            for link in backup.get('links') or []:
                cursor.execute('UPDATE paper_links SET doi = %s WHERE id = %s AND paper_id = %s AND doi IS NULL',
                               (link['doi'], link['id'], paper_id))
            cursor.execute(
                """UPDATE openalex_identifier_checks SET verdict = 'review', reason = 'undone',
                       restored_at = UTC_TIMESTAMP() WHERE paper_id = %s AND openalex_id = %s""",
                (paper_id, openalex_id))
            conn.commit()
            return 'restored'
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
