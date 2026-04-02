"""JEL code database operations."""
import logging
from datetime import datetime, timezone

from database.connection import execute_query, fetch_all, get_connection

# Maps top-level JEL codes to research_fields.slug values.
# "Migration" has no top-level JEL code — handled via description keywords.
_JEL_TO_FIELD_SLUGS: dict[str, str] = {
    "C": "econometrics-methods",
    "E": "macroeconomics",
    "F": "international-trade",
    "G": "finance",
    "H": "public-economics",
    "I": "health-economics",
    "J": "labour-economics",
    "L": "industrial-organisation",
    "O": "development-economics",
    "P": "political-economy",
    "Z": "cultural-economics",
}

_MIGRATION_KEYWORDS = ("migration", "immigrant", "immigration", "migrant", "diaspora")


def sync_researcher_fields_from_jel(researcher_id: int, jel_codes: list[str]) -> None:
    """Clears existing field associations and re-derives them from the
    given JEL codes. The 'migration' field is inferred via keyword
    matching on the researcher's description.
    """
    slugs: set[str] = set()
    for code in jel_codes:
        slug = _JEL_TO_FIELD_SLUGS.get(code.upper().strip())
        if slug:
            slugs.add(slug)

    with get_connection() as conn:
        with conn.cursor() as cursor:
            # Keyword fallback for the migration field (no direct JEL code)
            cursor.execute(
                "SELECT description FROM researchers WHERE id = %s",
                (researcher_id,),
            )
            row = cursor.fetchone()
            description = row[0] if row else None
            if description:
                if any(kw in description.lower() for kw in _MIGRATION_KEYWORDS):
                    slugs.add("migration")

            cursor.execute(
                "DELETE FROM researcher_fields WHERE researcher_id = %s",
                (researcher_id,),
            )
            if slugs:
                placeholders = ",".join(["%s"] * len(slugs))
                cursor.execute(
                    f"SELECT id FROM research_fields WHERE slug IN ({placeholders})",
                    tuple(slugs),
                )
                field_ids = [r[0] for r in cursor.fetchall()]
                for field_id in field_ids:
                    cursor.execute(
                        "INSERT IGNORE INTO researcher_fields (researcher_id, field_id) "
                        "VALUES (%s, %s)",
                        (researcher_id, field_id),
                    )
            conn.commit()


def get_all_jel_codes() -> list[dict]:
    """Return all JEL codes ordered by code."""
    return fetch_all("SELECT code, name, parent_code FROM jel_codes ORDER BY code")


def get_jel_codes_for_researcher(researcher_id: int) -> list[dict]:
    """Return JEL codes for a single researcher."""
    return fetch_all(
        """SELECT jc.code, jc.name
           FROM researcher_jel_codes rjc
           JOIN jel_codes jc ON jc.code = rjc.jel_code
           WHERE rjc.researcher_id = %s
           ORDER BY jc.code""",
        (researcher_id,),
    )


def get_jel_codes_for_researchers(researcher_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch JEL codes for multiple researchers."""
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = fetch_all(
        f"""SELECT rjc.researcher_id, jc.code, jc.name
            FROM researcher_jel_codes rjc
            JOIN jel_codes jc ON jc.code = rjc.jel_code
            WHERE rjc.researcher_id IN ({placeholders})
            ORDER BY jc.code""",
        tuple(researcher_ids),
    )
    result: dict[int, list[dict]] = {rid: [] for rid in researcher_ids}
    for row in rows:
        result[row["researcher_id"]].append({"code": row["code"], "name": row["name"]})
    return result


def save_researcher_jel_codes(researcher_id: int, jel_codes: list[str]) -> None:
    """Replace a researcher's JEL codes with the given list.

    Deletes existing codes and inserts the new set in a single transaction.
    Invalid codes (not in jel_codes table) are skipped with a warning.
    """
    from mysql.connector.errors import IntegrityError

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM researcher_jel_codes WHERE researcher_id = %s",
                (researcher_id,),
            )
            for code in jel_codes:
                try:
                    cursor.execute(
                        """INSERT INTO researcher_jel_codes
                           (researcher_id, jel_code, classified_at)
                           VALUES (%s, %s, %s)""",
                        (researcher_id, code.upper().strip(), now),
                    )
                except IntegrityError:
                    logging.warning(
                        "Skipped unknown JEL code '%s' for researcher %d",
                        code, researcher_id,
                    )
            conn.commit()
    sync_researcher_fields_from_jel(researcher_id, jel_codes)


def _get_all_jel_codes_for_researcher(researcher_id: int) -> list[str]:
    """Return all JEL code strings currently assigned to a researcher."""
    from database.connection import fetch_all as _fetch_all
    rows = _fetch_all(
        "SELECT jel_code FROM researcher_jel_codes WHERE researcher_id = %s",
        (researcher_id,),
    )
    return [r["jel_code"] for r in rows]


def get_researchers_needing_classification() -> list[dict]:
    """Return researchers with a description but no JEL codes assigned."""
    return fetch_all(
        """SELECT r.id, r.first_name, r.last_name, r.description
           FROM researchers r
           LEFT JOIN researcher_jel_codes rjc ON rjc.researcher_id = r.id
           WHERE r.description IS NOT NULL
             AND r.description != ''
             AND rjc.researcher_id IS NULL
           ORDER BY r.id"""
    )


def save_paper_topics(paper_id: int, topics: list[dict]) -> None:
    """Store OpenAlex topics for a paper. Replaces existing topics."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM paper_topics WHERE paper_id = %s", (paper_id,)
            )
            for topic in topics:
                cursor.execute(
                    """INSERT INTO paper_topics
                       (paper_id, openalex_topic_id, topic_name, subfield_name,
                        field_name, domain_name, score)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        paper_id,
                        topic["openalex_topic_id"],
                        topic["topic_name"],
                        topic.get("subfield_name"),
                        topic.get("field_name"),
                        topic.get("domain_name"),
                        topic.get("score"),
                    ),
                )
            conn.commit()


def get_paper_topics_for_researcher(researcher_id: int) -> list[dict]:
    """Get all OpenAlex topics for papers authored by a researcher."""
    return fetch_all(
        """SELECT pt.topic_name, pt.score
           FROM paper_topics pt
           JOIN papers p ON p.id = pt.paper_id
           JOIN authorship a ON a.publication_id = p.id
           WHERE a.researcher_id = %s
           ORDER BY pt.score DESC""",
        (researcher_id,),
    )


def get_papers_needing_topics() -> list[dict]:
    """Get papers with openalex_id but no topics stored yet."""
    return fetch_all(
        """SELECT p.id, p.openalex_id
           FROM papers p
           LEFT JOIN paper_topics pt ON pt.paper_id = p.id
           WHERE p.openalex_id IS NOT NULL
             AND pt.id IS NULL"""
    )


def get_all_researcher_topics() -> dict[int, list[dict]]:
    """Batch-fetch all topics grouped by researcher.

    Returns {researcher_id: [{"topic_name": ..., "score": ...}, ...]}.
    Only includes researchers who have papers with stored topics.
    """
    rows = fetch_all(
        """SELECT a.researcher_id, pt.topic_name, pt.score
           FROM paper_topics pt
           JOIN authorship a ON a.publication_id = pt.paper_id
           ORDER BY a.researcher_id, pt.score DESC"""
    )
    result: dict[int, list[dict]] = {}
    for row in rows:
        rid = row["researcher_id"]
        if rid not in result:
            result[rid] = []
        result[rid].append({"topic_name": row["topic_name"], "score": row["score"]})
    return result


def add_researcher_jel_codes(researcher_id: int, jel_codes: list[str]) -> None:
    """Add JEL codes to a researcher without removing existing ones.

    Skips codes already assigned (duplicate key).
    Logs a warning for unknown JEL codes (FK violation).
    """
    from mysql.connector import errorcode
    from mysql.connector.errors import IntegrityError

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            for code in jel_codes:
                try:
                    cursor.execute(
                        """INSERT INTO researcher_jel_codes
                           (researcher_id, jel_code, classified_at)
                           VALUES (%s, %s, %s)""",
                        (researcher_id, code.upper().strip(), now),
                    )
                except IntegrityError as e:
                    if getattr(e, "errno", None) == errorcode.ER_DUP_ENTRY:
                        pass  # Already assigned — skip silently
                    else:
                        logging.warning(
                            "Skipped unknown JEL code '%s' for researcher %d",
                            code,
                            researcher_id,
                        )
            conn.commit()
    # Sync fields from the full set, not just the newly added codes
    all_codes = _get_all_jel_codes_for_researcher(researcher_id)
    sync_researcher_fields_from_jel(researcher_id, all_codes)
