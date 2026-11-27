"""JEL code database operations."""
import logging
from datetime import datetime, timezone

from backend.database.connection import execute_query, fetch_all, get_connection

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
    rows = fetch_all(
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


# ---------------------------------------------------------------------------
# Incremental classification (researcher_jel_state)
# ---------------------------------------------------------------------------

_ACTIVE_RESEARCHER_SQL = (
    "EXISTS (SELECT 1 FROM researcher_urls u "
    "WHERE u.researcher_id = r.id AND u.is_active = 1)"
)


def get_jel_classification_candidates(
    limit: int,
    now: datetime,
    reclassify_after_days: int = 30,
    researcher_ids: list[int] | None = None,
    force: bool = False,
) -> list[dict]:
    """Return tracked researchers whose JEL codes should be (re)computed.

    A researcher is due when:
    - the job has never looked at them (no state row), or a merge reset it;
    - the state is provisional (``input_hash`` NULL);
    - the last LLM call failed: retried after ``attempts`` days (max 30);
    - their bio or papers changed since ``checked_at`` and either nothing was
      classifiable before, a new bio arrived that was not used, or the last
      check is older than ``reclassify_after_days``.

    ``force`` skips these checks (every tracked researcher is a candidate).
    Researchers without codes and with recent papers come first, so the feed
    filter gains coverage fastest. Rows are only candidates: the caller
    compares input hashes before spending an LLM call.
    """
    where = [_ACTIVE_RESEARCHER_SQL]
    params: list = []
    if researcher_ids:
        where.append(f"r.id IN ({','.join(['%s'] * len(researcher_ids))})")
        params.extend(researcher_ids)
    if not force:
        where.append(
            """(
                s.researcher_id IS NULL
                OR s.input_hash IS NULL
                OR (s.attempts > 0
                    AND s.checked_at < %s - INTERVAL LEAST(s.attempts, 30) DAY)
                OR (s.attempts = 0
                    AND (r.description_updated_at > s.checked_at
                         OR EXISTS (SELECT 1 FROM authorship a
                                    JOIN papers p ON p.id = a.publication_id
                                    WHERE a.researcher_id = r.id
                                      AND p.discovered_at > s.checked_at))
                    AND (s.source = 'none'
                         OR (s.used_bio = 0 AND r.description IS NOT NULL
                             AND r.description_updated_at > s.checked_at)
                         OR s.checked_at < %s - INTERVAL %s DAY))
            )"""
        )
        params.extend([now, now, reclassify_after_days])
    params.append(limit)
    return fetch_all(
        f"""SELECT r.id, r.first_name, r.last_name, r.description,
                   r.openalex_author_id, s.input_hash,
                   COALESCE(s.attempts, 0) AS attempts,
                   EXISTS (SELECT 1 FROM researcher_jel_codes rjc
                           WHERE rjc.researcher_id = r.id) AS has_codes
            FROM researchers r
            LEFT JOIN researcher_jel_state s ON s.researcher_id = r.id
            WHERE {" AND ".join(where)}
            ORDER BY (s.researcher_id IS NULL) DESC,
                     has_codes ASC,
                     (SELECT MAX(a2.publication_id) FROM authorship a2
                      WHERE a2.researcher_id = r.id) DESC,
                     r.id DESC
            LIMIT %s""",
        tuple(params),
    )


def get_jel_inputs_for_researchers(
    researcher_ids: list[int], max_papers: int = 20,
) -> dict[int, dict]:
    """Batch-load the paper evidence used to classify researchers.

    Returns ``{researcher_id: {"papers": [...], "topics": [...]}}``. Papers
    are newest first (by year, then id) and capped at ``max_papers``; topics
    are the stored OpenAlex topics of those papers.
    """
    result: dict[int, dict] = {rid: {"papers": [], "topics": []} for rid in researcher_ids}
    if not researcher_ids:
        return result
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = fetch_all(
        f"""SELECT a.researcher_id, p.id, p.title, p.abstract, p.year
            FROM authorship a
            JOIN papers p ON p.id = a.publication_id
            WHERE a.researcher_id IN ({placeholders})
              AND p.title IS NOT NULL AND p.title != ''
            ORDER BY a.researcher_id, p.year DESC, p.id DESC""",
        tuple(researcher_ids),
    )
    paper_owner: dict[int, list[int]] = {}
    for row in rows:
        papers = result[row["researcher_id"]]["papers"]
        if len(papers) >= max_papers:
            continue
        papers.append({"id": row["id"], "title": row["title"],
                       "abstract": row.get("abstract"), "year": row.get("year")})
        paper_owner.setdefault(row["id"], []).append(row["researcher_id"])
    if not paper_owner:
        return result
    paper_ids = list(paper_owner)
    topic_rows = fetch_all(
        f"""SELECT paper_id, topic_name, score FROM paper_topics
            WHERE paper_id IN ({",".join(["%s"] * len(paper_ids))})
            ORDER BY paper_id, score DESC""",
        tuple(paper_ids),
    )
    for row in topic_rows:
        for rid in paper_owner.get(row["paper_id"], []):
            result[rid]["topics"].append(
                {"topic_name": row["topic_name"], "score": row["score"]}
            )
    return result


def save_jel_state(
    researcher_id: int,
    *,
    input_hash: str | None,
    source: str,
    used_bio: bool,
    model: str | None,
    attempts: int,
    checked_at: datetime,
) -> None:
    """Upsert the classification state for one researcher."""
    execute_query(
        """INSERT INTO researcher_jel_state
               (researcher_id, input_hash, source, used_bio, model, checked_at, attempts)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE
               input_hash = VALUES(input_hash), source = VALUES(source),
               used_bio = VALUES(used_bio), model = VALUES(model),
               checked_at = VALUES(checked_at), attempts = VALUES(attempts)""",
        (researcher_id, input_hash, source, bool(used_bio), model, checked_at, attempts),
    )


def touch_jel_state(researcher_id: int, checked_at: datetime) -> None:
    """Mark a researcher as checked without changing its classification."""
    execute_query(
        "UPDATE researcher_jel_state SET checked_at = %s WHERE researcher_id = %s",
        (checked_at, researcher_id),
    )


def count_jel_llm_calls_since(since: datetime) -> int:
    """Count JEL classification LLM calls logged since ``since`` (UTC)."""
    rows = fetch_all(
        "SELECT COUNT(*) AS n FROM llm_usage "
        "WHERE call_type = 'jel_classification' AND called_at >= %s",
        (since,),
    )
    return int(rows[0]["n"]) if rows else 0


def get_jel_coverage_stats() -> dict:
    """Coverage of the JEL filter: tracked researchers and visible feed events."""
    rows = fetch_all(
        f"""SELECT
              (SELECT COUNT(*) FROM researchers r WHERE {_ACTIVE_RESEARCHER_SQL}) AS tracked,
              (SELECT COUNT(*) FROM researchers r
               WHERE {_ACTIVE_RESEARCHER_SQL}
                 AND EXISTS (SELECT 1 FROM researcher_jel_codes rjc
                             WHERE rjc.researcher_id = r.id)) AS tracked_with_codes,
              (SELECT COUNT(*) FROM researcher_jel_state) AS checked,
              (SELECT COUNT(*) FROM feed_events) AS events,
              (SELECT COUNT(*) FROM feed_events fe
               WHERE EXISTS (SELECT 1 FROM authorship a
                             JOIN researcher_jel_codes rjc
                               ON rjc.researcher_id = a.researcher_id
                             WHERE a.publication_id = fe.paper_id)) AS events_with_codes"""
    )
    return dict(rows[0]) if rows else {}
