"""Post-enrichment duplicate paper merging.

After OpenAlex enrichment assigns DOIs and OpenAlex IDs, this module finds papers
sharing the same identifier and merges them into a single canonical record.
"""
import logging
from database import Database

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
    doi_groups = Database.fetch_all(
        """SELECT doi, GROUP_CONCAT(id ORDER BY discovered_at) AS ids
           FROM papers WHERE doi IS NOT NULL
           GROUP BY doi HAVING COUNT(*) > 1"""
    )
    oa_groups = Database.fetch_all(
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
    papers = Database.fetch_all(
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

    with Database.get_connection() as conn:
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


def merge_duplicate_papers() -> int:
    """Find and merge all duplicate paper groups. Returns count of merges."""
    groups = find_duplicate_groups()
    if not groups:
        logger.info("No duplicate papers found")
        return 0

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
