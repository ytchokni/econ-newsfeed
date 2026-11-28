"""Weekly top-5 journal coverage for the About page.

Compares every research article published in AER, Econometrica, JPE, QJE and REStud since
``TOP5_COVERAGE_FROM_YEAR`` (Crossref ground truth) with our papers, using the matching
and classification of ``backend.coverage.top5_validation`` (the validation script behind
#257), and stores the headline numbers in ``top5_coverage_runs``. ``GET /api/stats`` reads
the latest row, so page views never call Crossref.

The validation script holds every paper, authorship row and researcher in memory (about
1 GB for 260k papers); the API container has 1 GB in total. This job streams the database
instead and keeps only what can match the ~3,000 articles:

* papers whose DOI or normalised title equals an article's, or that share one of the
  rarest title tokens a fuzzy match would retrieve by (two passes: document frequencies of
  the articles' tokens first, then the candidates);
* researchers sharing a surname key or an OpenAlex author ID with a Crossref author (no
  other researcher can pass the surname checks);
* authorship and paper details only for the papers the matcher asks about.

The result is the same as the full script's (tested on the 2026-11-27 export: 70.8% / 77.5%).

Network use per run: about 30 cursor-paged Crossref requests and about 60 batched OpenAlex
DOI lookups (author IDs; optional), anonymous, one second apart, identified by a project
User-Agent without a personal address.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Protocol

from backend.coverage.top5_validation import (
    JOURNALS,
    NameIndex,
    PaperIndex,
    Researcher,
    _title_tokens,
    attach_openalex_authors,
    classify_article,
    doi_from_url,
    fuzzy_candidate_tokens,
    fuzzy_targets,
    load_ground_truth,
    norm_name,
    normalize_doi,
    summarize,
    surname_core,
)
from backend.database.connection import execute_query, fetch_one, get_connection
from backend.database.papers import normalize_title

logger = logging.getLogger(__name__)

FROM_YEAR = int(os.environ.get("TOP5_COVERAGE_FROM_YEAR", "2020"))
USE_OPENALEX = os.environ.get("TOP5_COVERAGE_OPENALEX", "true").lower() == "true"
# A run is due when the latest stored one is older than this (weekly cron + catch-up at boot).
STALE_AFTER = timedelta(days=8)
# A boot-time catch-up is skipped when any attempt (even a failed one) is this recent.
CATCHUP_RETRY_AFTER = timedelta(hours=24)
_LOCK_NAME = "econ_top5_coverage"
_STREAM_BATCH = 5000


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------


class CoverageSource(Protocol):
    def paper_titles(self) -> Iterable[tuple[int, str | None, str | None]]:
        """(id, title, doi) for every paper with a title; iterated twice."""

    def links(self) -> Iterable[tuple[int, str | None, str | None]]:
        """(paper_id, doi, url) for paper links that may carry a DOI."""

    def researchers(self) -> Iterable[tuple[int, str, str, str | None, int]]:
        """(id, first, last, openalex_author_id, number of URLs) for every researcher."""

    def authors_of(self, paper_id: int) -> set[int]:
        """Researcher ids on one paper."""

    def paper(self, paper_id: int) -> dict:
        """id, title, year, venue, status, doi of one paper."""


class MySQLSource:
    """Streams from the live database with unbuffered cursors (read-only)."""

    def _stream(self, sql: str) -> Iterator[tuple]:
        conn = get_connection()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                while True:
                    rows = cur.fetchmany(_STREAM_BATCH)
                    if not rows:
                        break
                    yield from rows
            finally:
                cur.close()
        finally:
            conn.close()

    def paper_titles(self):
        return self._stream("SELECT id, title, doi FROM papers WHERE title IS NOT NULL")

    def links(self):
        return self._stream(
            "SELECT paper_id, doi, url FROM paper_links WHERE doi IS NOT NULL OR url LIKE '%10.%'"
        )

    def researchers(self):
        return self._stream(
            """SELECT r.id, r.first_name, r.last_name, r.openalex_author_id, COALESCE(u.n, 0)
               FROM researchers r
               LEFT JOIN (SELECT researcher_id, COUNT(*) n FROM researcher_urls
                          GROUP BY researcher_id) u ON u.researcher_id = r.id"""
        )

    def authors_of(self, paper_id: int) -> set[int]:
        from backend.database.connection import fetch_all

        rows = fetch_all("SELECT researcher_id FROM authorship WHERE publication_id = %s", (paper_id,))
        return {int(r["researcher_id"]) for r in rows}

    def paper(self, paper_id: int) -> dict:
        return fetch_one(
            "SELECT id, title, year, venue, status, doi FROM papers WHERE id = %s", (paper_id,)
        ) or {"id": paper_id}


class RowsSource:
    """In-memory rows in the validation script's export format (tests, offline checks)."""

    def __init__(self, papers: list[dict], links: list[dict], authorship: list[dict],
                 researchers: list[dict]):
        self._papers = {int(p["id"]): p for p in papers if p.get("title") is not None}
        self._links = links
        self._authors: dict[int, set[int]] = defaultdict(set)
        for a in authorship:
            self._authors[int(a["p"])].add(int(a["r"]))
        self._researchers = researchers

    def paper_titles(self):
        return ((pid, p.get("title"), p.get("doi")) for pid, p in self._papers.items())

    def links(self):
        return ((int(link["paper_id"]), link.get("doi"), link.get("url")) for link in self._links)

    def researchers(self):
        return ((int(r["id"]), r.get("first") or "", r.get("last") or "", r.get("oa"),
                 int(r.get("n_urls") or 0)) for r in self._researchers)

    def authors_of(self, paper_id: int) -> set[int]:
        return set(self._authors.get(paper_id, ()))

    def paper(self, paper_id: int) -> dict:
        return self._papers[paper_id]


class _LazyMap:
    """Read-only mapping that loads values on first access (``get`` and ``[]``)."""

    def __init__(self, load: Callable[[int], object]):
        self._load = load
        self._cache: dict[int, object] = {}

    def __getitem__(self, key: int):
        if key not in self._cache:
            self._cache[key] = self._load(key)
        return self._cache[key]

    def get(self, key: int, default=None):
        value = self[key]
        return default if value is None else value


# ---------------------------------------------------------------------------
# Streaming index
# ---------------------------------------------------------------------------


def _surname_keys(family: str | None) -> set[str]:
    """The keys NameIndex files a surname under (core tokens and the squashed name)."""
    return (set(surname_core(family)) | {"".join(norm_name(family).split())}) - {""}


def build_indexes(articles, source: CoverageSource) -> tuple[PaperIndex, NameIndex, dict]:
    """PaperIndex and NameIndex restricted to what can match ``articles``."""
    target_dois = {a.doi for a in articles}
    target_titles: set[str] = set()
    targets: list[str] = []
    for a in articles:
        target_titles |= {normalize_title(t) for t in (a.full_title, a.title)} - {""}
        targets.extend(fuzzy_targets(a))
    target_tokens = set().union(*(_title_tokens(t) for t in targets)) if targets else set()

    # Researchers: only those a Crossref author can match by surname key or OpenAlex ID.
    author_keys: set[str] = set()
    author_oa: set[str] = set()
    for a in articles:
        for au in a.authors:
            author_keys |= _surname_keys(au.family)
            if au.openalex_id:
                author_oa.add(au.openalex_id.upper())
    kept_researchers = []
    for rid, first, last, oa, n_urls in source.researchers():
        oa_key = (oa or "").rsplit("/", 1)[-1].upper()
        if _surname_keys(last) & author_keys or (oa_key and oa_key in author_oa):
            kept_researchers.append(Researcher(int(rid), first or "", last or "", oa, int(n_urls or 0)))
    names = NameIndex(kept_researchers)

    # Pass 1: how many titles each of the articles' tokens appears in.
    doc_freq: dict[str, int] = defaultdict(int)
    n_papers = 0
    for _pid, title, _doi in source.paper_titles():
        n = normalize_title(title)
        if n:
            n_papers += 1
            for tok in _title_tokens(n) & target_tokens:
                doc_freq[tok] += 1
    selected: set[str] = set()
    for t in targets:
        selected.update(fuzzy_candidate_tokens(t, lambda tok: doc_freq.get(tok, 0)))

    # Links can attach an article DOI to a paper; the paper must exist with a title.
    link_dois: dict[int, set[str]] = defaultdict(set)
    for pid, doi, url in source.links():
        d = normalize_doi(doi) or doi_from_url(url)
        if d and d in target_dois:
            link_dois[int(pid)].add(d)

    # Pass 2: keep candidate papers only.
    index = PaperIndex([], [], [], names)
    for pid, title, doi in source.paper_titles():
        pid = int(pid)
        n = normalize_title(title)
        keep = False
        if n:
            if n in target_titles:
                index.by_title[n].add(pid)
                keep = True
            hits = _title_tokens(n) & selected
            for tok in hits:
                index.postings[tok].append(pid)
            keep = keep or bool(hits)
        d = normalize_doi(doi)
        if d and d in target_dois:
            index.by_doi[d].add(pid)
            index.linked_dois[pid].add(d)
        for ld in link_dois.get(pid, ()):
            index.by_doi[ld].add(pid)
            index.linked_dois[pid].add(ld)
        if keep:
            index.norm[pid] = n
    index.doc_freq = lambda tok: doc_freq.get(tok, 0)  # type: ignore[method-assign]
    index.papers = _LazyMap(source.paper)  # type: ignore[assignment]
    index.authors_of = _LazyMap(source.authors_of)  # type: ignore[assignment]
    stats = {"papers": n_papers, "candidate_papers": len(index.norm),
             "candidate_researchers": len(kept_researchers)}
    return index, names, stats


# ---------------------------------------------------------------------------
# Run and store
# ---------------------------------------------------------------------------


def compute_coverage(source: CoverageSource, *, from_year: int = FROM_YEAR, until_year: int | None = None,
                     cache_dir=None, use_openalex: bool = USE_OPENALEX, sleep: float = 1.0,
                     ground_truth=None) -> dict:
    """Coverage summary (``summarize`` blocks) for top-5 articles from_year..until_year."""
    until_year = until_year or date.today().year
    gt = ground_truth
    if gt is None:
        # Responses are cached for this run only; the weekly schedule is the politeness bound.
        with tempfile.TemporaryDirectory(prefix="top5_") as tmp:
            cache = Path(cache_dir or tmp)
            gt = load_ground_truth(list(JOURNALS), from_year, until_year, cache, sleep=sleep)
            if use_openalex:
                found = attach_openalex_authors(gt.articles, cache, sleep=sleep)
                logger.info("top5 coverage: OpenAlex author IDs for %d/%d articles", found, len(gt.articles))
    index, names, stats = build_indexes(gt.articles, source)
    results = [classify_article(a, index, names) for a in gt.articles]
    summary = summarize(results, gt.excluded)
    summary["params"] = {"from_year": from_year, "until_year": until_year,
                         "journals": list(JOURNALS), "openalex": bool(use_openalex), **stats}
    return summary


def store_run(summary: dict, duration_seconds: int | None = None,
              computed_at: datetime | None = None) -> None:
    o = summary["overall"]
    keep = {k: summary[k] for k in ("overall", "by_journal", "by_year", "params") if k in summary}
    execute_query(
        """INSERT INTO top5_coverage_runs
               (computed_at, status, from_year, until_year, articles, observed, tracked_articles,
                tracked_observed, summary, duration_seconds)
           VALUES (%s, 'ok', %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            (computed_at or datetime.now(timezone.utc)).replace(tzinfo=None),
            summary["params"]["from_year"], summary["params"]["until_year"],
            o["articles"], o["observed"], o["tracked_articles"], o["tracked_observed"],
            json.dumps(keep), duration_seconds,
        ),
    )


def store_failure(error: str, duration_seconds: int | None = None,
                  attempted_at: datetime | None = None, from_year: int = FROM_YEAR) -> None:
    """Record a failed attempt, so boot-time catch-ups don't repeat it on every restart."""
    execute_query(
        """INSERT INTO top5_coverage_runs
               (computed_at, status, error, from_year, until_year, duration_seconds)
           VALUES (%s, 'failed', %s, %s, %s, %s)""",
        (
            (attempted_at or datetime.now(timezone.utc)).replace(tzinfo=None),
            error[:500], from_year, date.today().year, duration_seconds,
        ),
    )


def latest_run() -> dict | None:
    """The newest successful run, or None."""
    row = fetch_one(
        """SELECT computed_at, from_year, until_year, articles, observed, tracked_articles,
                  tracked_observed, summary
           FROM top5_coverage_runs WHERE status = 'ok'
           ORDER BY computed_at DESC, id DESC LIMIT 1"""
    )
    if not row:
        return None
    summary = row.get("summary")
    if isinstance(summary, (bytes, bytearray)):
        summary = summary.decode("utf-8")
    if isinstance(summary, str):
        summary = json.loads(summary)
    row["summary"] = summary or {}
    return row


def last_attempt_at() -> datetime | None:
    """When the newest run, successful or not, started."""
    row = fetch_one("SELECT MAX(computed_at) AS t FROM top5_coverage_runs")
    return (row or {}).get("t")


def _naive_utc(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def is_due(now: datetime | None = None) -> bool:
    """No successful result newer than STALE_AFTER."""
    row = latest_run()
    if not row or not row.get("computed_at"):
        return True
    return _naive_utc(now) - row["computed_at"] >= STALE_AFTER


def catchup_due(now: datetime | None = None) -> bool:
    """Boot-time catch-up: the result is stale and nothing was attempted in the last day.

    Without the second condition, a Crossref/OpenAlex outage would make every deploy
    or restart re-run the full comparison."""
    if not is_due(now):
        return False
    last = last_attempt_at()
    return last is None or _naive_utc(now) - last >= CATCHUP_RETRY_AFTER


def run_top5_coverage_job(*, force: bool = False, store: bool = True, catchup: bool = False,
                          source: CoverageSource | None = None) -> dict | None:
    """Compute and store coverage. Never raises (scheduler job).

    force: run regardless (weekly cron, ``--force``). catchup: run only if catchup_due()
    (boot). Otherwise: run only if no successful result is newer than STALE_AFTER.
    Failed attempts are recorded (when storing) so catch-ups back off for a day."""
    try:
        if catchup and not catchup_due():
            logger.info("top5 coverage: recent result or attempt, skipping catch-up")
            return None
        if not force and not catchup and not is_due():
            logger.info("top5 coverage: latest run is recent, skipping")
            return None
    except Exception:
        logger.exception("top5 coverage: could not read previous runs")
        return None
    lock_conn = get_connection()
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()
    try:
        cur = lock_conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, 0)", (_LOCK_NAME,))
        got = cur.fetchone()
        cur.close()
        if not (got and got[0] == 1):
            logger.info("top5 coverage: another run holds the lock, skipping")
            return None
        t0 = time.monotonic()
        summary = compute_coverage(source or MySQLSource())
        duration = int(time.monotonic() - t0)
        o = summary["overall"]
        logger.info("top5 coverage: %s/%s articles (%s%%), %s%% of %s with a tracked author, in %ss",
                    o["observed"], o["articles"], o["coverage_pct"], o["coverage_tracked_pct"],
                    o["tracked_articles"], duration)
        if store:
            store_run(summary, duration)
        return summary
    except Exception as exc:
        logger.exception("top5 coverage run failed")
        if store:
            try:
                store_failure(f"{type(exc).__name__}: {exc}", int(time.monotonic() - t0), attempted_at=started_at)
            except Exception:
                logger.exception("top5 coverage: could not record the failed attempt")
        return None
    finally:
        try:
            cur = lock_conn.cursor()
            cur.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
            cur.fetchone()
            cur.close()
        except Exception:
            pass
        lock_conn.close()
