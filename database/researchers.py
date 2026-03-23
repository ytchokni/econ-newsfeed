"""Researcher data access: find/create, URL management, CSV import."""
from __future__ import annotations

import csv
import json
import logging
import os
import re

from database.connection import execute_query, fetch_one, fetch_all
from database.llm import log_llm_usage


def _strip_initial(name: str) -> str | None:
    """If name is a single letter optionally followed by '.', return that letter lowercase. Else None."""
    stripped = name.strip()
    if len(stripped) == 1 and stripped.isalpha():
        return stripped.lower()
    if len(stripped) == 2 and stripped[0].isalpha() and stripped[1] == '.':
        return stripped[0].lower()
    return None


def first_name_is_initial_match(name_a: str, name_b: str) -> bool:
    """Return True when one name is a single-char initial matching the other's first character.

    Handles 'L.', 'L', or 'l.' matching 'Liam'. Returns False for exact matches,
    multi-char prefixes, or different initials.
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
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model,
        )
        log_llm_usage("researcher_disambiguation", model, response.usage)
        content = response.choices[0].message.content
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
    1.5. Initial match — single-char initial matches full first name (same last name)
    2. OpenAlex author ID match (deterministic, free)
    3. LLM disambiguation for same-last-name candidates
    4. Insert new researcher
    """
    def _fetch_one(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True)
            c.execute(query, params)
            row = c.fetchone()
            c.close()
            return row
        return fetch_one(query, params)

    def _fetch_all(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True)
            c.execute(query, params)
            rows = c.fetchall()
            c.close()
            return rows
        return fetch_all(query, params)

    def _execute(query, params):
        if conn is not None:
            c = conn.cursor()
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

    # 1.5. Initial match — "L." matches "Liam" for same last name
    candidates = _fetch_all(
        "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
        (last_name,),
    )
    if candidates:
        initial_matches = [
            c for c in candidates
            if first_name_is_initial_match(first_name, c['first_name'])
        ]
        if len(initial_matches) == 1:
            match = initial_matches[0]
            longer_name = first_name if len(first_name.rstrip('.')) > len(match['first_name'].rstrip('.')) else match['first_name']
            _execute(
                "UPDATE researchers SET first_name = %s WHERE id = %s",
                (longer_name, match['id']),
            )
            logging.info(
                f"Initial matched '{first_name} {last_name}' to researcher id={match['id']} ('{match['first_name']} {match['last_name']}')"
            )
            return match['id']

    # 2. OpenAlex author ID match
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

    # 3. Same-last-name candidates — let LLM decide if any is the same person
    if candidates is None:
        candidates = _fetch_all(
            "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
            (last_name,),
        )
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

    All operations run in the provided connection's transaction (caller manages commit/rollback).
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

    # 3. Upgrade first_name to the longer variant
    longer_name = canonical['first_name'] if len(canonical['first_name'].rstrip('.')) > len(duplicate['first_name'].rstrip('.')) else duplicate['first_name']
    c.execute(
        "UPDATE researchers SET first_name = %s WHERE id = %s",
        (longer_name, canonical_id),
    )

    # 4. Backfill metadata where canonical has NULL
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

    # 5. Delete duplicate (cascade handles researcher_urls, html_content, researcher_fields, etc.)
    c.execute("DELETE FROM researchers WHERE id = %s", (duplicate_id,))

    c.close()
    conn.commit()

    # 6. Log
    logging.info(
        f"Merged researcher #{duplicate_id} ({duplicate['first_name']} {duplicate['last_name']}) "
        f"into #{canonical_id} ({canonical['first_name']} {canonical['last_name']})"
    )


def update_researcher_bio(researcher_id: int, bio: str) -> None:
    """Legacy: update researcher description only if the current description is NULL."""
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
                researcher_id = get_researcher_id(first_name, last_name, position, affiliation)
                add_researcher_url(researcher_id, page_type, url)
        logging.info("Data imported successfully from file")
    except Exception as e:
        logging.error("Error importing data from file: %s", type(e).__name__)
