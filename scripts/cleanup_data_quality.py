"""One-time data cleanup driven by the tests_data_quality/ failure list.

Each step targets one failing invariant class. Dry-run by default — prints
what would change; pass --apply to write. Steps run in dependency order
(e.g. blow-up papers are deleted before garbage researchers, researcher
merges before shared-URL dedup, title cleanup before near-dup merging).

    poetry run python scripts/cleanup_data_quality.py            # dry run, all steps
    poetry run python scripts/cleanup_data_quality.py --apply
    poetry run python scripts/cleanup_data_quality.py --steps null_bad_years,null_junk_abstracts --apply

Safe to re-run: every step is idempotent (it selects current violations).
After applying, `make check-data` should drop the corresponding failures.
"""
import argparse
import logging
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from database import fetch_all, fetch_one, get_connection  # noqa: E402
from database.papers import compute_title_hash, normalize_title  # noqa: E402
from database.snapshots import _STATUS_RANK  # noqa: E402
from paper_merge import _CHILD_TABLES, find_duplicate_groups  # noqa: E402
from publication import clean_title  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("cleanup")

APPLY = False
STEPS: dict[str, callable] = {}


def step(fn):
    STEPS[fn.__name__] = fn
    return fn


# ---------------------------------------------------------------- helpers

# Mirrors tests_data_quality/conftest.py::mojibake_condition
MOJIBAKE_BIGRAMS = ["â€", "Ã©", "Ã¨", "Ã¡", "Ã³", "Ãº", "Ã­", "Ã§", "Ã£",
                    "Ã¶", "Ã¼", "Ã±", "Ã¤", "Ã¸", "Ã¥"]


def is_mojibake(s: str | None) -> bool:
    return bool(s) and any(b in s for b in MOJIBAKE_BIGRAMS)


# Last-resort sequence map for strings that can't round-trip (a non-cp1252
# byte was lost in transit, e.g. 'â€"' where the 0x93/0x94 dash byte became a
# straight quote). Longest sequences first. Only ever applied to strings
# already flagged by the mojibake bigram detector.
_LOSSY_FIXES = [
    ("â€™", "’"), ("â€œ", "“"), ("â€¢", "•"), ('â€"', "–"),
    ("Ã¡", "á"), ("Ã©", "é"), ("Ã¨", "è"), ("Ã³", "ó"), ("Ãº", "ú"),
    ("Ã­", "í"), ("Ã§", "ç"), ("Ã£", "ã"), ("Ã¶", "ö"), ("Ã¼", "ü"),
    ("Ã±", "ñ"), ("Ã¤", "ä"), ("Ã¸", "ø"), ("Ã¥", "å"),
    ("Ã ", "à"), ("Ä ", "č"),
]


def repair_mojibake(s: str) -> str | None:
    """Reverse UTF-8-read-as-latin1 damage. Returns None if irreparable."""
    for codec in ("cp1252", "latin-1"):
        try:
            fixed = s.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if not is_mojibake(fixed):
            return fixed
    fixed = s
    for bad, good in _LOSSY_FIXES:
        fixed = fixed.replace(bad, good)
    return fixed if not is_mojibake(fixed) else None


def merge_paper_pair(cursor, canonical_id: int, dup_id: int) -> None:
    """Merge dup into canonical: coalesce metadata, reassign children, delete.

    Unlike paper_merge.merge_paper_group this keeps the caller's choice of
    canonical and never gives the survivor a second new_paper feed event.
    """
    cursor.execute(
        "SELECT doi, openalex_id, year, venue, abstract, draft_url, status "
        "FROM papers WHERE id = %s", (dup_id,))
    dup = cursor.fetchone()
    if dup is None:
        return
    doi, openalex_id, year, venue, abstract, draft_url, dup_status = dup

    cursor.execute(
        """UPDATE papers SET
             doi = COALESCE(doi, %s), openalex_id = COALESCE(openalex_id, %s),
             year = COALESCE(year, %s), venue = COALESCE(venue, %s),
             abstract = COALESCE(abstract, %s), draft_url = COALESCE(draft_url, %s)
           WHERE id = %s""",
        (doi, openalex_id, year, venue, abstract, draft_url, canonical_id))

    # Keep the higher-ranked status (the PR #153 no-regression rule).
    cursor.execute("SELECT status FROM papers WHERE id = %s", (canonical_id,))
    canon_status = cursor.fetchone()[0]
    if dup_status and _STATUS_RANK.get(dup_status, -1) > _STATUS_RANK.get(canon_status, -1):
        cursor.execute("UPDATE papers SET status = %s WHERE id = %s",
                       (dup_status, canonical_id))

    for table, col in _CHILD_TABLES:
        if table == "feed_events":
            continue
        cursor.execute(
            f"UPDATE IGNORE `{table}` SET `{col}` = %s WHERE `{col}` = %s",
            (canonical_id, dup_id))

    # new_paper events: at most one per paper. Reassign only if the canonical
    # has none; otherwise drop the dup's. Other event types carry over.
    cursor.execute(
        "SELECT COUNT(*) FROM feed_events WHERE paper_id = %s AND event_type = 'new_paper'",
        (canonical_id,))
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "UPDATE feed_events SET paper_id = %s WHERE paper_id = %s AND event_type = 'new_paper'",
            (canonical_id, dup_id))
    cursor.execute(
        "UPDATE feed_events SET paper_id = %s WHERE paper_id = %s AND event_type != 'new_paper'",
        (canonical_id, dup_id))
    cursor.execute("DELETE FROM feed_events WHERE paper_id = %s", (dup_id,))
    cursor.execute("DELETE FROM papers WHERE id = %s", (dup_id,))


_RESEARCHER_CHILD_TABLES = [
    ("authorship", "researcher_id"),
    ("html_content", "researcher_id"),
    ("researcher_fields", "researcher_id"),
    ("researcher_jel_codes", "researcher_id"),
    ("researcher_snapshots", "researcher_id"),
    ("researcher_urls", "researcher_id"),
]


def merge_researcher_pair(cursor, canonical_id: int, dup_id: int) -> None:
    """Merge dup researcher into canonical: coalesce profile, reassign, delete."""
    cursor.execute(
        "SELECT position, affiliation, description, openalex_author_id, bio "
        "FROM researchers WHERE id = %s", (dup_id,))
    dup = cursor.fetchone()
    if dup is None:
        return
    position, affiliation, description, oa_id, bio = dup

    for table, col in _RESEARCHER_CHILD_TABLES:
        cursor.execute(
            f"UPDATE IGNORE `{table}` SET `{col}` = %s WHERE `{col}` = %s",
            (canonical_id, dup_id))

    cursor.execute("DELETE FROM researchers WHERE id = %s", (dup_id,))
    cursor.execute(
        """UPDATE researchers SET
             position = COALESCE(position, %s), affiliation = COALESCE(affiliation, %s),
             description = COALESCE(description, %s), bio = COALESCE(bio, %s),
             openalex_author_id = COALESCE(openalex_author_id, %s)
           WHERE id = %s""",
        (position, affiliation, description, bio, oa_id, canonical_id))


def _researcher_weight(row: dict) -> tuple:
    """Sort key picking the canonical researcher: tracked URLs, OpenAlex ID,
    publication count, then age (lower id)."""
    return (-row["n_urls"], 0 if row["openalex_author_id"] else 1, -row["n_pubs"], row["id"])


def _fetch_researcher_weights(ids: list[int]) -> list[dict]:
    ph = ",".join(["%s"] * len(ids))
    return fetch_all(
        f"""SELECT r.id, r.openalex_author_id,
              (SELECT COUNT(*) FROM researcher_urls u WHERE u.researcher_id = r.id) n_urls,
              (SELECT COUNT(*) FROM authorship a WHERE a.researcher_id = r.id) n_pubs
            FROM researchers r WHERE r.id IN ({ph})""",
        tuple(ids))


def _run_writes(label: str, fn) -> None:
    """Execute fn(cursor) in one transaction when --apply is set."""
    if not APPLY:
        return
    with get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            fn(cursor)
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception("FAILED: %s — rolled back", label)
            raise
        finally:
            cursor.close()


# ---------------------------------------------------------------- steps


@step
def delete_author_blowup_papers():
    """Papers with >100 authors are page-level extraction blow-ups."""
    rows = fetch_all(
        """SELECT p.id, LEFT(p.title, 60) title, COUNT(*) n
           FROM papers p JOIN authorship a ON a.publication_id = p.id
           GROUP BY p.id HAVING COUNT(*) > 100""")
    for r in rows:
        log.info("  delete paper %s (%s authors): %s", r["id"], r["n"], r["title"])

    def writes(cur):
        for r in rows:
            cur.execute("DELETE FROM papers WHERE id = %s", (r["id"],))
    _run_writes("delete_author_blowup_papers", writes)
    return len(rows)


@step
def delete_garbage_researchers():
    """Names with digits/@/slashes and no tracked URL are extraction junk
    (LLM-invented authors like 'August 2017', '//' page artifacts)."""
    rows = fetch_all(
        """SELECT r.id, r.first_name, r.last_name FROM researchers r
           WHERE (r.first_name REGEXP '[0-9@/]' OR r.last_name REGEXP '[0-9@/]')
             AND NOT EXISTS (SELECT 1 FROM researcher_urls u WHERE u.researcher_id = r.id)""")
    for r in rows:
        log.info("  delete researcher %s: %r %r", r["id"], r["first_name"], r["last_name"])

    def writes(cur):
        for r in rows:
            cur.execute("DELETE FROM researchers WHERE id = %s", (r["id"],))
    _run_writes("delete_garbage_researchers", writes)
    return len(rows)


@step
def trim_researcher_names():
    rows = fetch_all(
        """SELECT id, first_name, last_name FROM researchers
           WHERE first_name != TRIM(first_name) OR last_name != TRIM(last_name)""")
    for r in rows:
        log.info("  trim researcher %s: %r %r", r["id"], r["first_name"], r["last_name"])

    def writes(cur):
        cur.execute(
            """UPDATE researchers SET first_name = TRIM(first_name), last_name = TRIM(last_name)
               WHERE first_name != TRIM(first_name) OR last_name != TRIM(last_name)""")
    _run_writes("trim_researcher_names", writes)
    return len(rows)


@step
def fix_researcher_url_schemes():
    """'https:/x' (single slash) typos break the fetcher."""
    rows = fetch_all(
        """SELECT id, url FROM researcher_urls
           WHERE url NOT LIKE 'http://%' AND url NOT LIKE 'https://%'""")
    fixes = []
    for r in rows:
        fixed = re.sub(r"^(https?):/+", r"\1://", r["url"])
        if re.match(r"^https?://", fixed):
            fixes.append((r["id"], fixed))
            log.info("  fix url %s: %s -> %s", r["id"], r["url"], fixed)
        else:
            log.info("  delete unfixable url %s: %s", r["id"], r["url"])
            fixes.append((r["id"], None))

    def writes(cur):
        for url_id, fixed in fixes:
            if fixed:
                cur.execute("UPDATE IGNORE researcher_urls SET url = %s WHERE id = %s",
                            (fixed, url_id))
            else:
                cur.execute("DELETE FROM researcher_urls WHERE id = %s", (url_id,))
    _run_writes("fix_researcher_url_schemes", writes)
    return len(fixes)


@step
def merge_researchers_shared_openalex():
    """Two researcher rows with the same OpenAlex author ID are one person."""
    groups = fetch_all(
        """SELECT openalex_author_id, GROUP_CONCAT(id ORDER BY id) ids
           FROM researchers WHERE openalex_author_id IS NOT NULL
           GROUP BY openalex_author_id HAVING COUNT(*) > 1""")
    merges = []
    for g in groups:
        ids = [int(x) for x in g["ids"].split(",")]
        weights = sorted(_fetch_researcher_weights(ids), key=_researcher_weight)
        canonical = weights[0]["id"]
        for w in weights[1:]:
            merges.append((canonical, w["id"]))
    log.info("  %d groups -> %d merges", len(groups), len(merges))

    def writes(cur):
        for canonical, dup in merges:
            merge_researcher_pair(cur, canonical, dup)
    _run_writes("merge_researchers_shared_openalex", writes)
    return len(merges)


@step
def merge_researchers_exact_name():
    """Same full name, both with publications — split profiles from
    disambiguation failures. Runs after the OpenAlex-ID merge."""
    groups = fetch_all(
        """SELECT GROUP_CONCAT(r.id ORDER BY r.id) ids
           FROM researchers r
           WHERE EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r.id)
           GROUP BY LOWER(first_name), LOWER(last_name)
           HAVING COUNT(*) > 1""")
    merges = []
    for g in groups:
        ids = [int(x) for x in g["ids"].split(",")]
        weights = sorted(_fetch_researcher_weights(ids), key=_researcher_weight)
        canonical = weights[0]["id"]
        for w in weights[1:]:
            merges.append((canonical, w["id"]))
    log.info("  %d name groups -> %d merges", len(groups), len(merges))

    def writes(cur):
        for canonical, dup in merges:
            merge_researcher_pair(cur, canonical, dup)
    _run_writes("merge_researchers_exact_name", writes)
    return len(merges)


@step
def dedupe_shared_researcher_urls():
    """A URL tracked under several researchers (department pages) double-
    extracts every paper. Keep the earliest row, drop the rest."""
    groups = fetch_all(
        """SELECT url, GROUP_CONCAT(id ORDER BY id) ids FROM researcher_urls
           GROUP BY url HAVING COUNT(DISTINCT researcher_id) > 1""")
    doomed = []
    for g in groups:
        ids = [int(x) for x in g["ids"].split(",")]
        doomed.extend(ids[1:])
        log.info("  url %s: keep row %s, drop %s", g["url"][:70], ids[0], ids[1:])

    def writes(cur):
        for row_id in doomed:
            cur.execute("DELETE FROM researcher_urls WHERE id = %s", (row_id,))
    _run_writes("dedupe_shared_researcher_urls", writes)
    return len(doomed)


@step
def merge_papers_by_identifier():
    """Papers sharing a DOI or OpenAlex ID (merge_duplicate_papers debt)."""
    groups = find_duplicate_groups()
    merges = []
    for ids in groups:
        ph = ",".join(["%s"] * len(ids))
        rows = fetch_all(
            f"""SELECT id, (doi IS NOT NULL) + (openalex_id IS NOT NULL) idw,
                  CHAR_LENGTH(COALESCE(title,'')) tl, discovered_at
                FROM papers WHERE id IN ({ph})""", tuple(ids))
        rows.sort(key=lambda r: (-r["idw"], -r["tl"], r["id"]))
        canonical = rows[0]["id"]
        for r in rows[1:]:
            merges.append((canonical, r["id"]))
            log.info("  merge paper %s into %s", r["id"], canonical)

    def writes(cur):
        for canonical, dup in merges:
            merge_paper_pair(cur, canonical, dup)
    _run_writes("merge_papers_by_identifier", writes)
    return len(merges)


@step
def clean_paper_titles():
    """Strip truncation markers (`...`, `…`, trailing `,`/`;`), metadata
    suffixes (`— Job Market Paper`), and repair mojibake. Renames go through
    a collision check: if the cleaned title already exists, the papers are
    merged (the existing, fuller-titled paper wins)."""
    rows = fetch_all(
        r"""SELECT id, title FROM papers
            WHERE title REGEXP '(\\.\\.\\.|…|,|;)$'
               OR title REGEXP '\\[(JMP|Draft|Working Paper|New!?)\\]$'
               OR title REGEXP '(--|—|–)[[:space:]]*(JMP|Working Paper|Job Market Paper|Draft)$'
               OR (""" + " OR ".join(
                   f"title LIKE BINARY '%{b}%'" for b in MOJIBAKE_BIGRAMS) + ")")
    renames, irreparable = [], []
    for r in rows:
        new = clean_title(r["title"])
        new = re.sub(r"(\.\.\.|…|,|;|\s)+$", "", new).strip()
        if is_mojibake(new):
            repaired = repair_mojibake(new)
            if repaired is None:
                irreparable.append(r)
                log.info("  IRREPARABLE mojibake title %s: %r", r["id"], r["title"][:70])
                continue
            new = repaired
        if new and new != r["title"]:
            renames.append((r["id"], r["title"], new))
            log.info("  rename %s: %r -> %r", r["id"], r["title"][:60], new[:60])

    def writes(cur):
        for paper_id, _old, new in renames:
            new_hash = compute_title_hash(new)
            cur.execute("SELECT id FROM papers WHERE title_hash = %s AND id != %s",
                        (new_hash, paper_id))
            existing = cur.fetchone()
            if existing:
                # The fuller-titled existing paper survives; this one merges in.
                merge_paper_pair(cur, existing[0], paper_id)
            else:
                cur.execute("UPDATE papers SET title = %s, title_hash = %s WHERE id = %s",
                            (new, new_hash, paper_id))
    _run_writes("clean_paper_titles", writes)
    return len(renames)


# Differing words that signal genuinely distinct papers, not title variants.
_DISTINCT_MARKERS = re.compile(
    r"\d|^(i|ii|iii|iv|v|comment|reply|rejoinder|appendix|corrigendum|erratum|"
    r"part|revisited|extension|update|updated)$")


@step
def merge_near_duplicate_papers():
    """Same researcher, ≥0.92 title similarity (the test's exact pairing).
    Pairs whose differing words contain digits or distinct-paper markers
    (Part II, Comment, …) are skipped and reported."""
    rows = fetch_all(
        """SELECT a.researcher_id, p.id, p.title
           FROM papers p JOIN authorship a ON a.publication_id = p.id
           WHERE p.title IS NOT NULL""")
    by_researcher: dict[int, list[tuple[int, str, str]]] = {}
    for r in rows:
        norm = normalize_title(r["title"])
        if norm:
            by_researcher.setdefault(r["researcher_id"], []).append((r["id"], r["title"], norm))

    pairs, skipped, seen = [], [], set()
    for papers in by_researcher.values():
        if len(papers) < 2:
            continue
        papers.sort(key=lambda x: x[2])
        for (id1, t1, n1), (id2, t2, n2) in zip(papers, papers[1:]):
            pair = tuple(sorted((id1, id2)))
            if id1 == id2 or pair in seen or n1[:20] != n2[:20]:
                continue
            if SequenceMatcher(None, n1.split(), n2.split()).ratio() < 0.92:
                continue
            seen.add(pair)
            w1, w2 = set(n1.split()), set(n2.split())
            diff = (w1 ^ w2)
            if any(_DISTINCT_MARKERS.search(w) for w in diff):
                skipped.append((pair, t1[:50], t2[:50]))
                continue
            pairs.append(pair)
            log.info("  merge pair %s: %r ~ %r", pair, t1[:55], t2[:55])
    for pair, t1, t2 in skipped:
        log.info("  SKIPPED (distinct-paper marker) %s: %r vs %r", pair, t1, t2)

    def writes(cur):
        for id1, id2 in pairs:
            cur.execute(
                """SELECT id, (doi IS NOT NULL) + (openalex_id IS NOT NULL) idw,
                     CHAR_LENGTH(title) tl
                   FROM papers WHERE id IN (%s, %s)""", (id1, id2))
            got = cur.fetchall()
            if len(got) < 2:  # one side already merged away earlier this run
                continue
            got = sorted(got, key=lambda r: (-r[1], -r[2], r[0]))
            merge_paper_pair(cur, got[0][0], got[1][0])
    _run_writes("merge_near_duplicate_papers", writes)
    return len(pairs)


@step
def trim_link_dois():
    """DOIs that kept URL path segments ('/pdf', '/abstract', Oxford article
    ids) — trim to the paper's DOI when it's a prefix, else strip the
    known junk suffix."""
    prefix_rows = fetch_all(
        """SELECT pl.id, pl.doi link_doi, p.doi paper_doi
           FROM paper_links pl JOIN papers p ON p.id = pl.paper_id
           WHERE pl.doi IS NOT NULL AND p.doi IS NOT NULL
             AND LOWER(pl.doi) LIKE CONCAT(LOWER(p.doi), '/%')""")
    fixes = {r["id"]: r["paper_doi"] for r in prefix_rows}
    suffix_rows = fetch_all(
        """SELECT id, doi FROM paper_links
           WHERE doi REGEXP '/(html|abstract|pdf|epdf|full|fulltext)$'""")
    for r in suffix_rows:
        fixes.setdefault(r["id"], re.sub(r"/(html|abstract|pdf|epdf|full|fulltext)$", "", r["doi"]))
    for link_id, doi in fixes.items():
        log.info("  link %s doi -> %s", link_id, doi)

    def writes(cur):
        for link_id, doi in fixes.items():
            cur.execute("UPDATE paper_links SET doi = %s WHERE id = %s", (doi, link_id))
    _run_writes("trim_link_dois", writes)
    return len(fixes)


@step
def null_bad_years():
    rows = fetch_all(
        """SELECT id, year FROM papers
           WHERE year IS NOT NULL
             AND (year NOT REGEXP '^(19|20)[0-9]{2}$'
                  OR CAST(year AS UNSIGNED) > YEAR(NOW()) + 2)""")
    for r in rows:
        log.info("  null year %r on paper %s", r["year"], r["id"])

    def writes(cur):
        cur.execute(
            """UPDATE papers SET year = NULL
               WHERE year IS NOT NULL
                 AND (year NOT REGEXP '^(19|20)[0-9]{2}$'
                      OR CAST(year AS UNSIGNED) > YEAR(NOW()) + 2)""")
    _run_writes("null_bad_years", writes)
    return len(rows)


_STATUS_VENUES = ("'working paper','draft','under review','submitted','r&r',"
                  "'revise and resubmit','reject and resubmit','work in progress',"
                  "'job market paper','jmp'")


@step
def null_status_venues():
    rows = fetch_all(
        f"SELECT id, venue FROM papers WHERE LOWER(TRIM(venue)) IN ({_STATUS_VENUES})")
    log.info("  %d status-phrase venues -> NULL", len(rows))

    def writes(cur):
        cur.execute(
            f"UPDATE papers SET venue = NULL WHERE LOWER(TRIM(venue)) IN ({_STATUS_VENUES})")
    _run_writes("null_status_venues", writes)
    return len(rows)


@step
def null_junk_abstracts():
    rows = fetch_all(
        """SELECT id, abstract FROM papers
           WHERE abstract IS NOT NULL AND (TRIM(abstract) = '' OR CHAR_LENGTH(abstract) < 40)""")
    log.info("  %d junk abstracts -> NULL", len(rows))

    def writes(cur):
        cur.execute(
            """UPDATE papers SET abstract = NULL
               WHERE abstract IS NOT NULL AND (TRIM(abstract) = '' OR CHAR_LENGTH(abstract) < 40)""")
    _run_writes("null_junk_abstracts", writes)
    return len(rows)


@step
def clear_draft_url_equals_source():
    rows = fetch_all(
        "SELECT id FROM papers WHERE draft_url IS NOT NULL AND draft_url = source_url")
    log.info("  %d draft_url=source_url -> NULL", len(rows))

    def writes(cur):
        cur.execute(
            """UPDATE papers SET draft_url = NULL, draft_url_status = 'unchecked',
                                 draft_url_checked_at = NULL
               WHERE draft_url IS NOT NULL AND draft_url = source_url""")
    _run_writes("clear_draft_url_equals_source", writes)
    return len(rows)


@step
def fix_mojibake_text():
    """Venue/abstract/coauthor/description mojibake (titles are handled by
    clean_paper_titles because they need a hash recompute)."""
    targets = [
        ("papers", "venue", "id"),
        ("papers", "abstract", "id"),
        ("openalex_coauthors", "display_name", "id"),
        ("researchers", "description", "id"),
        ("researchers", "first_name", "id"),
        ("researchers", "last_name", "id"),
    ]
    fixes, irreparable = [], []
    for table, col, pk in targets:
        cond = " OR ".join(f"{col} LIKE BINARY '%{b}%'" for b in MOJIBAKE_BIGRAMS)
        rows = fetch_all(f"SELECT {pk} pk, {col} val FROM {table} WHERE {cond}")
        for r in rows:
            repaired = repair_mojibake(r["val"])
            if repaired is None:
                irreparable.append((table, col, r["pk"], r["val"][:60]))
                log.info("  IRREPARABLE %s.%s row %s: %r", table, col, r["pk"], r["val"][:60])
            else:
                fixes.append((table, col, r["pk"], repaired))
                log.info("  repair %s.%s row %s -> %r", table, col, r["pk"], repaired[:60])

    def writes(cur):
        for table, col, pk_val, repaired in fixes:
            cur.execute(f"UPDATE {table} SET {col} = %s WHERE id = %s", (repaired, pk_val))
    _run_writes("fix_mojibake_text", writes)
    return len(fixes)


@step
def delete_seed_new_paper_events():
    """Seed imports must never appear in the feed (the is_seed guard)."""
    rows = fetch_all(
        """SELECT fe.id FROM feed_events fe JOIN papers p ON p.id = fe.paper_id
           WHERE fe.event_type = 'new_paper' AND p.is_seed = 1""")
    log.info("  %d seed new_paper events -> delete", len(rows))

    def writes(cur):
        for r in rows:
            cur.execute("DELETE FROM feed_events WHERE id = %s", (r["id"],))
    _run_writes("delete_seed_new_paper_events", writes)
    return len(rows)


@step
def fix_orphan_papers():
    """Papers with no authorship: re-attach to the researcher who owns the
    source_url; delete when no owner can be found. Runs late so earlier
    researcher deletions can't immediately re-create orphans."""
    rows = fetch_all(
        """SELECT p.id, p.source_url,
             (SELECT MIN(ru.researcher_id) FROM researcher_urls ru
              WHERE ru.url = p.source_url) owner
           FROM papers p
           WHERE NOT EXISTS (SELECT 1 FROM authorship a WHERE a.publication_id = p.id)""")
    attach = [(r["id"], r["owner"]) for r in rows if r["owner"]]
    delete = [r["id"] for r in rows if not r["owner"]]
    for pid, owner in attach:
        log.info("  attach paper %s to researcher %s", pid, owner)
    for pid in delete:
        log.info("  delete unattributable orphan paper %s", pid)

    def writes(cur):
        for pid, owner in attach:
            cur.execute(
                "INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order) "
                "VALUES (%s, %s, 1)", (owner, pid))
        for pid in delete:
            cur.execute("DELETE FROM papers WHERE id = %s", (pid,))
    _run_writes("fix_orphan_papers", writes)
    return len(rows)


@step
def reset_hash_without_content():
    """content_hash with no stored content blocks extraction forever (the
    no_content loop). Clearing the hash makes the next fetch store fresh
    content; clearing extracted_hash keeps the row out of the extraction
    queue until that happens."""
    row = fetch_one(
        """SELECT COUNT(*) n FROM html_content
           WHERE content_hash IS NOT NULL AND (content IS NULL OR content = '')""")
    log.info("  %d hash-without-content rows -> reset for refetch", row["n"])

    # Chunked with a commit per batch: one big transaction on this
    # mediumtext table OOMs the 512MB DB container (undo log).
    if APPLY:
        while True:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE html_content SET content_hash = NULL, extracted_hash = NULL
                       WHERE content_hash IS NOT NULL AND (content IS NULL OR content = '')
                       LIMIT 500""")
                done = cursor.rowcount == 0
                conn.commit()
                cursor.close()
            if done:
                break
    return row["n"]


# ---------------------------------------------------------------- main

# Dependency order (see module docstring).
STEP_ORDER = [
    "delete_author_blowup_papers",
    "delete_garbage_researchers",
    "trim_researcher_names",
    "fix_researcher_url_schemes",
    "merge_researchers_shared_openalex",
    "merge_researchers_exact_name",
    "dedupe_shared_researcher_urls",
    "merge_papers_by_identifier",
    "clean_paper_titles",
    "merge_near_duplicate_papers",
    "trim_link_dois",
    "null_bad_years",
    "null_status_venues",
    "null_junk_abstracts",
    "clear_draft_url_equals_source",
    "fix_mojibake_text",
    "delete_seed_new_paper_events",
    "fix_orphan_papers",
    "reset_hash_without_content",
]


def main():
    global APPLY
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default: dry run)")
    parser.add_argument("--steps", help="comma-separated subset of steps to run")
    args = parser.parse_args()
    APPLY = args.apply

    selected = STEP_ORDER
    if args.steps:
        requested = [s.strip() for s in args.steps.split(",")]
        unknown = [s for s in requested if s not in STEPS]
        if unknown:
            parser.error(f"unknown steps: {unknown}; available: {STEP_ORDER}")
        selected = [s for s in STEP_ORDER if s in requested]

    mode = "APPLY" if APPLY else "DRY RUN"
    log.info("=== cleanup_data_quality (%s) ===", mode)
    summary = {}
    for name in selected:
        log.info("\n--- %s ---", name)
        summary[name] = STEPS[name]()

    log.info("\n=== summary (%s) ===", mode)
    for name, count in summary.items():
        log.info("  %-40s %6d", name, count)


if __name__ == "__main__":
    main()
