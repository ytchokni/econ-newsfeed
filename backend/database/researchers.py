"""Researcher data access: find/create, URL management, CSV import, search."""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
import unicodedata
from contextlib import contextmanager

from backend.database.connection import execute_query, fetch_one, fetch_all, get_connection
from backend.database.identity import merge_researcher_into, same_person, strip_repeated_surname
from backend.database.llm import log_llm_usage
from backend.database.extraction_queue import prioritise
from backend.config import fix_encoding


from backend.database.search_helpers import (
    escape_like as _escape_like,
)
from backend.database import researcher_index as _researcher_index
from backend.database.top_presets import (
    top5_publication_clause as _top5_publication_clause,
    top5_venue_clause as _top5_venue_clause,
    top20_affiliation_clause as _top20_affiliation_clause,
)


def refresh_has_top5_for(researcher_ids) -> None:
    """Recompute has_top5_pub for the given researchers.

    A researcher has a top-5 when they author a published or accepted paper in
    one of the five core journals (see ``backend.database.top_presets``).
    """
    ids = sorted({int(i) for i in researcher_ids})
    if not ids:
        return
    clause, params = _top5_publication_clause("p.venue", "p.status")
    placeholders = ", ".join(["%s"] * len(ids))
    execute_query(
        f"""UPDATE researchers r SET r.has_top5_pub = EXISTS(
                SELECT 1 FROM authorship a
                JOIN papers p ON p.id = a.publication_id
                WHERE a.researcher_id = r.id AND {clause}
            ) WHERE r.id IN ({placeholders})""",
        (*params, *ids),
    )


def _has_top5_target_sql() -> tuple[str, list[str]]:
    clause, params = _top5_publication_clause("p.venue", "p.status")
    return (
        "researchers r LEFT JOIN ("
        "SELECT DISTINCT a.researcher_id FROM authorship a "
        f"JOIN papers p ON p.id = a.publication_id WHERE {clause}"
        ") t5 ON t5.researcher_id = r.id"
    ), params


def has_top5_flag_changes() -> dict:
    """Count researchers whose stored has_top5_pub differs from the rule (read-only)."""
    from_sql, params = _has_top5_target_sql()
    tracked = "EXISTS (SELECT 1 FROM researcher_urls ru WHERE ru.researcher_id = r.id)"
    row = fetch_one(
        f"""SELECT
                COALESCE(SUM(r.has_top5_pub), 0) AS flagged_before,
                COALESCE(SUM(t5.researcher_id IS NOT NULL), 0) AS flagged_after,
                COALESCE(SUM(r.has_top5_pub AND t5.researcher_id IS NULL), 0) AS cleared,
                COALESCE(SUM(NOT r.has_top5_pub AND t5.researcher_id IS NOT NULL), 0) AS newly_set,
                COALESCE(SUM(r.has_top5_pub AND {tracked}), 0) AS tracked_before,
                COALESCE(SUM(t5.researcher_id IS NOT NULL AND {tracked}), 0) AS tracked_after
            FROM {from_sql}""",
        tuple(params),
    )
    return {k: int(v) for k, v in (row or {}).items()}


def refresh_all_has_top5() -> int:
    """Recompute has_top5_pub for every researcher; return the rows changed."""
    from_sql, params = _has_top5_target_sql()
    target = "(t5.researcher_id IS NOT NULL)"
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE {from_sql} SET r.has_top5_pub = {target} "
                f"WHERE r.has_top5_pub <> {target}",
                tuple(params),
            )
            changed = cursor.rowcount
        conn.commit()
    return changed


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


_LOWERCASE_PARTICLES = frozenset({
    "de", "del", "della", "di", "du", "da", "das", "dos", "do",
    "el", "al", "la", "le", "les", "lo", "los", "las",
    "van", "von", "den", "der", "het", "ten", "ter",
    "bin", "ibn", "ben", "bint",
    "y", "e", "i",
    "af", "av", "op",
})


_INITIALS_RE = re.compile(r'^([^\W\d_]\.)+[^\W\d_]?\.?$', re.UNICODE)


def _capitalize_word(word: str) -> str:
    """Capitalize a single word, handling Mc/Mac/O' prefixes and initials."""
    if _INITIALS_RE.match(word):
        return word.upper()
    low = word.lower()
    if low.startswith("mc") and len(word) > 2:
        return "Mc" + word[2:].capitalize()
    if low.startswith("mac") and len(word) > 3 and word[3:4].isalpha():
        return "Mac" + word[3:].capitalize()
    if len(word) >= 2 and word[1] == "'":
        return word[0].upper() + "'" + word[2:].capitalize()
    return word.capitalize()


def normalize_name_case(name: str) -> str:
    """Normalize capitalization of a researcher name component.

    Handles: ALL CAPS, all lowercase, Mc/Mac/O' prefixes,
    lowercase particles (van, de, von, …), hyphenated names.
    Leaves already-mixed-case names unchanged (e.g. "LeBlanc").
    """
    if not name or not name.strip():
        return name

    stripped = name.strip()

    if any('一' <= c <= '鿿' or '぀' <= c <= 'ヿ' or '가' <= c <= '힯' for c in stripped):
        return stripped

    if stripped == stripped.upper() or stripped == stripped.lower():
        parts = stripped.split()
        result = []
        for idx, part in enumerate(parts):
            hyph_parts = part.split("-")
            capitalized_hyph = []
            for hp in hyph_parts:
                if idx > 0 and hp.lower() in _LOWERCASE_PARTICLES:
                    capitalized_hyph.append(hp.lower())
                else:
                    capitalized_hyph.append(_capitalize_word(hp))
            result.append("-".join(capitalized_hyph))
        return " ".join(result)

    return stripped


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


def is_abbreviation_of(name_a: str, name_b: str) -> bool:
    """True if one first name is a substring of the other (accent-insensitive).

    Catches nickname pairs like Chris/Christopher, Jon/Jonathan, Max/Maximilian
    where one form is literally contained in the other.
    """
    a = _strip_accents(name_a).lower().strip()
    b = _strip_accents(name_b).lower().strip()
    if not a or not b:
        return False
    return a in b or b in a


def matches_author_name(first_name: str, last_name: str, display_name: str) -> bool:
    """Match given names and the complete surname, never a surname alone.

    OpenAlex may include middle names or initials. Require its surname suffix
    to agree with the local surname before comparing the remaining given names.
    """
    surname = [s.casefold() for s in _tokenize_name(last_name)]
    display = _tokenize_name(display_name)
    if not surname or len(display) <= len(surname):
        return False
    if [s.casefold() for s in display[-len(surname):]] != surname:
        return False
    return is_compatible_name(first_name, ' '.join(display[:-len(surname)]))


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


_prefetch_local = threading.local()


def _disambiguation_key(first_name: str, last_name: str, candidates: list[dict]) -> tuple:
    return (first_name, last_name, tuple(sorted(c['id'] for c in candidates)))


@contextmanager
def disambiguation_prefetch():
    """Collect LLM disambiguation answers made before a save transaction.

    Inside, prefetch_disambiguation() asks the model and remembers the answer;
    get_researcher_id() inside the transaction then reuses it instead of
    calling the model while holding row locks (and the extraction gate). A
    candidate list that changed in between misses the cache and is asked again.
    """
    previous = getattr(_prefetch_local, "answers", None)
    _prefetch_local.answers = {}
    try:
        yield
    finally:
        _prefetch_local.answers = previous


def prefetch_disambiguation(first_name: str, last_name: str) -> None:
    """Run get_researcher_id's read-only steps and, where it would ask the
    model (tier 3), ask now and cache the answer. Writes nothing."""
    answers = getattr(_prefetch_local, "answers", None)
    if answers is None or is_bad_researcher_name(first_name, last_name):
        return
    first_name, _ = fix_encoding(first_name)
    last_name, _ = fix_encoding(last_name)
    first_name = normalize_name_case(first_name)
    last_name = normalize_name_case(last_name)
    if fetch_one("SELECT id FROM researchers WHERE first_name = %s AND last_name = %s",
                 (first_name, last_name)):
        return
    candidates = fetch_all("SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
                           (last_name,))
    if not candidates:
        return
    initial_matches = [c for c in candidates
                       if is_compatible_name(first_name, c['first_name'])
                       and first_name.lower() != c['first_name'].lower()]
    if len(initial_matches) == 1:
        return
    key = _disambiguation_key(first_name, last_name, candidates)
    if key not in answers:
        answers[key] = _ask_disambiguation(first_name, last_name, candidates)


def _disambiguate_researcher(first_name: str, last_name: str, candidates: list[dict]) -> int | None:
    """Return the matching researcher id among same-last-name candidates, or None.

    Uses an answer prefetched before the save transaction when there is one.
    """
    answers = getattr(_prefetch_local, "answers", None)
    if answers:
        key = _disambiguation_key(first_name, last_name, candidates)
        if key in answers:
            return answers[key]
    return _ask_disambiguation(first_name, last_name, candidates)


def _ask_disambiguation(first_name: str, last_name: str, candidates: list[dict]) -> int | None:
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
        from backend.llm import throttle
        from backend.llm.client import get_client, get_model
        client = get_client()
        model = get_model()
        # An extraction worker holds the save gate; the call may wait for the
        # per-minute token budget, so let the other workers save meanwhile.
        # PaperSaver prefetches answers before its transaction, so this
        # normally holds no row locks (a cache miss still can).
        with throttle.extraction_gate.released():
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
    2. Unambiguous OpenAlex author ID match with compatible names
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

    # Normalize capitalization (fixes ALL CAPS / all lowercase from LLM / HTML)
    first_name = normalize_name_case(first_name)
    last_name = normalize_name_case(last_name)
    first_name = strip_repeated_surname(first_name.strip(), last_name.strip())

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

    # Existing IDs can be contaminated by old surname-only backfills. Never
    # treat an ID as stronger evidence than a conflicting person's name.
    if openalex_author_id:
        id_candidates = _fetch_all(
            "SELECT id, first_name, last_name FROM researchers WHERE openalex_author_id = %s",
            (openalex_author_id,),
        )
        compatible = [c for c in id_candidates if same_person(
            first_name, last_name, c['first_name'], c['last_name'])]
        if len(compatible) == 1:
            logging.info(
                "OpenAlex ID and name matched '%s %s' to researcher id=%s",
                first_name, last_name, compatible[0]['id'],
            )
            return compatible[0]['id']
        if id_candidates:
            logging.warning("Ignoring conflicting or ambiguous OpenAlex author ID %s for %s %s",
                            openalex_author_id, first_name, last_name)
            openalex_author_id = None

    # Fetch same-last-name candidates (shared by Tier 2.5 and Tier 3)
    candidates = _fetch_all(
        "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
        (last_name,),
    )

    # 2.5. Compatible name match — initials, prefixes, multi-initials,
    # nicknames ("Andy" for an existing "Andrew"), accent variants
    if candidates:
        initial_matches = [
            c for c in candidates
            if same_person(first_name, last_name, c['first_name'], c['last_name'])
            and first_name.lower() != c['first_name'].lower()
        ]
        if len(initial_matches) == 1:
            match = initial_matches[0]
            stored = strip_repeated_surname(match['first_name'], last_name)
            longer_name = _longer_first_name(first_name, stored)
            if longer_name != match['first_name'] and is_compatible_name(first_name, stored):
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
            matched = next((c for c in candidates if c['id'] == match_id), None)
            if openalex_author_id and matched and is_compatible_name(first_name, matched['first_name']):
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
    """Merge duplicate researcher into canonical and commit.

    Moves authorship, URLs, follows, JEL codes and the rest (see
    identity.merge_researcher_into), then deletes the duplicate. The caller
    is responsible for rollback on exception.
    """
    if canonical_id == duplicate_id:
        raise ValueError(f"Cannot merge researcher into itself (same id={canonical_id})")
    c = conn.cursor(dictionary=True, buffered=True)
    try:
        if not merge_researcher_into(c, canonical_id, duplicate_id):
            raise ValueError(f"Researcher not found: canonical={canonical_id} duplicate={duplicate_id}")
    finally:
        c.close()
    conn.commit()


def update_researcher_bio(researcher_id: int, bio: str) -> None:
    """Legacy: update researcher description only if the current description is NULL."""
    bio, _ = fix_encoding(bio)
    execute_query(
        "UPDATE researchers SET description = %s WHERE id = %s AND description IS NULL",
        (bio, researcher_id),
    )


def add_researcher_url(researcher_id: int, page_type: str, url: str) -> None:
    """Insert a new URL for a researcher into the researcher_urls table.

    A URL already tracked for someone with a different name is a shared
    (department, institute or news) page, not a personal page: refused.
    """
    if url_tracked_for_someone_else(researcher_id, url):
        logging.warning("Not adding %s for researcher %s: tracked for another researcher",
                        url, researcher_id)
        return
    execute_query(
        "INSERT IGNORE INTO researcher_urls (researcher_id, page_type, url) VALUES (%s, %s, %s)",
        (researcher_id, page_type, url),
    )


def url_tracked_for_someone_else(researcher_id: int, url: str) -> bool:
    """True if another researcher whose name differs tracks this URL."""
    rows = fetch_all(
        """SELECT r.id, r.first_name, r.last_name FROM researcher_urls ru
           JOIN researchers r ON r.id = ru.researcher_id
           WHERE ru.url = %s AND ru.researcher_id != %s""",
        (url, researcher_id),
    )
    me = fetch_one("SELECT first_name, last_name FROM researchers WHERE id = %s", (researcher_id,))
    if not me:
        return False
    return any(not same_person(me['first_name'], me['last_name'], r['first_name'], r['last_name'], strong=True)
               for r in rows)


def page_owners(url: str) -> list[dict]:
    """Researchers tracking a URL, lowest ID first."""
    return fetch_all(
        """SELECT DISTINCT r.id, r.first_name, r.last_name FROM researchers r
           JOIN researcher_urls ru ON ru.researcher_id = r.id
           WHERE ru.url = %s ORDER BY r.id""",
        (url,),
    )


def deactivate_unowned_url(url: str, reason: str) -> None:
    """Stop crawling a page that is not its owners' personal page (every row of it)."""
    execute_query(
        """UPDATE researcher_urls
           SET is_active = FALSE, deactivated_at = NOW(), deactivation_reason = %s
           WHERE url = %s AND is_active = TRUE""",
        (reason, url),
    )
    logging.warning("Deactivated %s: %s", url, reason)


def flag_url_ownership(url: str, flag: str | None) -> None:
    """Mark (or, with None, clear) a page whose owner it never names.

    Flagged pages stay active; their owner is not credited by position-0
    attachment, and they are listed for review in the admin Health tab.
    """
    if flag:
        execute_query(
            """UPDATE researcher_urls SET ownership_flag = %s, ownership_flagged_at = NOW()
               WHERE url = %s AND (ownership_flag IS NULL OR ownership_flag != %s)""",
            (flag, url, flag),
        )
    else:
        execute_query(
            "UPDATE researcher_urls SET ownership_flag = NULL, ownership_flagged_at = NULL "
            "WHERE url = %s AND ownership_flag IS NOT NULL",
            (url,),
        )


def get_ownership_review_urls() -> list[dict]:
    """Active URLs flagged because the page never names its owner."""
    return fetch_all(
        """SELECT ru.id, ru.url, ru.page_type, ru.ownership_flag, ru.ownership_flagged_at,
                  CONCAT(r.first_name, ' ', r.last_name) AS researcher_name,
                  r.id AS researcher_id
           FROM researcher_urls ru
           JOIN researchers r ON r.id = ru.researcher_id
           WHERE ru.is_active = TRUE AND ru.ownership_flag IS NOT NULL
           ORDER BY ru.ownership_flagged_at DESC
           LIMIT 500"""
    )


_URL_DEACTIVATION_THRESHOLD = 3
# A URL deactivated for one of these reasons is fetched again after this many
# days (see Researcher.get_all_researcher_urls); sites get rebuilt, moved or
# made public again. Both are a property of the site's current build, so they
# deactivate on the first failure rather than after _URL_DEACTIVATION_THRESHOLD.
TOO_LARGE_RECHECK_DAYS = 30
RECHECK_REASONS = ("response_too_large", "login_required")
# SQL list of RECHECK_REASONS (constants, not user input).
RECHECK_REASONS_SQL = ", ".join(f"'{r}'" for r in RECHECK_REASONS)


def record_url_fetch_failure(url_id: int, error_type: str) -> None:
    """Record a fetch failure for a URL. Deactivates immediately for RECHECK_REASONS,
    or after _URL_DEACTIVATION_THRESHOLD consecutive failures for other errors."""
    if error_type in RECHECK_REASONS:
        execute_query(
            """UPDATE researcher_urls
               SET is_active = FALSE, deactivated_at = NOW(), deactivation_reason = %s
               WHERE id = %s""",
            (error_type, url_id),
        )
        logging.warning("Deactivated URL %d: %s", url_id, error_type)
        return

    # Atomic increment + conditional deactivation (no TOCTOU race).
    #
    # A URL inactive for a RECHECK_REASONS reason is only fetched as a
    # recheck (TOO_LARGE_RECHECK_DAYS). Any other failure there restarts its recheck
    # clock and keeps the reason; counting it as an ordinary failure would
    # relabel it consecutive_failures (never rechecked again) and, until
    # then, leave deactivated_at old so every scrape fetched it again.
    #
    # MySQL applies SET assignments left to right, each seeing the values
    # already assigned. So consecutive_failures is incremented last (the
    # threshold tests see the old count: deactivation on the
    # _URL_DEACTIVATION_THRESHOLD-th failure, not one earlier), and the
    # recheck test stays true or false throughout because the clauses
    # before it change is_active/deactivation_reason only when it is false.
    recheck = f"(is_active = FALSE AND deactivation_reason IN ({RECHECK_REASONS_SQL}))"
    reached = "consecutive_failures + 1 >= %s"
    execute_query(
        f"""UPDATE researcher_urls
           SET deactivated_at = IF({recheck}, NOW(),
                   IF({reached} AND is_active = TRUE, NOW(), deactivated_at)),
               deactivation_reason = IF({recheck}, deactivation_reason,
                   IF({reached}, %s, deactivation_reason)),
               is_active = IF({recheck}, is_active, IF({reached}, FALSE, is_active)),
               consecutive_failures = IF({recheck}, consecutive_failures, consecutive_failures + 1)
           WHERE id = %s""",
        (_URL_DEACTIVATION_THRESHOLD, _URL_DEACTIVATION_THRESHOLD, "consecutive_failures",
         _URL_DEACTIVATION_THRESHOLD, url_id),
    )
    row = fetch_one(
        "SELECT consecutive_failures FROM researcher_urls WHERE id = %s", (url_id,),
    )
    if row and row["consecutive_failures"] >= _URL_DEACTIVATION_THRESHOLD:
        logging.warning("Deactivated URL %d after %d consecutive failures", url_id, row["consecutive_failures"])


def record_url_fetch_success(url_id: int) -> None:
    """Reset failure tracking on a successful fetch (no-op if nothing to reset).

    A URL deactivated for a RECHECK_REASONS reason that now fetches (within
    the size limit, without a sign-in redirect; see TOO_LARGE_RECHECK_DAYS)
    is reactivated. URLs deactivated
    for other reasons are not fetched, so they are never reactivated here.

    MySQL applies single-table SET assignments left to right, so is_active is
    assigned last: the IF()s must see its value before the update.
    """
    execute_query(
        f"""UPDATE researcher_urls
           SET consecutive_failures = 0,
               deactivated_at = IF(is_active, deactivated_at, NULL),
               deactivation_reason = IF(is_active, deactivation_reason, NULL),
               is_active = TRUE
           WHERE id = %s
             AND (consecutive_failures > 0
                  OR (is_active = FALSE AND deactivation_reason IN ({RECHECK_REASONS_SQL})))""",
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


def get_urls_needing_extraction(exclude_url_ids: tuple[int, ...] = (),
                                limit: int | None = 200) -> list[dict]:
    """Active researcher URLs whose stored HTML changed since last extraction.

    A URL needs extraction when its content_hash differs from extracted_hash
    (changed since last LLM run, or never extracted). URLs with no stored
    HTML are excluded (nothing to extract).

    Returns at most `limit` rows (all of them for None) in priority order, see
    backend.database.extraction_queue — the worker processes a batch and
    re-polls when it is done, so the order is recomputed every batch.
    """
    # Only columns of idx_extraction_state (plus researcher_urls): reading any
    # other html_content column makes MySQL fetch every stored page (~60 s on
    # production) instead of answering from the index (~0.2 s).
    query = """
        SELECT ru.id, ru.researcher_id, ru.url, ru.page_type, hc.content_hash,
               hc.extracted_hash IS NULL AS never_extracted
        FROM researcher_urls ru
        JOIN html_content hc ON hc.url_id = ru.id
        WHERE ru.is_active = TRUE
          AND hc.content_hash IS NOT NULL
          AND hc.has_stored_content = TRUE
          AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)
    """
    # Pages cooling down after repeated failures (record_extraction_failure)
    # are found separately and excluded by id: any other html_content column
    # in the query above takes it off idx_extraction_state and makes MySQL
    # read every MEDIUMTEXT row (~60 s on prod instead of 0.09 s).
    cooling = fetch_all(
        "SELECT url_id FROM html_content"
        " WHERE extraction_retry_at > NOW() AND extraction_failed_hash <=> content_hash"
    ) or []
    exclude_url_ids = tuple(dict.fromkeys(
        tuple(exclude_url_ids) + tuple(row['url_id'] for row in cooling)))
    if exclude_url_ids:
        query += " AND ru.id NOT IN (" + ",".join(["%s"] * len(exclude_url_ids)) + ")"
    query += " ORDER BY ru.id"
    rows = fetch_all(query, exclude_url_ids) if exclude_url_ids else fetch_all(query)
    _attach_pending_since(rows)
    return prioritise(rows, limit=limit)


def _attach_pending_since(rows: list[dict], chunk: int = 500) -> None:
    """Add when each page's text last changed, by url_id lookups (~0.7 s for
    12k pages on production; reading these columns in the queue query instead
    scans every stored page). content_changed_at is NULL on rows last changed
    before it existed; their last fetch time stands in."""
    by_id = {r['id']: r for r in rows}
    ids = list(by_id)
    for start in range(0, len(ids), chunk):
        part = ids[start:start + chunk]
        found = fetch_all(
            "SELECT url_id, COALESCE(content_changed_at, timestamp) AS pending_since "
            "FROM html_content WHERE url_id IN (" + ",".join(["%s"] * len(part)) + ")",
            tuple(part),
        )
        for f in found:
            by_id[f['url_id']]['pending_since'] = f['pending_since']


EXTRACTION_MAX_FAILURES = 3
EXTRACTION_COOLDOWN_SECONDS = 3600
EXTRACTION_MAX_COOLDOWN_SECONDS = 7 * 24 * 3600


def record_extraction_failure(url_id: int, content_hash: str | None) -> dict | None:
    """Count a failed extraction of one page version, persisted across restarts.

    The count is per content hash: a new page version starts again at one.
    From EXTRACTION_MAX_FAILURES failures on, the page is left out of
    get_urls_needing_extraction until extraction_retry_at, with the cooldown
    doubling per further failure up to EXTRACTION_MAX_COOLDOWN_SECONDS, so a
    page that always fails costs a few calls a week instead of one per poll.
    mark_extracted clears the state.

    MySQL applies SET clauses left to right: the count is updated first, and
    extraction_retry_at deliberately reads the new count.
    """
    execute_query(
        """UPDATE html_content
           SET extraction_failures = IF(extraction_failed_hash <=> %s, extraction_failures + 1, 1),
               extraction_failed_hash = %s,
               extraction_retry_at = IF(extraction_failures >= %s,
                   NOW() + INTERVAL LEAST(%s * POW(2, extraction_failures - %s), %s) SECOND,
                   NULL)
           WHERE url_id = %s""",
        (content_hash, content_hash, EXTRACTION_MAX_FAILURES, EXTRACTION_COOLDOWN_SECONDS,
         EXTRACTION_MAX_FAILURES, EXTRACTION_MAX_COOLDOWN_SECONDS, url_id),
    )
    return fetch_one(
        "SELECT extraction_failures, extraction_retry_at FROM html_content WHERE url_id = %s",
        (url_id,),
    )


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

    Returns dict with columns: id, first_name, last_name, position, affiliation,
    description, openalex_author_id. Returns None if not found.
    """
    return fetch_one(
        "SELECT id, first_name, last_name, position, affiliation, description, "
        "openalex_author_id FROM researchers WHERE id = %s",
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
    - Hide initials-only stubs ("A.", "A. Y.", "HTT Tran"); short names like Li stay

    Filters:
    - institution: r.affiliation LIKE %value%
    - position: r.position LIKE %value%
    - preset='top20': affiliation matches top-20 economics department keywords
    - field_slug: single slug uses =, comma-separated uses IN
    - search: matched and ranked by the in-process name index
      (see backend/database/researcher_index.py); each row then carries
      ``match``: "name", "affiliation" or "similar" (typo fallback)
    """
    conditions, params = _directory_conditions(
        institution=institution, field_slug=field_slug, position=position, preset=preset,
    )
    if _researcher_index.query_words(search):
        return _search_ranked(search, conditions, params, offset=offset, limit=limit)

    where = "WHERE " + " AND ".join(conditions)

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
        ORDER BY r.last_name, r.first_name, r.id
        LIMIT %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    return rows, total


_BASE_CONDITION_COUNT = 3
_ID_CHUNK = 2000


def _directory_conditions(
    *,
    institution: str | None,
    field_slug: str | None,
    position: str | None,
    preset: str | None,
) -> tuple[list[str], list]:
    """WHERE fragments for the directory: the base guards, then user filters."""
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

    # Hide initials-only stubs ("A.", "A. Y.", "HTT Tran") but not short real
    # names (Li, Wu, Xu); same rule as researcher_index.is_initials_stub.
    conditions.append(
        "r.first_name IS NOT NULL AND TRIM(r.first_name) <> '' "
        "AND r.last_name IS NOT NULL AND TRIM(r.last_name) <> '' "
        "AND NOT REGEXP_LIKE(TRIM(r.first_name), %s, 'c') "
        "AND NOT REGEXP_LIKE(TRIM(r.last_name), %s, 'c') "
        "AND NOT (REGEXP_LIKE(TRIM(r.first_name), %s, 'c') "
        "AND REGEXP_LIKE(TRIM(r.last_name), %s, 'c'))"
    )
    params.extend([
        _researcher_index.INITIALS_ONLY, _researcher_index.INITIALS_ONLY,
        _researcher_index.CAPITAL_INITIALS, _researcher_index.HAS_LOWERCASE,
    ])

    if institution:
        conditions.append("r.affiliation LIKE %s")
        params.append(f"%{_escape_like(institution)}%")

    if position:
        conditions.append("r.position LIKE %s")
        params.append(f"%{_escape_like(position)}%")

    if preset == "top20":
        dept_clause, dept_params = _top20_affiliation_clause("r.affiliation")
        conditions.append(dept_clause)
        params.extend(dept_params)

    if preset == "top5_rr_accepted":
        venue_clause, venue_params = _top5_venue_clause("p.venue")
        conditions.append(
            f"EXISTS (SELECT 1 FROM authorship a "
            f"JOIN papers p ON p.id = a.publication_id "
            f"WHERE a.researcher_id = r.id "
            f"AND p.status IN ('accepted', 'revise_and_resubmit') "
            f"AND {venue_clause})"
        )
        params.extend(venue_params)

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

    return conditions, params


def _fetch_all_late(sql: str, params=()) -> list[dict]:
    # Look fetch_all up at call time so tests can swap the module attribute.
    return fetch_all(sql, params)


def _name_index() -> _researcher_index.ResearcherIndex:
    return _researcher_index.get_index(_fetch_all_late)


def warm_researcher_index() -> None:
    """Build the search index in the background at API start-up."""
    _researcher_index.warm_index(_fetch_all_late)


def _match_label(match: _researcher_index.Match) -> str:
    if match.similar:
        return "similar"
    affiliation_only = (_researcher_index.TIER_AFFILIATION, _researcher_index.TIER_AFFILIATION_WORD)
    return "affiliation" if match.tier in affiliation_only else "name"


def _search_ranked(
    search: str, conditions: list[str], params: list, *, offset: int, limit: int,
) -> tuple[list[dict], int]:
    """Directory search: the index ranks, SQL applies filters and loads the page.

    The index already holds the base guards (``listed``); user filters are
    checked in SQL for the ranked ids. The page query repeats every condition,
    so a researcher changed since the index was built is never shown wrongly.
    """
    matches = list(_name_index().search(search, listed_only=True).matches)
    if matches and len(conditions) > _BASE_CONDITION_COUNT:
        allowed = _ids_matching(conditions, params, [m.researcher_id for m in matches])
        matches = [m for m in matches if m.researcher_id in allowed]
    total = len(matches)
    page = matches[offset:offset + limit]
    if not page:
        return [], total
    placeholders = ",".join(["%s"] * len(page))
    found = fetch_all(
        f"""
        SELECT r.id, r.first_name, r.last_name, r.position, r.affiliation, r.description
        FROM researchers r
        WHERE {" AND ".join(conditions)} AND r.id IN ({placeholders})
        """,
        (*params, *[m.researcher_id for m in page]),
    )
    by_id = {row["id"]: row for row in found}
    rows = []
    for match in page:
        row = by_id.get(match.researcher_id)
        if row is not None:
            rows.append({**row, "match": _match_label(match)})
    return rows, total


def _ids_matching(conditions: list[str], params: list, ids: list[int]) -> set[int]:
    where = " AND ".join(conditions)
    allowed: set[int] = set()
    for start in range(0, len(ids), _ID_CHUNK):
        chunk = ids[start:start + _ID_CHUNK]
        placeholders = ",".join(["%s"] * len(chunk))
        allowed.update(
            row["id"]
            for row in fetch_all(
                f"SELECT r.id FROM researchers r WHERE {where} AND r.id IN ({placeholders})",
                (*params, *chunk),
            )
        )
    return allowed


def researcher_search_suggestion(search: str | None, *, listed_only: bool = True) -> str | None:
    """"Did you mean ...?" text when ``search`` only matched by similar spelling.

    Uses the index's cached result for the same query, so calling it after
    search_researchers costs one primary-key lookup.
    """
    if not _researcher_index.query_words(search):
        return None
    result = _name_index().search(search, listed_only=listed_only)
    if not result.corrections or result.corrected_from is None:
        return None
    row = fetch_one(
        "SELECT first_name, last_name FROM researchers WHERE id = %s",
        (result.corrected_from,),
    )
    if not row:
        return None
    return _researcher_index.suggest_query(
        search, dict(result.corrections), f"{row['first_name'] or ''} {row['last_name'] or ''}",
    )


def search_researcher_options(*, search: str = "", researcher_ids: list[int] | None = None) -> list[dict]:
    """Return a bounded name chooser, or names for an existing selection."""
    if researcher_ids:
        placeholders = ",".join(["%s"] * len(researcher_ids))
        return fetch_all(
            f"SELECT id, CONCAT_WS(' ', first_name, last_name) AS name, affiliation "
            f"FROM researchers WHERE id IN ({placeholders}) ORDER BY last_name, first_name LIMIT 50",
            tuple(researcher_ids),
        )
    words = _researcher_index.query_words(search)
    if len(" ".join(words)) < 2:
        return []
    ids = _name_index().search(search, listed_only=False).ids[:20]
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"SELECT id, CONCAT_WS(' ', first_name, last_name) AS name, affiliation "
        f"FROM researchers WHERE id IN ({placeholders})",
        tuple(ids),
    )
    by_id = {row["id"]: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]
