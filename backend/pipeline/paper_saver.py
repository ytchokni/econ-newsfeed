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
from backend.config import guard_text_fields
from backend.pipeline.publication import clean_title
from datetime import datetime, timezone
import logging

_author_id_cache: dict[tuple[str, str], int] = {}

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

                    cursor = conn.cursor(buffered=True)

                    cursor.execute(
                        """INSERT IGNORE INTO papers
                           (source_url, title, title_hash, year, venue, abstract,
                            discovered_at, status, draft_url, is_seed)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (url, title, title_hash, pub.get('year'), pub.get('venue'),
                         pub.get('abstract'), datetime.now(timezone.utc), pub.get('status'),
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
                        cursor.execute("SELECT id FROM papers WHERE title_hash = %s", (title_hash,))
                        row = cursor.fetchone()
                        if not row:
                            logging.error(f"Could not find publication after INSERT IGNORE: {pub['title']}")
                            continue
                        publication_id = row[0]

                        cursor.execute("SELECT abstract, year, venue FROM papers WHERE id = %s", (publication_id,))
                        existing = cursor.fetchone()
                        if existing:
                            existing_abstract, existing_year, existing_venue = existing
                            new_abstract = pub.get('abstract')
                            new_year = pub.get('year')
                            new_venue = pub.get('venue')
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

                    # Authors
                    for author_order, author in enumerate(pub['authors'], start=1):
                        if not author:
                            continue
                        if len(author) == 1:
                            first_name, last_name = "", author[0]
                        elif len(author) == 2:
                            first_name, last_name = author
                        else:
                            first_name = " ".join(author[:-1])
                            last_name = author[-1]
                        cache_key = (first_name, last_name)
                        if cache_key in _author_id_cache:
                            author_id = _author_id_cache[cache_key]
                        else:
                            author_id = get_researcher_id(first_name, last_name, conn=conn)
                            _author_id_cache[cache_key] = author_id
                        cursor.execute(
                            "INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, %s)",
                            (author_id, publication_id, author_order),
                        )

                    # Page owner
                    cursor.execute(
                        """SELECT r.id FROM researchers r
                           JOIN researcher_urls ru ON ru.researcher_id = r.id
                           WHERE ru.url = %s LIMIT 1""",
                        (url,),
                    )
                    owner_row = cursor.fetchone()
                    if owner_row:
                        cursor.execute(
                            "INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, %s)",
                            (owner_row[0], publication_id, 0),
                        )

                    conn.commit()
                    results.append(SaveResult(
                        paper_id=publication_id,
                        title=title,
                        is_new=is_new,
                        new_to_this_url=new_to_this_url,
                        status=pub.get('status'),
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
        return results

    @staticmethod
    def apply_title_rename(paper_id: int, old_title: str, new_title: str,
                           metadata: dict, source_url: str) -> None:
        """Apply a known title rename: snapshot, update, dedup collisions.

        metadata should contain status, venue, abstract, draft_url, year.
        """
        new_hash = compute_title_hash(new_title)

        append_paper_snapshot(
            paper_id=paper_id,
            status=metadata.get('status'),
            venue=metadata.get('venue'),
            abstract=metadata.get('abstract'),
            draft_url=metadata.get('draft_url'),
            year=metadata.get('year'),
            source_url=source_url,
            title=old_title,
        )

        with get_connection() as conn:
            cursor = conn.cursor(buffered=True)
            try:
                # A paper with the target title may already exist (typically
                # saved as "new" earlier in the same run). It must be absorbed
                # BEFORE the title UPDATE — updating first violates
                # uq_title_hash and crashed the extraction worker (#177).
                cursor.execute(
                    "SELECT id FROM papers WHERE title_hash = %s AND id != %s",
                    (new_hash, paper_id),
                )
                dup = cursor.fetchone()
                if dup:
                    dup_id = dup[0]
                    # Reassign children to the surviving paper; rows that would
                    # violate a UNIQUE constraint are skipped (IGNORE) and
                    # cleaned up by ON DELETE CASCADE. feed_events are deleted
                    # instead — reassigning them would give the survivor a
                    # duplicate new_paper event.
                    from backend.enrichment.paper_merge import _CHILD_TABLES
                    for table, col in _CHILD_TABLES:
                        if table == 'feed_events':
                            continue
                        cursor.execute(
                            f"UPDATE IGNORE `{table}` SET `{col}` = %s WHERE `{col}` = %s",
                            (paper_id, dup_id),
                        )
                    cursor.execute("DELETE FROM feed_events WHERE paper_id = %s", (dup_id,))
                    cursor.execute("DELETE FROM papers WHERE id = %s", (dup_id,))
                cursor.execute(
                    "UPDATE papers SET title = %s, title_hash = %s WHERE id = %s",
                    (new_title, new_hash, paper_id),
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
