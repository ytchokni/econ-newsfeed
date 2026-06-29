"""Researcher data access: find/create, URL management, CSV import, search."""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import unicodedata

from backend.database.connection import execute_query, fetch_one, fetch_all
from backend.database.llm import log_llm_usage
from backend.config import fix_encoding


from backend.database.search_helpers import (
    escape_like as _escape_like,
    escape_fulltext as _escape_fulltext,
    FT_MIN_TOKEN_SIZE as _FT_MIN_TOKEN_SIZE,
    TOP20_DEPT_KEYWORDS as _TOP20_DEPT_KEYWORDS,
    TOP5_JOURNAL_KEYWORDS as _TOP5_JOURNAL_KEYWORDS,
)

_TOP5_VENUE_LIKES = " OR ".join(
    ["p.venue LIKE %s"] * len(_TOP5_JOURNAL_KEYWORDS)
)
_TOP5_VENUE_PARAMS = [f"%{_escape_like(kw)}%" for kw in _TOP5_JOURNAL_KEYWORDS]


def refresh_has_top5(researcher_id: int) -> None:
    """Recompute has_top5_pub for a single researcher."""
    execute_query(
        f"""UPDATE researchers SET has_top5_pub = EXISTS(
                SELECT 1 FROM authorship a
                JOIN papers p ON p.id = a.publication_id
                WHERE a.researcher_id = %s AND ({_TOP5_VENUE_LIKES})
            ) WHERE id = %s""",
        (researcher_id, *_TOP5_VENUE_PARAMS, researcher_id),
    )


def _strip_accents(s: str) -> str:
    """Remove diacritical marks: Gérard -> Gerard, Andrés -> Andres, Ø -> O."""
    return ''.join(
        c for c in unicodedata.normalize('NFKD', s)
        if not unicodedata.combining(c)
    )


_COMPOUND_INITIAL_RE = re.compile(r'([A-Za-z])\.(?=[A-Za-z])')


def _tokenize_name(name: str) -> list[str]:
    """Normalize and tokenize a first name for comparison.

    Strips accents, replaces hyphens with spaces, and splits compound
    initials (R.A. -> R. A.) before whitespace-tokenizing.
    """
    s = _strip_accents(name)
    s = s.replace('-', ' ')
    s = _COMPOUND_INITIAL_RE.sub(r'\1. ', s)
    return s.split()


def _strip_initial(name: str) -> str | None:
    """If name is a single letter optionally followed by '.', return that letter lowercase. Else None."""
    stripped = name.strip()
    if len(stripped) == 1 and stripped.isalpha():
        return stripped.lower()
    if len(stripped) == 2 and stripped[0].isalpha() and stripped[1] == '.':
        return stripped[0].lower()
    return None


def _tokens_match(a: str, b: str) -> bool:
    """True if two first-name tokens are compatible: equal, or one is an initial matching the other's first char."""
    if a.lower() == b.lower():
        return True
    init_a = _strip_initial(a)
    init_b = _strip_initial(b)
    if init_a is not None and init_b is not None:
        return init_a == init_b
    if init_a is not None:
        return init_a == b[0].lower()
    if init_b is not None:
        return init_b == a[0].lower()
    return False


def is_compatible_name(name_a: str, name_b: str) -> bool:
    """True if two first names are plausibly the same person.

    Tokenizes both (with accent stripping, hyphen normalization, and compound
    initial splitting), aligns shorter to longer positionally. Each token pair
    must be equal or one must be a single-char initial matching the other's
    first char. Prefix semantics: if all tokens in the shorter name match,
    returns True.

    Examples:
        is_compatible_name("M.", "Max")                  -> True
        is_compatible_name("R.A.", "Ronald A.")          -> True
        is_compatible_name("Gérard", "Gerard")           -> True
        is_compatible_name("Pei-Tha", "Pei Tha")        -> True
        is_compatible_name("Michael", "Max")             -> False
    """
    if not name_a or not name_b:
        return False
    tokens_a = _tokenize_name(name_a)
    tokens_b = _tokenize_name(name_b)
    if not tokens_a or not tokens_b:
        return False
    shorter, longer = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    for i, short_tok in enumerate(shorter):
        if i >= len(longer):
            return False
        if not _tokens_match(short_tok, longer[i]):
            return False
    return True


def is_bad_researcher_name(first_name: str, last_name: str) -> bool:
    """Return True if the name is too malformed to create a researcher record.

    Rejects: empty/whitespace first or last names, single-letter/initial-only last names.
    """
    if not first_name or not first_name.strip():
        return True
    if not last_name or not last_name.strip():
        return True
    # Single letter with optional period: "A", "A.", "K", "K."
    stripped_last = last_name.strip()
    if re.match(r'^[A-Za-z]\.?$', stripped_last):
        return True
    return False


def first_name_is_initial_match(name_a: str, name_b: str) -> bool:
    """Deprecated: use is_compatible_name instead.

    Kept for backward compatibility — returns True only for the single-initial
    subset (exact matches return False, unlike is_compatible_name).
    """
    if not name_a or not name_b:
        return False
    init_a = _strip_initial(name_a)
    init_b = _strip_initial(name_b)
    # Both are full names (no initial) — not an initial match
    if init_a is None and init_b is None:
        return False
    # Both are initials — compare them
    if init_a is not None and init_b is not None:
        return init_a == init_b
    # One is initial, one is full name — compare initial to first char
    if init_a is not None:
        return init_a == name_b[0].lower()
    return init_b == name_a[0].lower()


def _longer_first_name(a: str, b: str) -> str:
    """Return whichever first name is longer, ignoring trailing periods."""
    return a if len(a.rstrip('.')) > len(b.rstrip('.')) else b


def _disambiguate_researcher(first_name: str, last_name: str, candidates: list[dict]) -> int | None:
    """Use LLM to check if any same-last-name candidate is the same person.
    Returns the matching researcher id (int) or None if no match.
    candidates: list of dicts with keys id, first_name, last_name."""
    candidates_text = "\n".join(f"- ID {c['id']}: {c['first_name']} {c['last_name']}" for c in candidates)
    prompt = (
        f'You are disambiguating researcher names. A publication lists the author as: '
        f'"{first_name} {last_name}"\n\n'
        f'The database contains these existing researchers with the same last name:\n'
        f'{candidates_text}\n\n'
        f'Is the author the same person as any of these researchers? Consider:\n'
        f'- "J. Smith" and "John Smith" are likely the same person\n'
        f'- An abbreviated first name may match a full first name\n'
        f'- Only match if you are confident\n\n'
        f'Respond with JSON only: {{"match_id": <id or null>}}'
    )
    try:
        from backend.llm.client import get_client, get_model
        client = get_client()
        model = get_model()
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=256,  # {"match_id": N} is ~10 tokens; 256 headroom covers any preamble leak
        )
        log_llm_usage("researcher_disambiguation", model, response.usage)
        content = response.choices[0].message.content or ""
        match = re.search(r'\{.*?\}', content, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            match_id = data.get('match_id')
            if match_id is not None:
                candidate_ids = {c['id'] for c in candidates}
                match_id_int = int(match_id)
                if match_id_int in candidate_ids:
                    return match_id_int
                logging.warning(
                    f"LLM returned match_id={match_id} not in candidate IDs {candidate_ids}; ignoring"
                )
    except Exception as e:
        logging.error(f"LLM researcher disambiguation error: {e}")
    return None


def get_researcher_id(first_name: str, last_name: str, position: str | None = None,
                      affiliation: str | None = None,
                      openalex_author_id: str | None = None,
                      conn: "mysql.connector.connection.MySQLConnection | None" = None) -> int:
    """Get the researcher ID based on name.

    Matching priority:
    1. Exact first_name + last_name match
    2. OpenAlex author ID match (deterministic, free)
    2.5. Initial match — single-char initial matches full first name (same last name)
    3. LLM disambiguation for same-last-name candidates
    4. Insert new researcher
    """
    # Name validation guard — reject bad names before any DB interaction
    if is_bad_researcher_name(first_name, last_name):
        logging.warning(
            "Rejected bad researcher name: first_name=%r last_name=%r", first_name, last_name
        )
        return None

    # Fix any mojibake in name/affiliation fields
    first_name, _ = fix_encoding(first_name)
    last_name, _ = fix_encoding(last_name)
    if position:
        position, _ = fix_encoding(position)
    if affiliation:
        affiliation, _ = fix_encoding(affiliation)

    def _fetch_one(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True, buffered=True)
            c.execute(query, params)
            row = c.fetchone()
            c.close()
            return row
        return fetch_one(query, params)

    def _fetch_all(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True, buffered=True)
            c.execute(query, params)
            rows = c.fetchall()
            c.close()
            return rows
        return fetch_all(query, params)

    def _execute(query, params):
        if conn is not None:
            c = conn.cursor(buffered=True)
            c.execute(query, params)
            conn.commit()
            lid = c.lastrowid
            c.close()
            return lid
        return execute_query(query, params)

    # 1. Exact match
    result = _fetch_one(
        "SELECT id FROM researchers WHERE first_name = %s AND last_name = %s",
        (first_name, last_name),
    )
    if result:
        return result['id']

    # 2. OpenAlex author ID match (cheap, deterministic — check before querying candidates)
    if openalex_author_id:
        result = _fetch_one(
            "SELECT id FROM researchers WHERE openalex_author_id = %s",
            (openalex_author_id,),
        )
        if result:
            logging.info(
                f"OpenAlex ID matched '{first_name} {last_name}' to researcher id={result['id']}"
            )
            return result['id']

    # Fetch same-last-name candidates (shared by Tier 2.5 and Tier 3)
    candidates = _fetch_all(
        "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
        (last_name,),
    )

    # 2.5. Compatible name match — initials, prefixes, multi-initials
    if candidates:
        initial_matches = [
            c for c in candidates
            if is_compatible_name(first_name, c['first_name'])
            and first_name.lower() != c['first_name'].lower()
        ]
        if len(initial_matches) == 1:
            match = initial_matches[0]
            longer_name = _longer_first_name(first_name, match['first_name'])
            if longer_name != match['first_name']:
                _execute(
                    "UPDATE researchers SET first_name = %s WHERE id = %s",
                    (longer_name, match['id']),
                )
            logging.info(
                f"Initial matched '{first_name} {last_name}' to researcher id={match['id']} ('{match['first_name']} {match['last_name']}')"
            )
            return match['id']

    # 3. Same-last-name candidates — let LLM decide if any is the same person
    if candidates:
        match_id = _disambiguate_researcher(first_name, last_name, candidates)
        if match_id is not None:
            logging.info(
                f"LLM matched '{first_name} {last_name}' to existing researcher id={match_id}"
            )
            # Backfill openalex_author_id if we have it
            if openalex_author_id:
                _execute(
                    "UPDATE researchers SET openalex_author_id = %s WHERE id = %s AND openalex_author_id IS NULL",
                    (openalex_author_id, match_id),
                )
            return match_id

    # 4. No match found — insert new researcher
    new_id = _execute(
        "INSERT INTO researchers (first_name, last_name, position, affiliation, openalex_author_id) VALUES (%s, %s, %s, %s, %s)",
        (first_name, last_name, position, affiliation, openalex_author_id),
    )
    return new_id


def merge_researchers(canonical_id: int, duplicate_id: int, conn) -> None:
    """Merge duplicate researcher into canonical: transfer authorship, JEL codes, metadata, then delete.

    Commits after all operations complete. The caller is responsible for rollback on exception.
    """
    if canonical_id == duplicate_id:
        raise ValueError(f"Cannot merge researcher into itself (same id={canonical_id})")

    c = conn.cursor(dictionary=True)

    # Fetch both researchers (need metadata columns for backfill)
    c.execute(
        "SELECT first_name, last_name, affiliation, description, position, openalex_author_id "
        "FROM researchers WHERE id = %s", (canonical_id,),
    )
    canonical = c.fetchone()
    c.execute(
        "SELECT first_name, last_name, affiliation, description, position, openalex_author_id "
        "FROM researchers WHERE id = %s", (duplicate_id,),
    )
    duplicate = c.fetchone()

    if not canonical or not duplicate:
        c.close()
        raise ValueError(f"Researcher not found: canonical={canonical_id} duplicate={duplicate_id}")

    # 1. Transfer authorship (two-step to avoid unique constraint violations)
    c.execute(
        "DELETE FROM authorship WHERE researcher_id = %s "
        "AND publication_id IN (SELECT publication_id FROM "
        "(SELECT publication_id FROM authorship WHERE researcher_id = %s) AS tmp)",
        (duplicate_id, canonical_id),
    )
    c.execute(
        "UPDATE authorship SET researcher_id = %s WHERE researcher_id = %s",
        (canonical_id, duplicate_id),
    )

    # 2. Transfer JEL codes (IGNORE skips duplicates)
    c.execute(
        "UPDATE IGNORE researcher_jel_codes SET researcher_id = %s WHERE researcher_id = %s",
        (canonical_id, duplicate_id),
    )

    # 3. Transfer user follows (IGNORE skips duplicates; CASCADE cleans up leftovers)
    c.execute(
        "UPDATE IGNORE user_follows SET researcher_id = %s WHERE researcher_id = %s",
        (canonical_id, duplicate_id),
    )

    # 4. Upgrade first_name to the longer variant
    longer_name = _longer_first_name(canonical['first_name'], duplicate['first_name'])
    if longer_name != canonical['first_name']:
        c.execute(
            "UPDATE researchers SET first_name = %s WHERE id = %s",
            (longer_name, canonical_id),
        )

    # 5. Backfill metadata where canonical has NULL
    c.execute(
        "UPDATE researchers SET "
        "affiliation = COALESCE(affiliation, %s), "
        "description = COALESCE(description, %s), "
        "position = COALESCE(position, %s), "
        "openalex_author_id = COALESCE(openalex_author_id, %s) "
        "WHERE id = %s",
        (duplicate.get('affiliation'), duplicate.get('description'),
         duplicate.get('position'), duplicate.get('openalex_author_id'),
         canonical_id),
    )

    # 6. Delete duplicate (cascade handles researcher_urls, html_content, researcher_fields, etc.)
    c.execute("DELETE FROM researchers WHERE id = %s", (duplicate_id,))

    c.close()
    conn.commit()

    logging.info(
        f"Merged researcher #{duplicate_id} ({duplicate['first_name']} {duplicate['last_name']}) "
        f"into #{canonical_id} ({canonical['first_name']} {canonical['last_name']})"
    )


def update_researcher_bio(researcher_id: int, bio: str) -> None:
    """Legacy: update researcher description only if the current description is NULL."""
    bio, _ = fix_encoding(bio)
    execute_query(
        "UPDATE researchers SET description = %s WHERE id = %s AND description IS NULL",
        (bio, researcher_id),
    )


def add_researcher_url(researcher_id: int, page_type: str, url: str) -> None:
    """Insert a new URL for a researcher into the researcher_urls table."""
    execute_query(
        "INSERT IGNORE INTO researcher_urls (researcher_id, page_type, url) VALUES (%s, %s, %s)",
        (researcher_id, page_type, url),
    )


_URL_DEACTIVATION_THRESHOLD = 3


def record_url_fetch_failure(url_id: int, error_type: str) -> None:
    """Record a fetch failure for a URL. Deactivates immediately for response_too_large,
    or after _URL_DEACTIVATION_THRESHOLD consecutive failures for other errors."""
    if error_type == "response_too_large":
        execute_query(
            """UPDATE researcher_urls
               SET is_active = FALSE, deactivated_at = NOW(), deactivation_reason = %s
               WHERE id = %s""",
            (error_type, url_id),
        )
        logging.warning("Deactivated URL %d: %s", url_id, error_type)
        return

    # Atomic increment + conditional deactivation (no TOCTOU race)
    execute_query(
        """UPDATE researcher_urls
           SET consecutive_failures = consecutive_failures + 1,
               is_active = IF(consecutive_failures + 1 >= %s, FALSE, is_active),
               deactivated_at = IF(consecutive_failures + 1 >= %s AND is_active = TRUE, NOW(), deactivated_at),
               deactivation_reason = IF(consecutive_failures + 1 >= %s, %s, deactivation_reason)
           WHERE id = %s""",
        (_URL_DEACTIVATION_THRESHOLD, _URL_DEACTIVATION_THRESHOLD, _URL_DEACTIVATION_THRESHOLD, "consecutive_failures", url_id),
    )
    row = fetch_one(
        "SELECT consecutive_failures FROM researcher_urls WHERE id = %s", (url_id,),
    )
    if row and row["consecutive_failures"] >= _URL_DEACTIVATION_THRESHOLD:
        logging.warning("Deactivated URL %d after %d consecutive failures", url_id, row["consecutive_failures"])


def record_url_fetch_success(url_id: int) -> None:
    """Reset the failure counter on a successful fetch (no-op if already zero)."""
    execute_query(
        "UPDATE researcher_urls SET consecutive_failures = 0 WHERE id = %s AND consecutive_failures > 0",
        (url_id,),
    )


def get_deactivated_urls() -> list[dict]:
    """Return all deactivated URLs with researcher info."""
    return fetch_all(
        """SELECT ru.id, ru.url, ru.page_type, ru.deactivation_reason, ru.deactivated_at,
                  ru.consecutive_failures,
                  CONCAT(r.first_name, ' ', r.last_name) AS researcher_name,
                  r.id AS researcher_id
           FROM researcher_urls ru
           JOIN researchers r ON r.id = ru.researcher_id
           WHERE ru.is_active = FALSE
           ORDER BY ru.deactivated_at DESC"""
    )


def get_at_risk_urls() -> list[dict]:
    """Return active URLs with 2+ consecutive failures (about to be deactivated)."""
    return fetch_all(
        """SELECT ru.id, ru.url, ru.page_type, ru.consecutive_failures,
                  CONCAT(r.first_name, ' ', r.last_name) AS researcher_name,
                  r.id AS researcher_id
           FROM researcher_urls ru
           JOIN researchers r ON r.id = ru.researcher_id
           WHERE ru.is_active = TRUE AND ru.consecutive_failures >= 2
           ORDER BY ru.consecutive_failures DESC"""
    )


def get_urls_needing_extraction() -> list[dict]:
    """Active researcher URLs whose stored HTML changed since last extraction.

    A URL needs extraction when its content_hash differs from extracted_hash
    (changed since last LLM run, or never extracted). URLs with no stored
    HTML are excluded (nothing to extract).

    Returns at most 200 rows — the worker processes them one at a time and
    re-polls when the batch is done.
    """
    query = """
        SELECT ru.id, ru.researcher_id, ru.url, ru.page_type
        FROM researcher_urls ru
        JOIN html_content hc ON hc.url_id = ru.id
        WHERE ru.is_active = TRUE
          AND hc.content_hash IS NOT NULL
          AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)
        ORDER BY ru.id
        LIMIT 200
    """
    return fetch_all(query)


def reactivate_url(url_id: int) -> None:
    """Re-activate a deactivated URL and reset all failure tracking."""
    execute_query(
        """UPDATE researcher_urls
           SET is_active = TRUE, consecutive_failures = 0,
               deactivated_at = NULL, deactivation_reason = NULL
           WHERE id = %s""",
        (url_id,),
    )


def import_data_from_file(file_path: str) -> None:
    """Import data from a CSV or TXT file into the database."""
    try:
        with open(file_path, mode='r', encoding='utf-8-sig') as file:
            reader = csv.reader(file)
            next(reader, None)  # Skip header
            for row in reader:
                if len(row) < 6:
                    logging.warning(f"Skipping incomplete row: {row}")
                    continue
                first_name, last_name, position, affiliation, page_type, url = row
                first_name, _ = fix_encoding(first_name)
                last_name, _ = fix_encoding(last_name)
                position, _ = fix_encoding(position)
                affiliation, _ = fix_encoding(affiliation)
                researcher_id = get_researcher_id(first_name, last_name, position, affiliation)
                add_researcher_url(researcher_id, page_type, url)
        logging.info("Data imported successfully from file")
    except Exception as e:
        logging.error("Error importing data from file: %s", type(e).__name__)


# ---------------------------------------------------------------------------
# Batch-fetch helpers
# ---------------------------------------------------------------------------

def get_urls_for_researchers(researcher_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch URLs for multiple researchers from researcher_urls.

    Returns {researcher_id: [{id, page_type, url}, ...]}.
    Empty input returns {}.
    """
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = fetch_all(
        f"SELECT researcher_id, id, page_type, url FROM researcher_urls "
        f"WHERE researcher_id IN ({placeholders})",
        tuple(researcher_ids),
    )
    result: dict[int, list[dict]] = {rid: [] for rid in researcher_ids}
    for row in rows:
        result[row['researcher_id']].append({
            "id": row['id'],
            "page_type": row['page_type'],
            "url": row['url'],
        })
    return result


def get_pub_counts_for_researchers(researcher_ids: list[int]) -> dict[int, int]:
    """Batch-fetch publication counts for multiple researchers via authorship GROUP BY.

    Returns {researcher_id: count}. Missing IDs default to 0.
    Empty input returns {}.
    """
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = fetch_all(
        f"SELECT researcher_id, COUNT(*) AS cnt FROM authorship "
        f"WHERE researcher_id IN ({placeholders}) GROUP BY researcher_id",
        tuple(researcher_ids),
    )
    result: dict[int, int] = {rid: 0 for rid in researcher_ids}
    for row in rows:
        result[row['researcher_id']] = row['cnt']
    return result


def get_fields_for_researchers(researcher_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch research fields for multiple researchers via researcher_fields JOIN research_fields.

    Returns {researcher_id: [{id, name, slug}, ...]} ordered by rf.name.
    Empty input returns {}.
    """
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = fetch_all(
        f"""SELECT rf_link.researcher_id, rf.id, rf.name, rf.slug
            FROM researcher_fields rf_link
            JOIN research_fields rf ON rf.id = rf_link.field_id
            WHERE rf_link.researcher_id IN ({placeholders})
            ORDER BY rf.name""",
        tuple(researcher_ids),
    )
    result: dict[int, list[dict]] = {rid: [] for rid in researcher_ids}
    for row in rows:
        result[row['researcher_id']].append({
            "id": row['id'],
            "name": row['name'],
            "slug": row['slug'],
        })
    return result


# ---------------------------------------------------------------------------
# Single-researcher queries
# ---------------------------------------------------------------------------

def get_researcher_detail(researcher_id: int) -> dict | None:
    """Fetch a single researcher by ID.

    Returns dict with columns: id, first_name, last_name, position, affiliation, description.
    Returns None if not found.
    """
    return fetch_one(
        "SELECT id, first_name, last_name, position, affiliation, description "
        "FROM researchers WHERE id = %s",
        (researcher_id,),
    )


def get_researcher_papers(researcher_id: int) -> list[dict]:
    """Fetch papers for a researcher via papers JOIN authorship, ordered by discovered_at DESC.

    Returns list of dicts with keys:
    id, title, year, venue, source_url, discovered_at, status, draft_url,
    abstract, draft_url_status, doi.
    """
    return fetch_all(
        """
        SELECT p.id, p.title, p.year, p.venue, p.source_url, p.discovered_at, p.status,
               p.draft_url, p.abstract, p.draft_url_status, p.doi
        FROM papers p
        JOIN authorship a ON a.publication_id = p.id
        WHERE a.researcher_id = %s
        ORDER BY p.discovered_at DESC
        """,
        (researcher_id,),
    )


# ---------------------------------------------------------------------------
# Researcher search
# ---------------------------------------------------------------------------

def search_researchers(
    *,
    search: str | None = None,
    institution: str | None = None,
    field_slug: str | None = None,
    position: str | None = None,
    preset: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """Search researchers with dynamic WHERE filters.

    Returns (rows, total_count). All filter params are optional.

    Base conditions (always applied):
    - Only validated researchers: have openalex_author_id OR researcher_urls entry
    - Hide initial-only names (CHAR_LENGTH > 2, not matching '^[A-Z]\\.$')

    Filters:
    - institution: r.affiliation LIKE %value%
    - position: r.position LIKE %value%
    - preset='top20': affiliation matches top-20 economics department keywords
    - field_slug: single slug uses =, comma-separated uses IN
    - search: FULLTEXT for >= _FT_MIN_TOKEN_SIZE chars, LIKE fallback for shorter
    """
    conditions: list[str] = []
    params: list = []

    # Base conditions: only validated researchers with at least one publication
    conditions.append(
        "(r.openalex_author_id IS NOT NULL OR EXISTS "
        "(SELECT 1 FROM researcher_urls ru WHERE ru.researcher_id = r.id))"
    )
    conditions.append(
        "EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r.id)"
    )

    # Hide abbreviated/initial-only names
    conditions.append(
        "r.first_name IS NOT NULL AND CHAR_LENGTH(r.first_name) > 2 "
        "AND r.first_name NOT REGEXP '^[A-Z]\\\\.$' "
        "AND r.last_name IS NOT NULL AND CHAR_LENGTH(r.last_name) > 2 "
        "AND r.last_name NOT REGEXP '^[A-Z]\\\\.$'"
    )

    if institution:
        conditions.append("r.affiliation LIKE %s")
        params.append(f"%{_escape_like(institution)}%")

    if position:
        conditions.append("r.position LIKE %s")
        params.append(f"%{_escape_like(position)}%")

    if preset == "top20":
        dept_conditions = " OR ".join(["r.affiliation LIKE %s"] * len(_TOP20_DEPT_KEYWORDS))
        conditions.append(f"({dept_conditions})")
        params.extend(f"%{_escape_like(kw)}%" for kw in _TOP20_DEPT_KEYWORDS)

    if preset == "top5_rr_accepted":
        venue_likes = " OR ".join(["p.venue LIKE %s"] * len(_TOP5_JOURNAL_KEYWORDS))
        conditions.append(
            f"EXISTS (SELECT 1 FROM authorship a "
            f"JOIN papers p ON p.id = a.publication_id "
            f"WHERE a.researcher_id = r.id "
            f"AND p.status IN ('accepted', 'revise_and_resubmit') "
            f"AND ({venue_likes}))"
        )
        params.extend(f"%{_escape_like(kw)}%" for kw in _TOP5_JOURNAL_KEYWORDS)

    if preset == "has_top5":
        conditions.append("r.has_top5_pub = TRUE")

    if field_slug:
        field_slugs = [f.strip() for f in field_slug.split(",") if f.strip()]
        if len(field_slugs) == 1:
            conditions.append(
                "EXISTS (SELECT 1 FROM researcher_fields rf "
                "JOIN research_fields f ON f.id = rf.field_id "
                "WHERE rf.researcher_id = r.id AND f.slug = %s)"
            )
            params.append(field_slugs[0])
        elif len(field_slugs) > 1:
            placeholders = ",".join(["%s"] * len(field_slugs))
            conditions.append(
                f"EXISTS (SELECT 1 FROM researcher_fields rf "
                f"JOIN research_fields f ON f.id = rf.field_id "
                f"WHERE rf.researcher_id = r.id AND f.slug IN ({placeholders}))"
            )
            params.extend(field_slugs)

    search_term = (search or "").strip()
    if search_term:
        if len(search_term) >= _FT_MIN_TOKEN_SIZE:
            conditions.append(
                "(MATCH(r.first_name, r.last_name) AGAINST (%s IN BOOLEAN MODE)"
                " OR CONCAT(r.first_name, ' ', r.last_name) LIKE %s ESCAPE '\\\\')"
            )
            params.append(_escape_fulltext(search_term))
            params.append(f"%{_escape_like(search_term)}%")
        else:
            escaped = f"%{_escape_like(search_term)}%"
            conditions.append(
                "(r.first_name LIKE %s ESCAPE '\\\\'"
                " OR r.last_name LIKE %s ESCAPE '\\\\'"
                " OR CONCAT(r.first_name, ' ', r.last_name) LIKE %s ESCAPE '\\\\')"
            )
            params.extend([escaped, escaped, escaped])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_row = fetch_one(
        f"SELECT COUNT(*) AS cnt FROM researchers r {where}",
        tuple(params),
    )
    total = count_row['cnt'] if count_row else 0

    rows = fetch_all(
        f"""
        SELECT r.id, r.first_name, r.last_name, r.position, r.affiliation, r.description
        FROM researchers r
        {where}
        ORDER BY r.last_name, r.first_name
        LIMIT %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    return rows, total
