"""Post-enrichment duplicate paper merging.

After OpenAlex enrichment assigns DOIs and OpenAlex IDs, this module finds papers
sharing the same identifier and merges them into a single canonical record.
Also does fuzzy title matching for papers with identical author sets.
"""
import logging
import re
from difflib import SequenceMatcher
from backend.database import fetch_all, get_connection

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

    # Merge overlapping groups (papers sharing DOI AND openalex_id)
    merged: list[set[int]] = []
    for group in raw_groups:
        found = None
        for i, existing in enumerate(merged):
            if group & existing:
                found = i
                break
        if found is not None:
            merged[found] |= group
        else:
            merged.append(group)

    return [sorted(g) for g in merged]


def merge_paper_group(paper_ids: list[int]) -> None:
    """Merge duplicate papers into the earliest-discovered canonical record."""
    papers = fetch_all(
        f"""SELECT id, discovered_at, abstract, year, venue
            FROM papers WHERE id IN ({','.join(['%s'] * len(paper_ids))})
            ORDER BY discovered_at""",
        tuple(paper_ids),
    )
    if len(papers) < 2:
        return

    canonical_id = papers[0]['id']
    duplicates = papers[1:]
    dup_ids = [p['id'] for p in duplicates]

    logger.info("Merging papers %s into canonical %s", dup_ids, canonical_id)

    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            for dup in duplicates:
                cursor.execute(
                    """UPDATE papers SET
                        abstract = COALESCE(abstract, %s),
                        year = COALESCE(year, %s),
                        venue = COALESCE(venue, %s)
                    WHERE id = %s""",
                    (dup['abstract'], dup['year'], dup['venue'], canonical_id),
                )

            # UPDATE IGNORE skips rows that would violate UNIQUE constraints
            # (already exist for canonical_id). CASCADE deletion cleans up the rest.
            for dup_id in dup_ids:
                for table, col in _CHILD_TABLES:
                    cursor.execute(
                        f"UPDATE IGNORE `{table}` SET `{col}` = %s WHERE `{col}` = %s",
                        (canonical_id, dup_id),
                    )

            for dup_id in dup_ids:
                cursor.execute("DELETE FROM papers WHERE id = %s", (dup_id,))

            conn.commit()
            logger.info("Merged %d duplicates into paper %s", len(dup_ids), canonical_id)
        except Exception:
            conn.rollback()
            logger.exception("Failed to merge papers %s", paper_ids)
            raise
        finally:
            cursor.close()


_FUZZY_THRESHOLD = 0.85

_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'of', 'on', 'in', 'at', 'to', 'for',
    'and', 'or', 'by', 'with', 'from', 'as', 'is', 'are',
})


def _normalize(title: str) -> str:
    t = title.lower().replace('-', '').replace("'s", "").replace("’s", "")
    return re.sub(r'[^\w\s]', '', t)


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
    """Find papers with identical author sets and similar titles.

    Groups papers by their exact author set, then within each group finds
    pairs whose titles are similar above _FUZZY_THRESHOLD.  Skips pairs that
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
                if _title_similarity(pi['title'], pj['title']) >= _FUZZY_THRESHOLD:
                    groups.append(sorted([pi['id'], pj['id']]))

    return groups


def merge_duplicate_papers() -> int:
    """Find and merge all duplicate paper groups. Returns count of merges."""
    id_groups = find_duplicate_groups()
    fuzzy_groups = find_fuzzy_duplicate_groups()
    groups = id_groups + fuzzy_groups
    if not groups:
        logger.info("No duplicate papers found")
        return 0
    logger.info("Found %d ID-based + %d fuzzy duplicate groups", len(id_groups), len(fuzzy_groups))

    logger.info("Found %d duplicate paper groups to merge", len(groups))
    merged = 0
    for group in groups:
        try:
            merge_paper_group(group)
            merged += 1
        except Exception:
            logger.exception("Skipping failed merge for group %s", group)
    logger.info("Completed %d/%d merges", merged, len(groups))
    return merged
