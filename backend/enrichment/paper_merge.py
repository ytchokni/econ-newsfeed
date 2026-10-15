"""Post-enrichment duplicate paper merging.

After OpenAlex enrichment assigns DOIs and OpenAlex IDs, this module finds papers
sharing the same identifier and merges them into a single canonical record.
Also finds matching normalized titles with identical author sets.
"""
import logging
from difflib import SequenceMatcher
from backend.database import fetch_all, get_connection
from backend.database.snapshots import STATUS_ORDER, _STATUS_RANK
from backend.database.papers import normalize_title, has_title_identity
from backend.publication_rules import is_working_paper_series

logger = logging.getLogger(__name__)

# Tables with paper_id FK that need row reassignment before deleting duplicates.
_CHILD_TABLES = [
    ("authorship", "publication_id"),
    ("paper_urls", "paper_id"),
    ("paper_links", "paper_id"),
    ("feed_events", "paper_id"),
    ("paper_snapshots", "paper_id"),
    ("openalex_coauthors", "paper_id"),
    ("paper_topics", "paper_id"),
]


def find_duplicate_groups() -> list[list[int]]:
    """Find groups of papers sharing the same DOI or OpenAlex ID."""
    doi_groups = fetch_all(
        """SELECT doi, GROUP_CONCAT(id ORDER BY discovered_at) AS ids
           FROM papers WHERE doi IS NOT NULL
           GROUP BY doi HAVING COUNT(*) > 1"""
    )
    oa_groups = fetch_all(
        """SELECT openalex_id, GROUP_CONCAT(id ORDER BY discovered_at) AS ids
           FROM papers WHERE openalex_id IS NOT NULL
           GROUP BY openalex_id HAVING COUNT(*) > 1"""
    )

    raw_groups: list[set[int]] = []
    for row in doi_groups:
        raw_groups.append({int(x) for x in row['ids'].split(',')})
    for row in oa_groups:
        raw_groups.append({int(x) for x in row['ids'].split(',')})

    return _connected_groups(raw_groups)


def _connected_groups(groups):
    """Collapse every overlap, including bridges between previously separate groups."""
    merged = []
    for raw in groups:
        group = set(raw)
        while any(group & existing for existing in merged):
            overlaps = [existing for existing in merged if group & existing]
            for existing in overlaps:
                group.update(existing)
                merged.remove(existing)
        if len(group) > 1:
            merged.append(group)
    return [sorted(group) for group in merged]


def _compatible_identity(a, b):
    """Identifier equality alone is not evidence of a shared paper."""
    if not has_title_identity(a.get('title')) or not has_title_identity(b.get('title')):
        return False
    for key in ('doi', 'openalex_id'):
        if a.get(key) and b.get(key) and a[key].casefold() != b[key].casefold():
            return False
    aa, ba = set(a.get('author_ids') or []), set(b.get('author_ids') or [])
    if not aa or not ba or not (aa <= ba or ba <= aa):
        return False
    shared_id = any(a.get(key) and a.get(key) == b.get(key) for key in ('doi', 'openalex_id'))
    if not shared_id and (aa != ba or len(aa) < 2):
        return False
    # A single changed place, intervention, or negation can identify a different
    # paper despite near-perfect fuzzy similarity. Renames need source review.
    return normalize_title(a['title']) == normalize_title(b['title'])


def highest_known_status(statuses) -> str | None:
    """Return the furthest valid current status among duplicate paper rows."""
    known = set(statuses).intersection(STATUS_ORDER)
    return next((status for status in reversed(STATUS_ORDER) if status in known), None)


def merge_paper_group(paper_ids: list[int]) -> bool:
    """Merge verified identities only; preserve coherent metadata and every source."""
    paper_ids = sorted(set(paper_ids))
    if len(paper_ids) < 2:
        return False
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            placeholders = ','.join(['%s'] * len(paper_ids))
            cursor.execute(
                f"SELECT * FROM papers WHERE id IN ({placeholders}) ORDER BY id FOR UPDATE",
                tuple(paper_ids),
            )
            papers = sorted(cursor.fetchall(), key=lambda p: (
                p['discovered_at'] is not None, p['discovered_at'], p['id'],
            ))
            if len(papers) != len(paper_ids):
                conn.rollback()
                return False
            cursor.execute(
                f"SELECT publication_id, researcher_id FROM authorship WHERE publication_id IN ({placeholders}) FOR UPDATE",
                tuple(paper_ids),
            )
            authors = cursor.fetchall()
            for paper in papers:
                paper['author_ids'] = {a['researcher_id'] for a in authors if a['publication_id'] == paper['id']}
            if any(not _compatible_identity(a, b) for i, a in enumerate(papers) for b in papers[i + 1:]):
                logger.warning('Refused incompatible duplicate candidate group: %s', paper_ids)
                conn.rollback()
                return False

            canonical_id = papers[0]['id']
            cursor.execute(
                f"""UPDATE feed_events fe JOIN papers p ON p.id = fe.paper_id
                    SET fe.event_venue = p.venue, fe.event_source_url = p.source_url,
                        fe.event_metadata_captured = TRUE
                    WHERE fe.paper_id IN ({placeholders}) AND fe.event_metadata_captured = FALSE""",
                tuple(paper_ids),
            )
            ranked = sorted(papers, key=lambda p: _STATUS_RANK.get(p['status'], -1), reverse=True)
            status = highest_known_status(p['status'] for p in papers)
            venues = [p for p in ranked if p.get('venue') and (
                _STATUS_RANK.get(status, -1) <= _STATUS_RANK['working_paper'] or
                (_STATUS_RANK.get(p['status'], -1) > _STATUS_RANK['working_paper'] and
                 not is_working_paper_series(p['venue'])))]
            metadata = venues[0] if venues else ranked[0]
            link = next((p for p in ranked if p.get('draft_url') and p.get('draft_url_status') == 'valid'),
                        next((p for p in ranked if p.get('draft_url')), {}))
            first = lambda key: next((p[key] for p in ranked if p.get(key)), None)
            cursor.execute(
                """UPDATE papers SET status = %s, venue = %s, year = %s, source_url = %s,
                   abstract = %s, doi = %s, openalex_id = %s, draft_url = %s,
                   draft_url_status = %s, draft_url_checked_at = %s, is_seed = %s WHERE id = %s""",
                (status, metadata.get('venue') if venues else None, metadata.get('year') or first('year'),
                 metadata.get('source_url') or first('source_url'), first('abstract'), first('doi'),
                 first('openalex_id'), link.get('draft_url'), link.get('draft_url_status') or 'unchecked',
                 link.get('draft_url_checked_at'), all(p.get('is_seed') for p in papers), canonical_id),
            )
            for paper in papers:
                if paper.get('source_url'):
                    cursor.execute(
                        'INSERT IGNORE INTO paper_urls (paper_id, url, discovered_at) VALUES (%s, %s, %s)',
                        (canonical_id, paper['source_url'], paper['discovered_at']),
                    )
            for duplicate in papers[1:]:
                # Reconcile metadata for URLs already on the canonical record
                # before UPDATE IGNORE leaves their duplicate rows to cascade.
                cursor.execute(
                    """UPDATE paper_links c JOIN paper_links d ON c.url = d.url
                       SET c.doi = COALESCE(c.doi, d.doi),
                           c.link_type = IF(c.link_type = 'other', d.link_type, c.link_type),
                           c.discovered_at = LEAST(c.discovered_at, d.discovered_at)
                       WHERE c.paper_id = %s AND d.paper_id = %s""",
                    (canonical_id, duplicate['id']),
                )
                for table, col in _CHILD_TABLES:
                    cursor.execute(f"UPDATE IGNORE `{table}` SET `{col}` = %s WHERE `{col}` = %s",
                                   (canonical_id, duplicate['id']))
            # Existing enriched links win over these bare draft URL fallbacks.
            for paper in papers:
                if paper.get('draft_url'):
                    cursor.execute(
                        "INSERT IGNORE INTO paper_links (paper_id, url, link_type, discovered_at) VALUES (%s, %s, 'other', UTC_TIMESTAMP())",
                        (canonical_id, paper['draft_url']),
                    )
            cursor.execute(
                """SELECT id FROM feed_events WHERE paper_id = %s AND event_type = 'new_paper'
                   ORDER BY created_at, id FOR UPDATE""", (canonical_id,),
            )
            for event in cursor.fetchall()[1:]:
                cursor.execute('DELETE FROM feed_events WHERE id = %s', (event['id'],))
            for duplicate in papers[1:]:
                cursor.execute('DELETE FROM papers WHERE id = %s', (duplicate['id'],))
            conn.commit()
            logger.info('Merged %d verified duplicates into paper %s', len(papers) - 1, canonical_id)
            return True
        except Exception:
            conn.rollback()
            logger.exception('Failed to merge papers %s', paper_ids)
            raise
        finally:
            cursor.close()


_FUZZY_THRESHOLD = 0.85

_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'of', 'on', 'in', 'at', 'to', 'for',
    'and', 'or', 'by', 'with', 'from', 'as', 'is', 'are',
})


def _normalize(title: str) -> str:
    return normalize_title(title.replace("'s", '').replace("’s", ''))


def _content_words(title: str) -> list[str]:
    return [w for w in _normalize(title).split() if w not in _STOP_WORDS]


def _content_overlap(t1: str, t2: str) -> float:
    """Overlap coefficient on content words: |intersection| / min(|A|, |B|)."""
    s1, s2 = set(_content_words(t1)), set(_content_words(t2))
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / min(len(s1), len(s2))


_SEQ_FLOOR = 0.5


def _title_similarity(t1: str, t2: str) -> float:
    w1 = _normalize(t1).split()
    w2 = _normalize(t2).split()
    if not w1 or not w2:
        return 0.0
    seq_ratio = SequenceMatcher(None, w1, w2).ratio()
    overlap = _content_overlap(t1, t2)
    # Overlap is the primary signal, but require a minimum sequence agreement
    # to guard against same-topic-different-paper false positives.
    if overlap > seq_ratio and seq_ratio < _SEQ_FLOOR:
        return seq_ratio
    return max(seq_ratio, overlap)


def find_fuzzy_duplicate_groups() -> list[list[int]]:
    """Find papers with identical author sets and normalized titles.

    The historical function name is retained for callers. Loose fuzzy matches
    cannot authorize a destructive merge.  Skips pairs that
    already share a DOI or OpenAlex ID (handled by find_duplicate_groups).
    """
    candidates = fetch_all("""
        SELECT p.id, p.title, p.doi, p.openalex_id,
               GROUP_CONCAT(a.researcher_id ORDER BY a.researcher_id) AS author_ids
        FROM papers p
        JOIN authorship a ON a.publication_id = p.id
        GROUP BY p.id, p.title, p.doi, p.openalex_id
        HAVING COUNT(a.researcher_id) >= 2
    """)

    # Group by identical author set
    by_authors: dict[str, list[dict]] = {}
    for row in candidates:
        key = row['author_ids']
        by_authors.setdefault(key, []).append(row)

    groups: list[list[int]] = []
    for author_key, papers in by_authors.items():
        if len(papers) < 2:
            continue
        for i in range(len(papers)):
            for j in range(i + 1, len(papers)):
                pi, pj = papers[i], papers[j]
                # Skip pairs that share an identifier (handled by find_duplicate_groups)
                if (pi['doi'] and pj['doi'] and pi['doi'] == pj['doi']):
                    continue
                if (pi['openalex_id'] and pj['openalex_id'] and pi['openalex_id'] == pj['openalex_id']):
                    continue
                if _compatible_identity({**pi, 'author_ids': author_key.split(',')},
                                        {**pj, 'author_ids': author_key.split(',')}):
                    groups.append(sorted([pi['id'], pj['id']]))

    return _connected_groups(groups)


def merge_duplicate_papers() -> int:
    """Find and merge all duplicate paper groups. Returns count of merges."""
    id_groups = find_duplicate_groups()
    fuzzy_groups = find_fuzzy_duplicate_groups()
    groups = _connected_groups(id_groups + fuzzy_groups)
    if not groups:
        logger.info("No duplicate papers found")
        return 0
    logger.info("Found %d ID-based + %d fuzzy duplicate groups", len(id_groups), len(fuzzy_groups))

    logger.info("Found %d duplicate paper groups to merge", len(groups))
    merged = 0
    for group in groups:
        try:
            if merge_paper_group(group):
                merged += 1
        except Exception:
            logger.exception("Skipping failed merge for group %s", group)
    logger.info("Completed %d/%d merges", merged, len(groups))
    return merged
