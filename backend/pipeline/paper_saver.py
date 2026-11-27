"""Paper persistence — upsert, dedup, author linking.

Returns SaveResult objects describing what changed.
Does NOT create feed events — that is FeedEventEmitter's responsibility.
"""
from dataclasses import dataclass
import re
from backend.database import (
    append_paper_snapshot,
    compute_title_hash,
    fetch_all,
    get_connection,
    get_researcher_id,
    normalize_title,
)
from backend.database.researchers import (
    is_abbreviation_of, is_compatible_name, merge_researchers, refresh_has_top5_for,
)
from backend.database.identity import (
    SHARED_PAGE, match_score, on_merge, page_ownership_problem, same_person, strip_repeated_surname,
)
from backend.database.snapshots import _STATUS_RANK
from backend.database.papers import title_hash_candidates
from backend.publication_rules import is_working_paper_series
from backend.config import guard_text_fields
from backend.pipeline.publication import clean_title
from datetime import datetime, timezone
import logging

_author_id_cache: dict[tuple[str, str], int] = {}


_merged_into: dict[int, int] = {}


def _forget_merged(duplicate_id: int, canonical_id: int) -> None:
    """Point cached names at the surviving record after a researcher merge."""
    _merged_into[duplicate_id] = canonical_id
    for key, rid in list(_author_id_cache.items()):
        if rid == duplicate_id:
            _author_id_cache[key] = canonical_id


on_merge(_forget_merged)


def _current_id(researcher_id: int) -> int:
    """Follow merges made since the ID was looked up (e.g. by a Layer 2 merge)."""
    seen = set()
    while researcher_id in _merged_into and researcher_id not in seen:
        seen.add(researcher_id)
        researcher_id = _merged_into[researcher_id]
    return researcher_id

_SHARED_PAPER_THRESHOLD = 2
_SHARED_PAPER_THRESHOLD_WEAK = 5

_SIMILARITY_THRESHOLD = 0.5


@dataclass
class SaveResult:
    paper_id: int
    title: str
    is_new: bool
    new_to_this_url: bool
    status: str | None


@dataclass
class TitleRename:
    paper_id: int
    old_title: str
    new_title: str
    similarity: float


def _title_similarity(title_a: str | None, title_b: str | None) -> float:
    """Jaccard similarity on normalized word tokens. Used to detect title renames."""
    tokens_a = set(normalize_title(title_a).split())
    tokens_b = set(normalize_title(title_b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


_BRACKET_PREFIX = re.compile(r'^\[.*?\]\s*')


def validate_title_change(old_title: str, new_title: str) -> bool:
    """Return True only for genuine title renames, filtering LLM extraction artifacts.

    Rejects: identical-after-normalization, bracket prefix diffs, subtitle
    addition/removal, single-word addition/removal, hyphenation-only changes.
    """
    norm_old = normalize_title(old_title)
    norm_new = normalize_title(new_title)

    if norm_old == norm_new:
        return False

    stripped_old = normalize_title(_BRACKET_PREFIX.sub('', old_title))
    stripped_new = normalize_title(_BRACKET_PREFIX.sub('', new_title))
    if stripped_old == stripped_new:
        return False

    old_tokens = norm_old.split()
    new_tokens = norm_new.split()
    if abs(len(old_tokens) - len(new_tokens)) <= 1:
        shorter, longer = sorted([old_tokens, new_tokens], key=len)
        diff_count = 0
        j = 0
        for tok in longer:
            if j < len(shorter) and tok == shorter[j]:
                j += 1
            else:
                diff_count += 1
        diff_count += len(shorter) - j
        if diff_count <= 1:
            return False

    dehyphen_old = norm_old.replace(' ', '')
    dehyphen_new = norm_new.replace(' ', '')
    if dehyphen_old == dehyphen_new:
        return False

    if norm_old.startswith(norm_new) or norm_new.startswith(norm_old):
        return False

    return True


def _have_conflicting_urls(rid_a: int, rid_b: int) -> bool:
    """True if both researchers have personal websites and none overlap."""
    urls = fetch_all(
        "SELECT researcher_id, url FROM researcher_urls WHERE researcher_id IN (%s, %s)",
        (rid_a, rid_b),
    )
    urls_a = {r['url'] for r in urls if r['researcher_id'] == rid_a}
    urls_b = {r['url'] for r in urls if r['researcher_id'] == rid_b}
    if not urls_a or not urls_b:
        return False
    return len(urls_a & urls_b) == 0


def _merge_compatible_authors(paper_id: int) -> None:
    """Layer 2: merge researcher pairs on this paper sharing enough papers.

    Compatible names (initials/prefixes): 2+ shared papers.
    Any same-last-name pair: 5+ shared papers, no conflicting personal websites.
    """
    authors = fetch_all(
        """SELECT a.researcher_id, r.first_name, r.last_name, r.openalex_author_id
           FROM authorship a JOIN researchers r ON r.id = a.researcher_id
           WHERE a.publication_id = %s""",
        (paper_id,),
    )
    if len(authors) < 2:
        return
    if _merge_openalex_twins(authors):
        return

    by_last: dict[str, list[dict]] = {}
    for a in authors:
        by_last.setdefault(a['last_name'].lower().strip(), []).append(a)

    for last_key, group in by_last.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a['researcher_id'] == b['researcher_id']:
                    continue
                compatible = is_compatible_name(a['first_name'], b['first_name'])
                threshold = _SHARED_PAPER_THRESHOLD if compatible else _SHARED_PAPER_THRESHOLD_WEAK
                shared = fetch_all(
                    """SELECT COUNT(*) AS cnt FROM authorship a1
                       JOIN authorship a2 ON a1.publication_id = a2.publication_id
                       WHERE a1.researcher_id = %s AND a2.researcher_id = %s""",
                    (a['researcher_id'], b['researcher_id']),
                )
                if not shared or shared[0]['cnt'] < threshold:
                    continue
                if not compatible:
                    if not is_abbreviation_of(a['first_name'], b['first_name']):
                        continue
                    if _have_conflicting_urls(a['researcher_id'], b['researcher_id']):
                        continue
                canonical = a if len(a['first_name']) >= len(b['first_name']) else b
                duplicate = b if canonical is a else a
                try:
                    with get_connection() as conn:
                        merge_researchers(canonical['researcher_id'], duplicate['researcher_id'], conn)
                    logging.info(
                        "Layer 2 merge: %s %s (id=%d) absorbed %s %s (id=%d) — %d shared papers",
                        canonical['first_name'], canonical['last_name'], canonical['researcher_id'],
                        duplicate['first_name'], duplicate['last_name'], duplicate['researcher_id'],
                        shared[0]['cnt'],
                    )
                except Exception:
                    logging.exception(
                        "Layer 2 merge failed: %d into %d",
                        duplicate['researcher_id'], canonical['researcher_id'],
                    )
                return


def _merge_openalex_twins(authors: list[dict]) -> bool:
    """Merge two authors of one paper that share an OpenAlex ID and a name.

    One person listed twice ("Andrew Garin" and "Andy Garin") because the
    paper came from several pages that spell the name differently.
    """
    by_oa: dict[str, list[dict]] = {}
    for a in authors:
        if a.get('openalex_author_id'):
            by_oa.setdefault(a['openalex_author_id'], []).append(a)
    for group in by_oa.values():
        if len(group) != 2:
            continue
        a, b = group
        # Default strength: OpenAlex itself conflates Zhang/Zheng, Meyer/Mayer.
        if not same_person(a['first_name'], a['last_name'], b['first_name'], b['last_name']):
            continue
        from backend.database.identity import canonical_order, merge_researcher_into, researcher_weights
        try:
            with get_connection() as conn:
                cursor = conn.cursor(dictionary=True, buffered=True)
                try:
                    ranked = sorted(researcher_weights(cursor, [a['researcher_id'], b['researcher_id']]),
                                    key=canonical_order)
                    if len(ranked) == 2:
                        merge_researcher_into(cursor, ranked[0]['id'], ranked[1]['id'])
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    cursor.close()
            return True
        except Exception:
            logging.exception("OpenAlex twin merge failed: %s / %s", a['researcher_id'], b['researcher_id'])
    return False


def _page_owners(cursor, url: str) -> tuple[list[dict], str | None]:
    """Researchers tracking this URL (lowest ID first) and why the page should
    not be credited to them (see identity.page_ownership_problem), if any."""
    cursor.execute(
        """SELECT DISTINCT r.id, r.first_name, r.last_name FROM researchers r
           JOIN researcher_urls ru ON ru.researcher_id = r.id
           WHERE ru.url = %s ORDER BY r.id""",
        (url,),
    )
    owners = [{"id": row[0], "first_name": row[1], "last_name": row[2]}
              for row in cursor.fetchall() or []]
    if not owners:
        return [], None
    cursor.execute(
        """SELECT hc.content, hc.raw_html, ru.page_type FROM html_content hc
           JOIN researcher_urls ru ON ru.id = hc.url_id
           WHERE ru.url = %s AND (hc.content IS NOT NULL OR hc.raw_html IS NOT NULL)
           ORDER BY hc.timestamp DESC LIMIT 1""",
        (url,),
    )
    page = cursor.fetchall() or []
    text, raw = (page[0][0], page[0][1]) if page else (None, None)
    page_type = page[0][2] if page and len(page[0]) > 2 else None
    return owners, page_ownership_problem(owners, text, raw, url, page_type)


def _owner_byline_index(owners: list[dict], authors: list[tuple[str, str]]) -> dict[int, int]:
    """Map byline positions to the page owner they name ({index: owner_id}).

    Each owner takes at most one byline author: the most specific match
    ("Dora L. Costa" over a co-author "D. Costa" is impossible to tell apart,
    so an exact name beats a full first name, which beats initials).
    """
    routed: dict[int, int] = {}
    for owner in owners:
        best = None
        for idx, (first, last) in enumerate(authors):
            if idx in routed or not first:
                continue
            if not same_person(owner['first_name'], owner['last_name'], first, last, strong=True):
                continue
            score = match_score(owner['first_name'], owner['last_name'], first, last)
            if best is None or score > best[0]:
                best = (score, idx)
        if best is not None:
            routed[best[1]] = owner['id']
    return routed


class PaperSaver:
    @staticmethod
    def save_publications(
        url: str,
        publications: list[dict],
        is_seed: bool = False,
    ) -> list[SaveResult]:
        """Save extracted publications. Returns SaveResult per successfully saved paper."""
        results = []
        with get_connection() as conn:
            lookup = conn.cursor(buffered=True)
            try:
                owners, ownership_problem = _page_owners(lookup, url)
            finally:
                lookup.close()
            if ownership_problem:
                logging.info("Not crediting page owner(s) %s with papers from %s: %s",
                             [o['id'] for o in owners], url, ownership_problem)
            for pub in publications:
                cursor = None
                try:
                    title = clean_title(pub['title'].strip()) if pub['title'] else ''
                    pub = guard_text_fields(
                        dict(pub, title=title),
                        ["title", "abstract", "venue"],
                        context=f"papers (url={url})",
                    )
                    title = pub['title']
                    title_hash = compute_title_hash(title)
                    status = pub.get('status')

                    cursor = conn.cursor(buffered=True)

                    if not title.isascii():
                        hashes = title_hash_candidates(title)
                        cursor.execute(
                            f"SELECT id, title, title_hash FROM papers WHERE title_hash IN ({','.join(['%s'] * len(hashes))}) FOR UPDATE",
                            tuple(hashes),
                        )
                        matches = [row for row in cursor.fetchall()
                                   if normalize_title(row[1]) == normalize_title(title)]
                        if len(matches) > 1:
                            raise ValueError('Ambiguous legacy title identity; repair duplicates first')
                        if matches and matches[0][2] != title_hash:
                            cursor.execute('UPDATE papers SET title_hash = %s WHERE id = %s',
                                           (title_hash, matches[0][0]))

                    cursor.execute(
                        """INSERT IGNORE INTO papers
                           (source_url, title, title_hash, year, venue, abstract,
                            discovered_at, status, draft_url, is_seed)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (url, title, title_hash, pub.get('year'), pub.get('venue'),
                         pub.get('abstract'), datetime.now(timezone.utc), status,
                         pub.get('draft_url'), is_seed),
                    )

                    if cursor.lastrowid:
                        publication_id = cursor.lastrowid
                        cursor.execute(
                            "INSERT IGNORE INTO paper_urls (paper_id, url, discovered_at) VALUES (%s, %s, %s)",
                            (publication_id, url, datetime.now(timezone.utc)),
                        )
                        is_new = True
                        new_to_this_url = True
                    else:
                        cursor.execute("SELECT id, title FROM papers WHERE title_hash = %s", (title_hash,))
                        row = cursor.fetchone()
                        if not row:
                            logging.error(f"Could not find publication after INSERT IGNORE: {pub['title']}")
                            continue
                        publication_id = row[0]
                        if normalize_title(row[1]) != normalize_title(title):
                            raise ValueError('Title hash belongs to a different legacy title; repair identity first')

                        cursor.execute("SELECT abstract, year, venue, status FROM papers WHERE id = %s FOR UPDATE", (publication_id,))
                        existing = cursor.fetchone()
                        if existing:
                            existing_abstract, existing_year, existing_venue, existing_status = existing
                            new_abstract = pub.get('abstract')
                            new_year = pub.get('year')
                            new_venue = pub.get('venue')
                            if (_STATUS_RANK.get(status, -1) < _STATUS_RANK.get(existing_status, -1)
                                    or (_STATUS_RANK.get(existing_status, -1) > _STATUS_RANK['working_paper']
                                        and is_working_paper_series(new_venue))):
                                new_venue = None
                            needs_backfill = (
                                (not existing_abstract and new_abstract)
                                or (not existing_year and new_year)
                                or (not existing_venue and new_venue)
                            )
                            if needs_backfill:
                                cursor.execute(
                                    """UPDATE papers SET
                                        abstract = COALESCE(abstract, %s),
                                        year = COALESCE(year, %s),
                                        venue = COALESCE(venue, %s)
                                    WHERE id = %s""",
                                    (new_abstract, new_year, new_venue, publication_id),
                                )
                                logging.info(f"Backfilled metadata for duplicate: {pub['title']}")

                        cursor.execute(
                            "INSERT IGNORE INTO paper_urls (paper_id, url, discovered_at) VALUES (%s, %s, %s)",
                            (publication_id, url, datetime.now(timezone.utc)),
                        )
                        is_new = False
                        new_to_this_url = cursor.rowcount > 0
                        logging.info(f"Duplicate publication (title_hash match), added source URL: {pub['title']}")

                    # Authors — track resolved (last_name -> (researcher_id, first_name))
                    # for within-paper dedup of name variants
                    resolved_by_last: dict[str, list[tuple[int, str]]] = {}

                    byline = []
                    for author in pub['authors']:
                        if not author:
                            byline.append(None)
                        elif len(author) == 1:
                            byline.append(("", author[0]))
                        elif len(author) == 2:
                            byline.append((author[0], author[1]))
                        else:
                            byline.append((" ".join(author[:-1]), author[-1]))
                    # ["Peter Hull", "Hull"] -> "Peter" / "Hull"
                    byline = [(strip_repeated_surname(b[0], b[1]), b[1]) if b else None for b in byline]
                    # A byline name that is the page owner's resolves to the
                    # owner, never to a same-name stub record.
                    owner_at = _owner_byline_index(
                        owners, [b if b else ("", "") for b in byline]) if ownership_problem != SHARED_PAGE else {}

                    for author_order, name in enumerate(byline, start=1):
                        if not name:
                            continue
                        first_name, last_name = name

                        # Page owner named in the byline
                        last_key = last_name.lower().strip()
                        if (author_order - 1) in owner_at:
                            author_id = _current_id(owner_at[author_order - 1])
                            resolved_by_last.setdefault(last_key, []).append((author_id, first_name))
                            cursor.execute(
                                "INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, %s)",
                                (author_id, publication_id, author_order),
                            )
                            continue

                        # Layer 1: check if a compatible-name author is already
                        # resolved for this paper (same last name, compatible first)
                        author_id = None
                        if first_name and last_key in resolved_by_last:
                            for rid, resolved_first in resolved_by_last[last_key]:
                                if is_compatible_name(first_name, resolved_first):
                                    author_id = rid
                                    break

                        if author_id is None:
                            cache_key = (first_name, last_name)
                            if cache_key in _author_id_cache:
                                author_id = _author_id_cache[cache_key]
                            else:
                                author_id = get_researcher_id(first_name, last_name, conn=conn)
                                _author_id_cache[cache_key] = author_id

                        if author_id is not None:
                            resolved_by_last.setdefault(last_key, []).append((author_id, first_name))
                            cursor.execute(
                                "INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, %s)",
                                (author_id, publication_id, author_order),
                            )

                    # Page owner not in the byline: attach at position 0 when the
                    # extractor could not see the full author list. Never on a
                    # page that does not name its owner or that several
                    # unrelated researchers track (department/news pages).
                    if owners and not ownership_problem:
                        named = set(owner_at.values())
                        for owner in owners:
                            if owner['id'] in named:
                                continue
                            owner_last_key = owner['last_name'].lower().strip()
                            if any(is_compatible_name(owner['first_name'], resolved_first)
                                   for _, resolved_first in resolved_by_last.get(owner_last_key, [])
                                   if owner['first_name']):
                                continue
                            if not pub.get('_authors_complete'):
                                cursor.execute(
                                    "INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, %s)",
                                    (_current_id(owner['id']), publication_id, 0),
                                )
                            else:
                                logging.info("Did not attach page owner %s to explicit byline for %s",
                                             owner['id'], title)

                    conn.commit()

                    try:
                        _merge_compatible_authors(publication_id)
                    except Exception:
                        logging.exception("Layer 2 author merge failed for paper %d", publication_id)

                    results.append(SaveResult(
                        paper_id=publication_id,
                        title=title,
                        is_new=is_new,
                        new_to_this_url=new_to_this_url,
                        status=status,
                    ))
                    logging.info(f"Publication saved successfully: {pub['title']}")

                except Exception as e:
                    logging.error(
                        "Error saving publication '%s': %s: %s",
                        pub.get('title', '<unknown>'), type(e).__name__, e,
                    )
                    conn.rollback()
                finally:
                    if cursor:
                        cursor.close()

        logging.info(f"{len(publications)} publications processed for {url}")

        if results:
            try:
                owner_rows = fetch_all(
                    "SELECT r.id FROM researchers r "
                    "JOIN researcher_urls ru ON ru.researcher_id = r.id "
                    "WHERE ru.url = %s", (url,),
                )
                paper_ids = sorted({r.paper_id for r in results})
                author_rows = fetch_all(
                    "SELECT DISTINCT researcher_id AS id FROM authorship "
                    f"WHERE publication_id IN ({', '.join(['%s'] * len(paper_ids))})",
                    tuple(paper_ids),
                )
                refresh_has_top5_for(row['id'] for row in [*owner_rows, *author_rows])
            except Exception as e:
                logging.warning("Failed to refresh has_top5_pub for %s: %s", url, e)

        return results

    @staticmethod
    def apply_title_rename(paper_id: int, old_title: str, new_title: str,
                           metadata: dict, source_url: str) -> None:
        """Apply a known title rename; refuse collisions with another identity.

        metadata should contain status, venue, abstract, draft_url, year.
        """
        new_hash = compute_title_hash(new_title)

        with get_connection() as conn:
            cursor = conn.cursor(buffered=True)
            try:
                # A paper with the target title may already exist (typically
                # saved as "new" earlier in the same run). Abstain rather than
                # deleting that record without validated title/author evidence.
                hashes = title_hash_candidates(new_title)
                cursor.execute(
                    f"SELECT id FROM papers WHERE title_hash IN ({','.join(['%s'] * len(hashes))}) AND id != %s FOR UPDATE",
                    (*hashes, paper_id),
                )
                dup = cursor.fetchone()
                if dup:
                    raise ValueError('Title rename collides with another paper; require a validated merge')
                cursor.execute(
                    "UPDATE papers SET title = %s, title_hash = %s WHERE id = %s",
                    (new_title, new_hash, paper_id),
                )
                append_paper_snapshot(
                    paper_id=paper_id, status=metadata.get('status'),
                    venue=metadata.get('venue'), abstract=metadata.get('abstract'),
                    draft_url=metadata.get('draft_url'), year=metadata.get('year'),
                    source_url=source_url, title=old_title, conn=conn,
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                logging.error("Error applying title rename %s → %s: %s",
                              old_title[:50], new_title[:50], e)
                raise
            finally:
                cursor.close()

    @staticmethod
    def reconcile_title_renames(source_url: str, extracted_pubs: list[dict]) -> list[TitleRename]:
        """Detect and apply title renames. Returns list of TitleRename for event emission.

        Handles: title update, duplicate cleanup, snapshot recording.
        Does NOT create feed events — caller emits via FeedEventEmitter.
        """
        existing = fetch_all(
            "SELECT id, title, title_hash FROM papers WHERE source_url = %s",
            (source_url,),
        )
        if not existing:
            return []

        existing_normalized = {normalize_title(p['title']): p for p in existing}
        extracted_normalized = {
            normalize_title(pub['title']): pub for pub in extracted_pubs if pub.get('title')
        }

        disappeared = set(existing_normalized.keys()) - set(extracted_normalized.keys())
        appeared = set(extracted_normalized.keys()) - set(existing_normalized.keys())

        if not disappeared or not appeared:
            return []

        matched_disappeared = set()
        renames_to_apply = []

        for app_norm in appeared:
            best_sim = 0.0
            best_dis = None
            for dis_norm in disappeared:
                if dis_norm in matched_disappeared:
                    continue
                sim = _title_similarity(
                    existing_normalized[dis_norm]['title'],
                    extracted_normalized[app_norm]['title'],
                )
                if sim > best_sim:
                    best_sim = sim
                    best_dis = dis_norm

            if best_dis is not None and best_sim >= _SIMILARITY_THRESHOLD:
                old_t = existing_normalized[best_dis]['title']
                new_t = extracted_normalized[app_norm]['title']
                if not validate_title_change(old_t, new_t):
                    logging.info(
                        "Suppressed spurious title rename (sim=%.2f): '%s' → '%s'",
                        best_sim, old_t[:50], new_t[:50],
                    )
                    continue
                matched_disappeared.add(best_dis)
                renames_to_apply.append((
                    existing_normalized[best_dis],
                    extracted_normalized[app_norm],
                    best_sim,
                ))

        if not renames_to_apply:
            return []

        renames = []
        for old_paper, new_pub, sim in renames_to_apply:
            old_id = old_paper['id']
            old_title = old_paper['title']
            new_title = new_pub['title'].strip()

            try:
                PaperSaver.apply_title_rename(
                    old_id, old_title, new_title, new_pub, source_url,
                )
            except Exception:
                continue

            renames.append(TitleRename(
                paper_id=old_id,
                old_title=old_title,
                new_title=new_title,
                similarity=sim,
            ))
            logging.info(
                "Title rename detected (sim=%.2f): '%s' → '%s' (paper_id=%d)",
                sim, old_title[:50], new_title[:50], old_id,
            )

        return renames
