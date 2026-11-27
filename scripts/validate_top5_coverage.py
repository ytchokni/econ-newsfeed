#!/usr/bin/env python3
"""Validate how completely we observe articles published in the top-5 economics journals.

Ground truth comes from Crossref (research articles in AER, Econometrica, JPE, QJE and
REStud), optionally enriched with OpenAlex author IDs. Our side is a READ-ONLY export of
`papers`, `paper_links`, `authorship` and `researchers` (plus stored page HTML for the
researchers needed to diagnose misses), taken either from the production MySQL container
over SSH or from the local database. Nothing is ever written to a database.

Every Crossref article is matched against our papers in three tiers (DOI, normalised
title, conservative fuzzy title) and then classified:

  observed        matched to at least one of our papers
    c_*           ... but the record is wrong: still a working paper / R&R, missing or
                  different venue (c_doi_unlinked: correct record but no DOI attached)
    d             ... and it matches more than one of our papers (duplicates)
  a_*             not observed, and no author is a tracked researcher (expected miss)
  b_*             not observed although an author is tracked: crawl/extraction gap,
                  split by what the tracked researcher's stored pages contain

Usage (all read-only; responses and DB exports are cached under --cache-dir):

    poetry run python scripts/validate_top5_coverage.py                       # prod over SSH, 2020..today
    poetry run python scripts/validate_top5_coverage.py --from-year 2022 --journals aer,qje
    poetry run python scripts/validate_top5_coverage.py --source local        # local DB (.env)
    poetry run python scripts/validate_top5_coverage.py --refresh-db          # re-export today's DB snapshot

Outputs in --out: articles.csv (one row per article), summary.json, summary.md.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html as html_module
import json
import logging
import os
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:  # Local mode reads DB settings from .env; SSH mode never touches a local DB.
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # pragma: no cover
    pass
_LOCAL_DB_CONFIGURED = bool(os.environ.get("DB_HOST"))
# backend.config validates these at import time; the title helpers we reuse need no DB.
for _var, _default in (("DB_HOST", "unused"), ("DB_USER", "unused"), ("DB_PASSWORD", "unused"),
                       ("DB_NAME", "unused"), ("GOOGLE_API_KEY", "unused")):
    os.environ.setdefault(_var, _default)

from backend.database.papers import normalize_title  # noqa: E402
from backend.pipeline.feed_events import _normalize_html_for_matching  # noqa: E402

log = logging.getLogger("top5")

USER_AGENT = "top5-coverage-validator/1.0 (read-only research data audit)"
CROSSREF_API = "https://api.crossref.org"
OPENALEX_API = "https://api.openalex.org"
CROSSREF_SELECT = ",".join([
    "DOI", "title", "subtitle", "author", "type", "published-print", "published-online",
    "issued", "container-title", "page", "volume", "issue",
])

# ---------------------------------------------------------------------------
# Journals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Journal:
    key: str
    name: str
    issns: tuple[str, ...]           # (print, electronic) — verified against /journals/{issn}
    venue_patterns: tuple[str, ...]  # regexes on normalize_venue(venue)
    venue_exclusions: tuple[str, ...] = ()


JOURNALS: dict[str, Journal] = {
    "aer": Journal(
        "aer", "American Economic Review", ("0002-8282", "1944-7981"),
        (r"\bamerican economic review\b", r"\baer\b"),
        (r"\binsights\b", r"\bpapers (and )?proceedings\b", r"\bpandp\b", r"\bp p\b",
         r"\baej\b", r"\bjournal of economic perspectives\b"),
    ),
    "ecta": Journal("ecta", "Econometrica", ("0012-9682", "1468-0262"), (r"\beconometrica\b",)),
    "jpe": Journal(
        "jpe", "Journal of Political Economy", ("0022-3808", "1537-534X"),
        (r"\bjournal of political economy\b", r"\bjpe\b"),
        (r"\beuropean journal of political economy\b", r"political economy (micro|macro)",
         r"\bjpe (micro|macro)"),
    ),
    "qje": Journal("qje", "Quarterly Journal of Economics", ("0033-5533", "1531-4650"),
                   (r"\bquarterly journal of economics\b", r"\bqje\b")),
    "restud": Journal("restud", "Review of Economic Studies", ("0034-6527", "1467-937X"),
                      (r"\breview of economics? studies\b", r"\brestud\b")),
}


def normalize_venue(venue: str | None) -> str:
    if not venue:
        return ""
    v = unicodedata.normalize("NFKD", venue).casefold()
    v = "".join(c for c in v if not unicodedata.combining(c))
    v = re.sub(r"[^a-z0-9]+", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def venue_journal(venue: str | None) -> str | None:
    """Return the key of the top-5 journal a free-text venue names, if any."""
    v = normalize_venue(venue)
    if not v:
        return None
    for j in JOURNALS.values():
        if any(re.search(p, v) for p in j.venue_patterns) and not any(re.search(x, v) for x in j.venue_exclusions):
            return j.key
    return None


# ---------------------------------------------------------------------------
# Crossref ground truth
# ---------------------------------------------------------------------------

# Rules applied to Crossref `journal-article` records, in this order.
EXCLUSION_RULES: list[tuple[str, re.Pattern]] = [
    ("erratum_or_retraction", re.compile(
        r"^(online )?(erratum|errata|corrigendum|corrigenda|correction|corrections|addendum|retraction)\b"
        r"|\b(erratum|corrigendum)\b|^retraction of\b")),
    ("comment_or_reply", re.compile(
        r"^(a )?(comments?|reply|replies|rejoinder)\b"
        r"|[:?.!]\s*(a )?(comments?|reply|rejoinder)\s*$"
        r"|[:?.!]\s*(a )?(comments?|reply|replies) (on|to)\b"
        r"|\b(comments?|reply|replies) (on|to)\s*:?\s*[“\"'‘]"
        r"|\brejoinder\b")),
    ("lecture_or_address", re.compile(
        r"\b(nobel|prize|memorial|fisher[- ]schultz|walras[- ]bowley|ely|hicks) lecture\b"
        r"|^[^:]{0,80}\blecture\s*:|\b(presidential|inaugural|keynote) address\b")),
    ("obituary", re.compile(r"\bobituar|\bin memoriam\b|^remembering\b")),
    ("front_matter_or_editorial", re.compile(
        r"^(front|back) ?matter\b|^(frontmatter|backmatter)\b|^back cover\b|^index to volume\b"
        r"|^(recent |acknowledg\w* of )?referees\b|^forthcoming papers\b|\bturnaround times?\b|^submission of manuscripts\b"
        r"|^report of\b|\bannual reports?\b|\breport of the (editor|secretary|treasurer|president)"
        r"|\belection of fellows\b|\bprize announcement\b|^announcements?\b|^editorial\b"
        r"|^editors?'? ?(note|introduction)\b|\bminutes of\b|^notes? (to|for) contributors\b")),
]


def clean_crossref_text(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    d = d.strip().rstrip(".,;)")
    return d if d.startswith("10.") and "/" in d else None


_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"'<>?#]+", re.IGNORECASE)


def doi_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _DOI_IN_TEXT.search(url)
    return normalize_doi(m.group(0)) if m else None


def exclusion_reason(item: dict) -> str | None:
    """Why a Crossref record is not a research article (None = keep)."""
    doi = normalize_doi(item.get("DOI")) or ""
    if doi.startswith("10.1257/pandp"):
        return "aea_papers_and_proceedings"
    if item.get("type") not in (None, "journal-article"):
        return "not_journal_article"
    if not item.get("author"):
        return "no_authors"  # front/back matter, referee lists, reports, announcements
    title = clean_crossref_text((item.get("title") or [""])[0]).casefold()
    if not title:
        return "no_title"
    for reason, pattern in EXCLUSION_RULES:
        if pattern.search(title):
            return reason
    return None


def _date_year(item: dict, key: str) -> int | None:
    parts = (item.get(key) or {}).get("date-parts") or []
    if parts and parts[0] and parts[0][0]:
        return int(parts[0][0])
    return None


def article_year(item: dict) -> int | None:
    """Issue (print) year; online year for online-first articles not yet in an issue."""
    return _date_year(item, "published-print") or _date_year(item, "published-online") or _date_year(item, "issued")


@dataclass
class Author:
    given: str
    family: str
    orcid: str | None = None
    openalex_id: str | None = None

    @property
    def display(self) -> str:
        return f"{self.given} {self.family}".strip()


@dataclass
class Article:
    doi: str
    journal: str
    year: int
    title: str
    subtitle: str
    authors: list[Author]
    volume: str | None = None
    issue: str | None = None

    @property
    def full_title(self) -> str:
        return f"{self.title}: {self.subtitle}" if self.subtitle else self.title


def parse_crossref_item(item: dict, journal: str) -> Article | None:
    doi = normalize_doi(item.get("DOI"))
    year = article_year(item)
    if not doi or not year:
        return None
    authors = []
    for a in item.get("author") or []:
        given, family = a.get("given") or "", a.get("family") or ""
        if not family and a.get("name"):
            family = a["name"]
        orcid = (a.get("ORCID") or "").rsplit("/", 1)[-1] or None
        authors.append(Author(given=given, family=family, orcid=orcid))
    subtitle = clean_crossref_text((item.get("subtitle") or [""])[0])
    return Article(
        doi=doi, journal=journal, year=year,
        title=clean_crossref_text((item.get("title") or [""])[0]),
        subtitle=subtitle, authors=authors,
        volume=item.get("volume"), issue=item.get("issue"),
    )


def _http_get_json(session, url: str, params: dict, *, retries: int = 5) -> dict:
    for attempt in range(retries):
        resp = session.get(url, params=params, timeout=60)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = (resp.headers or {}).get("Retry-After") if hasattr(resp, "headers") else None
            wait = min(120, int(retry_after)) if retry_after and str(retry_after).isdigit() else min(60, 2 ** attempt * 2)
            log.warning("HTTP %s from %s, retrying in %ss: %s", resp.status_code, url, wait,
                        getattr(resp, "text", "")[:300].replace("\n", " "))
            time.sleep(wait)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"Giving up on {url} after {retries} attempts")


def _make_session():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def fetch_crossref_works(issn: str, from_date: str, until_date: str, cache_dir: Path, *,
                         session=None, refresh: bool = False, sleep: float = 1.0) -> list[dict]:
    """All journal-article records for one ISSN, cursor-paged and cached as one file."""
    cache_file = cache_dir / "crossref" / f"{issn}_{from_date}_{until_date}.json.gz"
    if cache_file.exists() and not refresh:
        with gzip.open(cache_file, "rt", encoding="utf-8") as f:
            return json.load(f)
    session = session or _make_session()
    items: list[dict] = []
    cursor = "*"
    while True:
        params = {
            "filter": f"from-pub-date:{from_date},until-pub-date:{until_date},type:journal-article",
            "rows": 500, "cursor": cursor, "select": CROSSREF_SELECT,
        }
        msg = _http_get_json(session, f"{CROSSREF_API}/journals/{issn}/works", params)["message"]
        page = msg.get("items") or []
        items.extend(page)
        cursor = msg.get("next-cursor")
        log.info("Crossref %s: %d/%s", issn, len(items), msg.get("total-results"))
        if not page or not cursor or len(items) >= msg.get("total-results", 0):
            break
        time.sleep(sleep)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache_file, "wt", encoding="utf-8") as f:
        json.dump(items, f)
    time.sleep(sleep)
    return items


@dataclass
class GroundTruth:
    articles: list[Article]
    excluded: list[dict]  # {journal, year, doi, title, reason}


def load_ground_truth(journals: list[str], from_year: int, until_year: int, cache_dir: Path, *,
                      session=None, refresh: bool = False, sleep: float = 1.0,
                      today: date | None = None) -> GroundTruth:
    today = today or date.today()
    # Online-first articles can precede their issue by over a year, so fetch two years
    # earlier and assign years by issue date afterwards.
    from_date = f"{from_year - 2}-01-01"
    until_date = min(date(until_year, 12, 31), today).isoformat()
    articles: dict[str, Article] = {}
    excluded: dict[str, dict] = {}
    for key in journals:
        journal = JOURNALS[key]
        for issn in journal.issns:
            for item in fetch_crossref_works(issn, from_date, until_date, cache_dir,
                                             session=session, refresh=refresh, sleep=sleep):
                year = article_year(item)
                doi = normalize_doi(item.get("DOI"))
                if not doi or year is None or not (from_year <= year <= until_year):
                    continue
                if doi in articles or doi in excluded:
                    continue
                reason = exclusion_reason(item)
                if reason:
                    excluded[doi] = {"journal": key, "year": year, "doi": doi, "reason": reason,
                                     "title": clean_crossref_text((item.get("title") or [""])[0])}
                    continue
                art = parse_crossref_item(item, key)
                if art:
                    articles[doi] = art
    return GroundTruth(sorted(articles.values(), key=lambda a: (a.journal, a.year, a.doi)),
                       list(excluded.values()))


def attach_openalex_authors(articles: list[Article], cache_dir: Path, *, session=None,
                            refresh: bool = False, sleep: float = 1.0, api_key: str | None = None) -> int:
    """Add OpenAlex author IDs to Crossref authors (batched DOI lookups, cached). Returns #works found."""
    cache_file = cache_dir / "openalex_authorships.json.gz"
    cache: dict[str, list] = {}
    if cache_file.exists() and not refresh:
        with gzip.open(cache_file, "rt", encoding="utf-8") as f:
            cache = json.load(f)
    missing = [a.doi for a in articles if a.doi not in cache]
    if missing:
        session = session or _make_session()
        for i in range(0, len(missing), 50):
            batch = missing[i:i + 50]
            params = {"filter": "doi:" + "|".join(batch), "per-page": 50, "select": "id,doi,authorships"}
            if api_key:
                params["api_key"] = api_key
            try:
                data = _http_get_json(session, f"{OPENALEX_API}/works", params)
            except Exception as exc:  # name matching still works without OpenAlex IDs
                log.warning("OpenAlex lookup stopped after %d DOIs (%s); continuing with what is cached", i, exc)
                break
            for doi in batch:
                cache.setdefault(doi, [])
            for work in data.get("results") or []:
                d = normalize_doi(work.get("doi"))
                if d:
                    cache[d] = [
                        {"name": (au.get("author") or {}).get("display_name"),
                         "id": ((au.get("author") or {}).get("id") or "").rsplit("/", 1)[-1] or None,
                         "orcid": ((au.get("author") or {}).get("orcid") or "").rsplit("/", 1)[-1] or None}
                        for au in work.get("authorships") or []
                    ]
            log.info("OpenAlex: %d/%d DOIs", min(i + 50, len(missing)), len(missing))
            time.sleep(sleep)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(cache_file, "wt", encoding="utf-8") as f:
            json.dump(cache, f)
    found = 0
    for art in articles:
        oa = cache.get(art.doi) or []
        if oa:
            found += 1
        for author in art.authors:
            for entry in oa:
                if entry.get("id") and _surnames_compatible(author.family, entry.get("name") or "", loose=True):
                    author.openalex_id = entry["id"]
                    author.orcid = author.orcid or entry.get("orcid")
                    break
    return found


# ---------------------------------------------------------------------------
# Our data (read-only)
# ---------------------------------------------------------------------------

QUERIES = {
    "papers": """SELECT JSON_OBJECT('id', id, 'title', title, 'year', year, 'venue', venue,
                     'status', status, 'doi', doi) FROM papers WHERE title IS NOT NULL""",
    "links": """SELECT JSON_OBJECT('paper_id', paper_id, 'doi', doi, 'url', url) FROM paper_links
                WHERE doi IS NOT NULL OR url LIKE '%%10.%%'""",
    "authorship": "SELECT JSON_OBJECT('r', researcher_id, 'p', publication_id) FROM authorship",
    "researchers": """SELECT JSON_OBJECT('id', r.id, 'first', r.first_name, 'last', r.last_name,
                         'oa', r.openalex_author_id, 'n_urls', COALESCE(u.n, 0),
                         'n_active', COALESCE(u.a, 0), 'n_html', COALESCE(h.n, 0))
                      FROM researchers r
                      LEFT JOIN (SELECT researcher_id, COUNT(*) n, SUM(is_active) a
                                 FROM researcher_urls GROUP BY researcher_id) u ON u.researcher_id = r.id
                      LEFT JOIN (SELECT u2.researcher_id, COUNT(*) n FROM html_content h2
                                 JOIN researcher_urls u2 ON u2.id = h2.url_id
                                 WHERE h2.content IS NOT NULL GROUP BY u2.researcher_id) h
                             ON h.researcher_id = r.id""",
}

HTML_QUERY = """SELECT JSON_OBJECT('researcher_id', u.researcher_id, 'url_id', u.id, 'url', u.url,
                   'page_type', u.page_type, 'is_active', u.is_active,
                   'deactivation_reason', u.deactivation_reason,
                   'pending', (h.extracted_hash IS NULL OR h.extracted_hash <> h.content_hash),
                   'fetched_at', h.timestamp, 'extracted_at', h.extracted_at,
                   'content', h.content, 'raw_html', h.raw_html)
                FROM researcher_urls u LEFT JOIN html_content h ON h.url_id = u.id
                WHERE u.researcher_id IN ({ids})"""

Runner = Callable[[str], list[dict]]


def ssh_runner(host: str, key: str | None, container: str) -> Runner:
    """Run one SELECT inside a READ ONLY transaction in the prod MySQL container."""
    remote = (
        f"docker exec -i {container} sh -c "
        "'MYSQL_PWD=\"$MYSQL_PASSWORD\" mysql -u\"$MYSQL_USER\" \"$MYSQL_DATABASE\" "
        "--batch --raw --skip-column-names --default-character-set=utf8mb4' | gzip -c"
    )
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    if key:
        cmd += ["-i", os.path.expanduser(key)]
    cmd += [host, remote]

    def run(sql: str) -> list[dict]:
        if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
            raise ValueError("Only SELECT statements are allowed")
        script = ("SET SESSION TRANSACTION READ ONLY;\nSTART TRANSACTION READ ONLY;\n"
                  + sql.replace("%%", "%") + ";\nROLLBACK;\n")
        proc = subprocess.run(cmd, input=script.encode("utf-8"), capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ssh query failed: {proc.stderr.decode(errors='replace')[:500]}")
        text = gzip.decompress(proc.stdout).decode("utf-8")
        # split("\n"), not splitlines(): titles can contain U+2028 and other Unicode line breaks.
        return [json.loads(line) for line in text.split("\n") if line.strip()]

    return run


def local_runner() -> Runner:
    if not _LOCAL_DB_CONFIGURED:
        raise SystemExit("--source local needs DB_HOST/DB_USER/DB_PASSWORD/DB_NAME (e.g. in .env)")
    from backend.database.connection import get_connection

    def run(sql: str) -> list[dict]:
        if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
            raise ValueError("Only SELECT statements are allowed")
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("START TRANSACTION READ ONLY")
            try:
                cur.execute(sql.replace("%%", "%"))
                rows = [json.loads(r[0]) for r in cur.fetchall()]
            finally:
                conn.rollback()
                cur.execute("SET SESSION TRANSACTION READ WRITE")
                cur.close()
        return rows

    return run


def cached_query(runner: Runner, name: str, sql: str, cache_dir: Path, refresh: bool) -> list[dict]:
    path = cache_dir / f"{name}.jsonl.gz"
    if path.exists() and not refresh:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    t0 = time.time()
    rows = runner(sql)
    log.info("Exported %s: %d rows in %.0fs", name, len(rows), time.time() - t0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

_PARTICLES = {"de", "la", "le", "del", "della", "der", "den", "van", "von", "da", "di", "du",
              "dos", "das", "do", "y", "e", "al", "el", "ten", "ter", "st", "mac"}
_TRANSLIT = str.maketrans({"ß": "ss", "ø": "o", "æ": "ae", "œ": "oe", "đ": "d", "ł": "l", "ı": "i", "þ": "th"})


def norm_name(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).casefold().translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def surname_core(s: str | None) -> list[str]:
    toks = norm_name(s).split()
    core = [t for t in toks if t not in _PARTICLES]
    return core or toks


def _surnames_compatible(a: str, b: str, *, loose: bool = False) -> bool:
    """Same surname modulo accents, particles, hyphens and Spanish/compound double surnames.

    loose=True compares against any token of b (b may be a full display name)."""
    ca, cb = surname_core(a), surname_core(b)
    if not ca or not cb:
        return False
    if loose:
        return bool(set(ca) & set(cb)) or "".join(ca) in "".join(cb)
    if ca == cb or "".join(norm_name(a).split()) == "".join(norm_name(b).split()):
        return True
    sa, sb = set(ca), set(cb)
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return short < long_ and any(len(t) >= 3 for t in short)


def _given_variants(given: str | None) -> list[str]:
    toks = norm_name(given).split()
    if not toks:
        return []
    out = [toks[0]]
    if len(toks[0]) == 1 and len(toks) > 1:  # "J. Michael Smith" -> also "michael"
        out.append(toks[1])
    return out


def _given_compatible(a: str | None, b: str | None) -> tuple[bool, bool]:
    """(compatible, initial_only)."""
    va, vb = _given_variants(a), _given_variants(b)
    if not va or not vb:
        return False, False
    for x in va:
        for y in vb:
            if x == y and len(x) > 1:
                return True, False
    for x in va:
        for y in vb:
            if (len(x) == 1 or len(y) == 1) and x[0] == y[0]:
                return True, True
    return False, False


@dataclass
class Researcher:
    id: int
    first: str
    last: str
    openalex_id: str | None = None
    n_urls: int = 0
    n_active: int = 0
    n_html: int = 0

    @property
    def tracked(self) -> bool:
        return self.n_urls > 0

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}".strip()


@dataclass
class AuthorMatch:
    researcher_id: int
    method: str  # openalex | name | name_initial | authorship
    tracked: bool


class NameIndex:
    def __init__(self, researchers: Iterable[Researcher]):
        self.researchers: dict[int, Researcher] = {}
        self.by_token: dict[str, list[int]] = defaultdict(list)
        self.by_openalex: dict[str, list[int]] = defaultdict(list)
        for r in researchers:
            self.researchers[r.id] = r
            for tok in set(surname_core(r.last)) | {"".join(norm_name(r.last).split())}:
                if tok:
                    self.by_token[tok].append(r.id)
            if r.openalex_id:
                self.by_openalex[r.openalex_id.rsplit("/", 1)[-1].upper()].append(r.id)

    def match_author(self, author: Author) -> list[AuthorMatch]:
        out: dict[int, AuthorMatch] = {}
        if author.openalex_id:
            for rid in self.by_openalex.get(author.openalex_id.upper(), []):
                r = self.researchers[rid]
                if _surnames_compatible(author.family, f"{r.first} {r.last}", loose=True):
                    out[rid] = AuthorMatch(rid, "openalex", r.tracked)
        candidates: set[int] = set()
        for tok in set(surname_core(author.family)) | {"".join(norm_name(author.family).split())}:
            candidates.update(self.by_token.get(tok, ()))
        full, initial = [], []
        for rid in candidates:
            if rid in out:
                continue
            r = self.researchers[rid]
            if not _surnames_compatible(author.family, r.last):
                continue
            ok, initial_only = _given_compatible(author.given, r.first)
            if ok:
                (initial if initial_only else full).append(r)
        for r in full:
            out[r.id] = AuthorMatch(r.id, "name", r.tracked)
        # An initial-only match is accepted only when it is unambiguous among tracked researchers.
        tracked_initial = [r for r in initial if r.tracked]
        if not full and len(tracked_initial) == 1:
            r = tracked_initial[0]
            out[r.id] = AuthorMatch(r.id, "name_initial", True)
        for r in initial:
            if not r.tracked and r.id not in out:
                out[r.id] = AuthorMatch(r.id, "name_initial", False)
        return list(out.values())


# ---------------------------------------------------------------------------
# Papers and matching
# ---------------------------------------------------------------------------

_STOP = {"the", "and", "for", "from", "with", "evidence", "theory", "model", "models", "economic",
         "economics", "effects", "effect", "market", "markets", "into", "under", "between",
         "their", "does", "what", "when", "how", "why", "about", "over", "data", "policy"}


def _title_tokens(norm: str) -> set[str]:
    return {t for t in norm.split() if len(t) >= 4 and t not in _STOP}


@dataclass
class MatchResult:
    tier: str | None = None                   # doi | title | fuzzy
    paper_ids: list[int] = field(default_factory=list)  # best first
    by_tier: dict[str, list[int]] = field(default_factory=dict)
    fuzzy_scores: dict[int, float] = field(default_factory=dict)


class PaperIndex:
    FUZZY_STRICT = 0.93      # accepted on title alone
    FUZZY_WITH_AUTHOR = 0.85  # accepted when a Crossref author's surname is on the paper
    MIN_FUZZY_CHARS = 25

    def __init__(self, papers: Iterable[dict], links: Iterable[dict], authorship: Iterable[dict],
                 names: NameIndex):
        self.papers: dict[int, dict] = {}
        self.norm: dict[int, str] = {}
        self.by_doi: dict[str, set[int]] = defaultdict(set)
        self.by_title: dict[str, set[int]] = defaultdict(set)
        self.postings: dict[str, list[int]] = defaultdict(list)
        self.authors_of: dict[int, set[int]] = defaultdict(set)
        self.papers_of: dict[int, set[int]] = defaultdict(set)
        self.linked_dois: dict[int, set[str]] = defaultdict(set)
        self.names = names
        for p in papers:
            pid = int(p["id"])
            self.papers[pid] = p
            n = normalize_title(p.get("title"))
            self.norm[pid] = n
            if n:
                self.by_title[n].add(pid)
                for tok in _title_tokens(n):
                    self.postings[tok].append(pid)
            d = normalize_doi(p.get("doi"))
            if d:
                self.by_doi[d].add(pid)
                self.linked_dois[pid].add(d)
        for link in links:
            pid = int(link["paper_id"])
            d = normalize_doi(link.get("doi")) or doi_from_url(link.get("url"))
            if d and pid in self.papers:
                self.by_doi[d].add(pid)
                self.linked_dois[pid].add(d)
        for a in authorship:
            rid, pid = int(a["r"]), int(a["p"])
            self.authors_of[pid].add(rid)
            self.papers_of[rid].add(pid)

    def _surname_overlap(self, pid: int, article: Article) -> bool:
        surnames = [a.family for a in article.authors if a.family]
        for rid in self.authors_of.get(pid, ()):
            r = self.names.researchers.get(rid)
            if r and any(_surnames_compatible(s, r.last) for s in surnames):
                return True
        return False

    MIN_PREFIX_WORDS = 3
    MIN_PREFIX_CHARS = 15

    def _fuzzy(self, article: Article, exclude: set[int]) -> dict[int, float]:
        """Conservative fuzzy tier.

        Accepts (i) SequenceMatcher ratio >= FUZZY_STRICT, (ii) ratio >= FUZZY_WITH_AUTHOR with a
        shared author surname, or (iii) one title equal to the other minus its subtitle
        ("Robots and Jobs" vs "Robots and Jobs: Evidence from US Labor Markets"), with >= 3 words
        and a shared author surname. Ratios are only used for titles of >= MIN_FUZZY_CHARS chars.
        """
        targets = {normalize_title(article.full_title), normalize_title(article.title)} - {""}
        accepted: dict[int, float] = {}
        for target in targets:
            toks = sorted(_title_tokens(target), key=lambda t: len(self.postings.get(t, ())))
            toks = [t for t in toks if 0 < len(self.postings.get(t, ())) <= 20000][:3]
            candidates: set[int] = set()
            for t in toks:
                candidates.update(self.postings[t])
            for pid in candidates - exclude:
                cand = self.norm[pid]
                score, ok = 0.0, False
                if len(target) >= self.MIN_FUZZY_CHARS and len(cand) >= self.MIN_FUZZY_CHARS:
                    sm = SequenceMatcher(None, target, cand, autojunk=False)
                    if sm.real_quick_ratio() >= self.FUZZY_WITH_AUTHOR and sm.quick_ratio() >= self.FUZZY_WITH_AUTHOR:
                        score = sm.ratio()
                    ok = score >= self.FUZZY_STRICT
                    if not ok and score >= self.FUZZY_WITH_AUTHOR:
                        ok = self._surname_overlap(pid, article)
                if not ok:
                    short, long_ = sorted((target, cand), key=len)
                    if (len(short.split()) >= self.MIN_PREFIX_WORDS and len(short) >= self.MIN_PREFIX_CHARS
                            and long_.startswith(short + " ")):
                        ok = self._surname_overlap(pid, article)
                        score = max(score, 0.80)
                if ok:
                    accepted[pid] = max(score, accepted.get(pid, 0.0))
        return accepted

    def match(self, article: Article) -> MatchResult:
        res = MatchResult()
        doi_ids = sorted(self.by_doi.get(article.doi, ()))
        title_ids: set[int] = set()
        for t in (article.full_title, article.title):
            n = normalize_title(t)
            if n:
                title_ids |= self.by_title.get(n, set())
        title_ids_sorted = sorted(title_ids - set(doi_ids))
        found = set(doi_ids) | title_ids
        fuzzy = self._fuzzy(article, found)
        res.fuzzy_scores = fuzzy
        res.by_tier = {"doi": doi_ids, "title": title_ids_sorted,
                       "fuzzy": sorted(fuzzy, key=lambda p: -fuzzy[p])}
        for tier in ("doi", "title", "fuzzy"):
            if res.by_tier[tier]:
                res.tier = res.tier or tier
                res.paper_ids.extend(res.by_tier[tier])
        return res


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

GOOD_STATUSES = {"published", "accepted"}
B_ORDER = ["b1_in_page_text", "b2_in_raw_html_only", "b3_possible_renamed",
           "b4_not_on_tracked_pages", "b5_no_stored_html"]


@dataclass
class ArticleResult:
    article: Article
    match: MatchResult
    author_matches: list[list[AuthorMatch]]
    observed: bool
    duplicate: bool
    tracked_ids: list[int]
    known_untracked_ids: list[int]
    record: str | None = None        # correct | c_wrong_status | c_wrong_venue | c_missing_venue
    doi_linked: bool = False
    best_paper: dict | None = None
    category: str = ""               # observed_correct | c | a1 | a2 | b...
    detail: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def tracked(self) -> bool:
        return bool(self.tracked_ids)


# Recognisable spellings of the right journal that the site's top-5 venue filter does not match:
# abbreviations, misspellings, and words split by stray spaces ("Journal of Polit ical Economy").
_VENUE_VARIANTS = {
    "aer": (r"^aer$", r"\bamerican economics? review\b"),
    "ecta": (r"\becma\b", r"\becta\b"),
    "jpe": (r"\bj (of )?political economy\b",),
    "qje": (r"\bquarterly journal of economics?\b",),
    "restud": (r"^res$", r"\breview of econ\w* studies\b"),
}
_JOURNAL_SQUASHED = {
    "aer": "americaneconomicreview", "ecta": "econometrica", "jpe": "journalofpoliticaleconomy",
    "qje": "quarterlyjournalofeconomics", "restud": "reviewofeconomicstudies",
}


def venue_variant_of(venue: str | None, journal: str) -> bool:
    v = normalize_venue(venue)
    if not v:
        return False
    if any(re.search(p, v) for p in _VENUE_VARIANTS.get(journal, ())):
        return True
    squashed = v.replace(" ", "")
    return _JOURNAL_SQUASHED[journal] in squashed and not any(
        re.search(x, v) for x in JOURNALS[journal].venue_exclusions)


def _record_quality(paper: dict, journal: str) -> str:
    vj = venue_journal(paper.get("venue"))
    status = paper.get("status")
    if vj and vj != journal:
        return "c_wrong_venue"
    if status not in GOOD_STATUSES:
        return "c_wrong_status"
    if not vj:
        if not normalize_venue(paper.get("venue")):
            return "c_missing_venue"
        return "c_venue_variant" if venue_variant_of(paper.get("venue"), journal) else "c_wrong_venue"
    return "correct"


def classify_article(article: Article, index: PaperIndex, names: NameIndex) -> ArticleResult:
    m = index.match(article)
    author_matches = [names.match_author(a) for a in article.authors]
    tracked = {am.researcher_id for ams in author_matches for am in ams if am.tracked}
    known = {am.researcher_id for ams in author_matches for am in ams if not am.tracked}
    # A tracked co-author linked to the matched paper whose surname matches a Crossref
    # author counts as tracked even when the given names differ (nicknames, middle names).
    for pid in m.paper_ids:
        for rid in index.authors_of.get(pid, ()):
            r = names.researchers.get(rid)
            if r and r.tracked and rid not in tracked and any(
                    _surnames_compatible(a.family, r.last) for a in article.authors):
                tracked.add(rid)
    res = ArticleResult(
        article=article, match=m, author_matches=author_matches, observed=bool(m.paper_ids),
        duplicate=len(set(m.paper_ids)) > 1, tracked_ids=sorted(tracked),
        known_untracked_ids=sorted(known - tracked),
    )
    if res.observed:
        qualities = {pid: _record_quality(index.papers[pid], article.journal) for pid in m.paper_ids}
        good = [pid for pid in m.paper_ids if qualities[pid] == "correct"]
        best = good[0] if good else m.paper_ids[0]
        res.best_paper = index.papers[best]
        res.record = qualities[best]
        res.doi_linked = any(article.doi in index.linked_dois.get(pid, ()) for pid in m.paper_ids)
        res.category = "observed_correct" if res.record == "correct" else "c"
        res.detail = res.record if res.record != "correct" else ("doi_linked" if res.doi_linked else "c_doi_unlinked")
    elif res.tracked:
        res.category = "b"
        res.detail = "b_pending_html_check"
    else:
        res.category = "a"
        res.detail = "a1_author_known_untracked" if res.known_untracked_ids else "a2_no_known_author"
    return res


def _page_contains(norm_titles: list[str], norm_text: str) -> bool:
    padded = f" {norm_text} "
    return any(t and f" {t} " in padded for t in norm_titles)


def diagnose_b(res: ArticleResult, pages_by_researcher: dict[int, list[dict]], index: PaperIndex,
               names: NameIndex, content_max_chars: int = 20000) -> None:
    """Refine a b-miss using the tracked researchers' stored pages and existing papers."""
    art = res.article
    titles = [normalize_title(art.full_title), normalize_title(art.title)]
    main = normalize_title(re.split(r"[:?]", art.title)[0])
    if len(main.split()) >= 4 and main not in titles:
        titles.append(main)
    best_rank, best_ev = len(B_ORDER), {}
    for rid in res.tracked_ids:
        r = names.researchers.get(rid)
        pages = [p for p in pages_by_researcher.get(rid, []) if p.get("content") or p.get("raw_html")]
        ev = {"researcher_id": rid, "researcher": r.name if r else str(rid),
              "n_urls": len(pages_by_researcher.get(rid, [])), "n_pages_stored": len(pages),
              "n_active": sum(1 for p in pages_by_researcher.get(rid, []) if p.get("is_active"))}
        reasons = sorted({p.get("deactivation_reason") or "unknown" for p in pages_by_researcher.get(rid, [])
                          if not p.get("is_active")})
        if reasons:
            ev["deactivation_reasons"] = reasons
        detail = "b5_no_stored_html"
        if pages:
            detail = "b4_not_on_tracked_pages"
            for p in pages:
                content = p.get("content") or ""
                if content and _page_contains(titles, normalize_title(content)):
                    detail = "b1_in_page_text"
                    ev.update(url=p.get("url"), pending_extraction=bool(p.get("pending")),
                              page_active=bool(p.get("is_active")))
                    break
                if p.get("raw_html") and _page_contains(titles, _normalize_html_for_matching(p["raw_html"])):
                    if detail != "b2_in_raw_html_only":
                        detail = "b2_in_raw_html_only"
                        ev.update(url=p.get("url"), text_len=len(content),
                                  truncated=len(content) >= content_max_chars - 10,
                                  pending_extraction=bool(p.get("pending")),
                                  page_active=bool(p.get("is_active")))
        if detail in ("b4_not_on_tracked_pages", "b5_no_stored_html"):
            # The researcher may have the paper under its working-paper title.
            best_pid, best_score = None, 0.0
            target = titles[0]
            ttoks = _title_tokens(target)
            for pid in index.papers_of.get(rid, ()):
                cand = index.norm.get(pid, "")
                ctoks = _title_tokens(cand)
                if not ttoks or not ctoks:
                    continue
                jacc = len(ttoks & ctoks) / len(ttoks | ctoks)
                score = max(jacc, SequenceMatcher(None, target, cand).ratio() if jacc > 0.2 else 0.0)
                if score > best_score:
                    best_pid, best_score = pid, score
            if best_pid is not None and best_score >= 0.6:
                detail = "b3_possible_renamed"
                p = index.papers[best_pid]
                ev.update(similar_paper_id=best_pid, similar_title=p.get("title"),
                          similar_status=p.get("status"), similar_venue=p.get("venue"),
                          similarity=round(best_score, 2))
        if detail == "b4_not_on_tracked_pages" and not ev["n_active"]:
            ev["all_urls_inactive"] = True
        rank = B_ORDER.index(detail)
        if rank < best_rank:
            best_rank, best_ev = rank, ev
    res.detail = B_ORDER[best_rank] if best_rank < len(B_ORDER) else "b5_no_stored_html"
    res.evidence = best_ev


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def summarize(results: list[ArticleResult], excluded: list[dict]) -> dict:
    def block(rs: list[ArticleResult]) -> dict:
        n = len(rs)
        obs = [r for r in rs if r.observed]
        tr = [r for r in rs if r.tracked]
        tr_obs = [r for r in tr if r.observed]
        return {
            "articles": n,
            "observed": len(obs),
            "coverage_pct": _pct(len(obs), n),
            "tracked_articles": len(tr),
            "tracked_share_pct": _pct(len(tr), n),
            "tracked_observed": len(tr_obs),
            "coverage_tracked_pct": _pct(len(tr_obs), len(tr)),
            "observed_correct": sum(1 for r in obs if r.category == "observed_correct"),
            "observed_correct_and_doi": sum(1 for r in obs if r.category == "observed_correct" and r.doi_linked),
            "tier_doi": sum(1 for r in obs if r.match.tier == "doi"),
            "tier_title": sum(1 for r in obs if r.match.tier == "title"),
            "tier_fuzzy": sum(1 for r in obs if r.match.tier == "fuzzy"),
            "a_no_tracked_author": sum(1 for r in rs if r.category == "a"),
            "b_tracked_absent": sum(1 for r in rs if r.category == "b"),
            "c_wrong_record": sum(1 for r in rs if r.category == "c"),
            "c_doi_unlinked": sum(1 for r in obs if r.category == "observed_correct" and not r.doi_linked),
            "d_duplicates": sum(1 for r in rs if r.duplicate),
            "observed_untracked": sum(1 for r in obs if not r.tracked),
        }

    by_journal = defaultdict(list)
    by_year = defaultdict(list)
    by_jy = defaultdict(list)
    for r in results:
        by_journal[r.article.journal].append(r)
        by_year[r.article.year].append(r)
        by_jy[(r.article.journal, r.article.year)].append(r)
    details = Counter(r.detail for r in results if not r.observed or r.category == "c")
    tracked_details = Counter(r.detail for r in results if r.tracked and (not r.observed or r.category == "c"))
    return {
        "overall": block(results),
        "by_journal": {k: block(v) for k, v in sorted(by_journal.items())},
        "by_year": {str(k): block(v) for k, v in sorted(by_year.items())},
        "by_journal_year": {f"{j}:{y}": block(v) for (j, y), v in sorted(by_jy.items())},
        "miss_details": dict(details.most_common()),
        "miss_details_tracked": dict(tracked_details.most_common()),
        "b_pending_extraction": sum(1 for r in results if r.detail == "b1_in_page_text"
                                    and r.evidence.get("pending_extraction")),
        "b2_truncated": sum(1 for r in results if r.detail == "b2_in_raw_html_only" and r.evidence.get("truncated")),
        "match_methods": dict(Counter(am.method for r in results for ams in r.author_matches
                                      for am in ams if am.tracked).most_common()),
        "excluded": dict(Counter(e["reason"] for e in excluded).most_common()),
        "excluded_by_journal": {j: dict(Counter(e["reason"] for e in excluded if e["journal"] == j))
                                for j in sorted({e["journal"] for e in excluded})},
    }


def article_row(r: ArticleResult, names: NameIndex) -> dict:
    a = r.article
    bp = r.best_paper or {}
    return {
        "journal": a.journal, "year": a.year, "doi": a.doi, "title": a.full_title,
        "authors": "; ".join(x.display for x in a.authors),
        "observed": int(r.observed), "match_tier": r.match.tier or "",
        "n_matched_papers": len(set(r.match.paper_ids)),
        "paper_ids": " ".join(map(str, r.match.paper_ids)),
        "fuzzy_score": max(r.match.fuzzy_scores.values()) if r.match.fuzzy_scores else "",
        "category": r.category, "detail": r.detail, "duplicate": int(r.duplicate),
        "doi_linked": int(r.doi_linked),
        "our_title": bp.get("title") or "", "our_status": bp.get("status") or "",
        "our_venue": bp.get("venue") or "",
        "tracked_researchers": "; ".join(f"{names.researchers[i].name} [{i}]" for i in r.tracked_ids
                                         if i in names.researchers),
        "evidence": json.dumps(r.evidence, ensure_ascii=False) if r.evidence else "",
    }


def render_markdown(summary: dict, args: argparse.Namespace) -> str:
    cols = [("articles", "N"), ("observed", "Obs"), ("coverage_pct", "Cov %"),
            ("tracked_articles", "Tracked"), ("coverage_tracked_pct", "Cov|tracked %"),
            ("observed_correct", "Correct rec."), ("tier_doi", "DOI"), ("tier_title", "Title"),
            ("tier_fuzzy", "Fuzzy"), ("b_tracked_absent", "b"), ("c_wrong_record", "c"),
            ("d_duplicates", "d")]

    def table(rows: dict[str, dict], label: str) -> str:
        head = f"| {label} | " + " | ".join(c[1] for c in cols) + " |"
        sep = "|" + "---|" * (len(cols) + 1)
        body = [f"| {k} | " + " | ".join("" if v.get(c[0]) is None else str(v.get(c[0])) for c in cols) + " |"
                for k, v in rows.items()]
        return "\n".join([head, sep, *body])

    lines = [
        f"# Top-5 coverage validation ({args.from_year}–{args.until_year})",
        "",
        table({"all": summary["overall"]}, "Scope"), "",
        "## By journal", table(summary["by_journal"], "Journal"), "",
        "## By year", table(summary["by_year"], "Year"), "",
        "## Miss / record-problem details",
        *[f"- {k}: {v}" for k, v in summary["miss_details"].items()], "",
        "## Excluded Crossref records",
        *[f"- {k}: {v}" for k, v in summary["excluded"].items()],
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from-year", type=int, default=2020)
    p.add_argument("--until-year", type=int, default=date.today().year)
    p.add_argument("--journals", default=",".join(JOURNALS), help=f"comma list of {','.join(JOURNALS)}")
    p.add_argument("--source", choices=["ssh", "local"], default="ssh")
    p.add_argument("--ssh-host", default="root@167.233.132.217")
    p.add_argument("--ssh-key", default="~/.ssh/hetzner")
    p.add_argument("--db-container", default="econ-newsfeed-db-1")
    p.add_argument("--cache-dir", type=Path, default=REPO_ROOT / ".cache" / "top5_coverage")
    p.add_argument("--out", type=Path, default=None, help="output dir (default: <cache-dir>/out)")
    p.add_argument("--refresh-crossref", action="store_true")
    p.add_argument("--refresh-db", action="store_true", help="re-export today's DB snapshot")
    p.add_argument("--no-openalex", action="store_true", help="skip OpenAlex author-ID lookup")
    p.add_argument("--use-openalex-key", action="store_true",
                   help="send OPENALEX_API_KEY (spends the enrichment budget); default: anonymous")
    p.add_argument("--no-html", action="store_true", help="skip stored-HTML diagnosis of b-misses")
    p.add_argument("--sleep", type=float, default=1.0, help="seconds between API requests")
    p.add_argument("--content-max-chars", type=int, default=int(os.environ.get("CONTENT_MAX_CHARS", 20000)))
    args = p.parse_args(argv)
    args.journals = [j.strip() for j in args.journals.split(",") if j.strip()]
    unknown = [j for j in args.journals if j not in JOURNALS]
    if unknown:
        p.error(f"unknown journals: {unknown}")
    return args


def main(argv: list[str] | None = None) -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args(argv)
    cache = args.cache_dir
    out = args.out or cache / "out"
    out.mkdir(parents=True, exist_ok=True)

    gt = load_ground_truth(args.journals, args.from_year, args.until_year, cache,
                           refresh=args.refresh_crossref, sleep=args.sleep)
    log.info("Crossref: %d research articles, %d excluded", len(gt.articles), len(gt.excluded))
    if not args.no_openalex:
        # The project's OPENALEX_API_KEY (picked up from .env by backend.config) funds enrichment;
        # this audit uses the anonymous pool (~60 requests) unless told otherwise.
        api_key = os.environ.get("OPENALEX_API_KEY") if args.use_openalex_key else None
        found = attach_openalex_authors(gt.articles, cache, sleep=args.sleep, api_key=api_key)
        log.info("OpenAlex: %d/%d works found", found, len(gt.articles))

    runner = ssh_runner(args.ssh_host, args.ssh_key, args.db_container) if args.source == "ssh" else local_runner()
    db_cache = cache / f"db_{args.source}_{date.today().isoformat()}"
    rows = {name: cached_query(runner, name, sql, db_cache, args.refresh_db) for name, sql in QUERIES.items()}
    researchers = [Researcher(int(r["id"]), r.get("first") or "", r.get("last") or "", r.get("oa"),
                              int(r.get("n_urls") or 0), int(r.get("n_active") or 0), int(r.get("n_html") or 0))
                   for r in rows["researchers"]]
    names = NameIndex(researchers)
    index = PaperIndex(rows["papers"], rows["links"], rows["authorship"], names)
    log.info("Indexed %d papers, %d researchers", len(index.papers), len(names.researchers))

    results = [classify_article(a, index, names) for a in gt.articles]
    b_results = [r for r in results if r.category == "b"]
    if b_results and not args.no_html:
        rids = sorted({rid for r in b_results for rid in r.tracked_ids})
        pages: dict[int, list[dict]] = defaultdict(list)
        for i in range(0, len(rids), 150):
            chunk = rids[i:i + 150]
            key = hashlib.sha1(",".join(map(str, chunk)).encode()).hexdigest()[:12]
            for p in cached_query(runner, f"html_{key}", HTML_QUERY.format(ids=",".join(map(str, chunk))),
                                  db_cache, args.refresh_db):
                pages[int(p["researcher_id"])].append(p)
        for r in b_results:
            diagnose_b(r, pages, index, names, args.content_max_chars)

    summary = summarize(results, gt.excluded)
    summary["params"] = {"from_year": args.from_year, "until_year": args.until_year,
                         "journals": args.journals, "source": args.source,
                         "run_date": date.today().isoformat()}
    with open(out / "articles.csv", "w", newline="", encoding="utf-8") as f:
        rows_out = [article_row(r, names) for r in results]
        w = csv.DictWriter(f, fieldnames=list(rows_out[0]) if rows_out else ["doi"])
        w.writeheader()
        w.writerows(rows_out)
    with open(out / "excluded.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["journal", "year", "doi", "reason", "title"])
        w.writeheader()
        w.writerows(sorted(gt.excluded, key=lambda e: (e["journal"], e["year"], e["reason"])))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    md = render_markdown(summary, args)
    (out / "summary.md").write_text(md)
    print(md)
    log.info("Wrote %s", out)
    return summary


if __name__ == "__main__":
    main()
