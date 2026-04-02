"""OpenAlex API client and publication enrichment."""
import logging
import os
import time
from datetime import date

import requests

from database import Database

logger = logging.getLogger(__name__)

OPENALEX_BASE_URL = "https://api.openalex.org"
_OPENALEX_PREFIX = "https://openalex.org/"
_DOI_PREFIX = "https://doi.org/"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")
API_KEY = os.environ.get("OPENALEX_API_KEY", "")
DAILY_BUDGET = int(os.environ.get("OPENALEX_DAILY_BUDGET", "1000"))

_session = None
_daily_counter = {"date": None, "count": 0}


def _get_session():
    """Lazily create a requests session with API key auth."""
    global _session
    if _session is None:
        _session = requests.Session()
        ua = f"econ-newsfeed/1.0 (mailto:{MAILTO})" if MAILTO else "econ-newsfeed/1.0"
        _session.headers.update({"User-Agent": ua})
        if API_KEY:
            _session.headers.update({"Authorization": f"Bearer {API_KEY}"})
    return _session


def _check_budget() -> bool:
    """Return True if we haven't hit the daily search budget."""
    today = date.today()
    if _daily_counter["date"] != today:
        _daily_counter["date"] = today
        _daily_counter["count"] = 0
    return _daily_counter["count"] < DAILY_BUDGET


def _increment_budget() -> None:
    """Record one API call against the daily budget."""
    _daily_counter["count"] += 1


def reconstruct_abstract(inverted_index: dict) -> str:
    """Reconstruct abstract text from OpenAlex inverted-index format."""
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)


def _get_with_retry(session, url, params):
    """GET with one retry on 429, respecting Retry-After header."""
    resp = session.get(url, params=params, timeout=10)
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "5"))
        logger.info("OpenAlex rate limited, waiting %ds", retry_after)
        time.sleep(retry_after)
        resp = session.get(url, params=params, timeout=10)
    return resp


def search_work(title: str, author_name: str) -> dict | None:
    """Search OpenAlex for a work matching the given title and author.

    Returns a dict with keys: doi, openalex_id, coauthors, abstract
    or None if no match is found. Returns None if daily budget exhausted.
    """
    if not _check_budget():
        return None

    session = _get_session()
    params = {"search": title, "per_page": 5}
    if MAILTO:
        params["mailto"] = MAILTO

    try:
        resp = _get_with_retry(session, f"{OPENALEX_BASE_URL}/works", params)
        resp.raise_for_status()
        _increment_budget()
        results = resp.json().get("results", [])
    except (requests.RequestException, ValueError) as e:
        logger.warning("OpenAlex search failed for '%s': %s", title[:50], e)
        return None

    if not results:
        return None

    # Match on last name — handles "M. Steinhardt" vs "Max Friedrich Steinhardt"
    last_name = author_name.split()[-1].lower() if author_name.strip() else ""
    if not last_name:
        return None

    for work in results:
        for authorship in work.get("authorships", []):
            display = (authorship.get("author", {}).get("display_name") or "").lower()
            if last_name in display.split():
                return _parse_work(work)

    return None


def lookup_by_doi(doi: str) -> dict | None:
    """Look up a work in OpenAlex by exact DOI.

    Returns a dict with keys: doi, openalex_id, coauthors, abstract, title
    or None if not found. Does not consume the daily search budget.
    """
    session = _get_session()
    try:
        resp = session.get(f"{OPENALEX_BASE_URL}/works/doi:{doi}", timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        work = resp.json()
        result = _parse_work(work)
        result["title"] = work.get("title", "")
        return result
    except (requests.RequestException, ValueError) as e:
        logger.warning("OpenAlex DOI lookup failed for '%s': %s", doi, e)
        return None


def _strip_prefix(url: str | None, prefix: str) -> str | None:
    """Strip a URL prefix and return the identifier, or None if empty."""
    return (url or "").replace(prefix, "") or None


def _parse_topics(work: dict) -> list[dict]:
    """Extract topic metadata from an OpenAlex work object."""
    return [
        {
            "openalex_topic_id": _strip_prefix(t.get("id"), _OPENALEX_PREFIX),
            "topic_name": t.get("display_name", ""),
            "subfield_name": (t.get("subfield") or {}).get("display_name"),
            "field_name": (t.get("field") or {}).get("display_name"),
            "domain_name": (t.get("domain") or {}).get("display_name"),
            "score": t.get("score"),
        }
        for t in work.get("topics", [])
    ]


def _is_bad_coauthor_name(display_name: str) -> bool:
    """Return True if a coauthor display_name is too incomplete to store."""
    name = display_name.strip()
    if not name:
        return True
    parts = name.split()
    # Single-initial first part: "A.", "A", "J." etc.
    if len(parts) >= 2:
        first = parts[0]
        if len(first.rstrip('.')) <= 1:
            return True
    return False


def _parse_work(work: dict) -> dict:
    """Parse an OpenAlex work object into our enrichment dict."""
    doi = _strip_prefix(work.get("doi"), _DOI_PREFIX)
    openalex_id = _strip_prefix(work.get("id"), _OPENALEX_PREFIX)

    coauthors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        display_name = author.get("display_name", "")
        if _is_bad_coauthor_name(display_name):
            logger.debug("Skipping coauthor with bad name: %r", display_name)
            continue
        coauthors.append({
            "display_name": display_name,
            "openalex_author_id": _strip_prefix(author.get("id"), _OPENALEX_PREFIX),
        })

    abstract = None
    inverted_index = work.get("abstract_inverted_index")
    if inverted_index:
        abstract = reconstruct_abstract(inverted_index)

    pub_year = work.get("publication_year")
    year = str(pub_year) if pub_year is not None else None

    return {
        "doi": doi,
        "openalex_id": openalex_id,
        "coauthors": coauthors,
        "abstract": abstract,
        "topics": _parse_topics(work),
        "year": year,
    }


def enrich_publication(paper_id, title, author_name, existing_abstract=None, doi=None):
    """Enrich a single publication with OpenAlex data.

    If doi is provided, uses exact DOI lookup (no budget cost).
    Otherwise falls back to title+author search.
    Returns True if enrichment data was found and stored, False otherwise.
    """
    result = None

    # Strategy 1: DOI lookup (exact, no budget cost)
    if doi:
        result = lookup_by_doi(doi)

    # Strategy 2: Title search (fuzzy, costs budget)
    if not result:
        result = search_work(title, author_name)

    if not result:
        return False

    # Only use OpenAlex abstract as fallback
    abstract = result["abstract"] if not existing_abstract else None

    Database.update_openalex_data(
        paper_id=paper_id,
        doi=result["doi"],
        openalex_id=result["openalex_id"],
        coauthors=result["coauthors"],
        abstract=abstract,
        year=result.get("year"),
    )

    # Backfill openalex_author_id on researchers from coauthor data
    _backfill_researcher_openalex_ids(paper_id, result["coauthors"])

    return True


def _backfill_researcher_openalex_ids(paper_id, coauthors):
    """Populate openalex_author_id on researchers matched to this paper's authors."""
    if not coauthors:
        return
    # Build lookup: openalex_author_id -> display_name from coauthors
    oa_ids = {ca["openalex_author_id"]: ca["display_name"]
              for ca in coauthors if ca.get("openalex_author_id")}
    if not oa_ids:
        return
    # Get researchers linked to this paper
    rows = Database.fetch_all(
        """SELECT r.id, r.first_name, r.last_name, r.openalex_author_id
           FROM researchers r
           JOIN authorship a ON a.researcher_id = r.id
           WHERE a.publication_id = %s AND r.openalex_author_id IS NULL""",
        (paper_id,),
    )
    for r in rows:
        last_name = r['last_name'].lower()
        for oa_id, display_name in oa_ids.items():
            # Match by last name appearing in OpenAlex display name
            if last_name in display_name.lower().split():
                Database.execute_query(
                    "UPDATE researchers SET openalex_author_id = %s WHERE id = %s",
                    (oa_id, r['id']),
                )
                break


def enrich_new_publications(limit=50):
    """Enrich unenriched publications with OpenAlex data.

    Papers with DOIs from paper_links are enriched first (exact match).
    Papers without links are only enriched if published (title search fallback).
    """
    if not _check_budget():
        logger.info("OpenAlex daily budget exhausted (%d/%d), skipping", _daily_counter["count"], DAILY_BUDGET)
        return 0

    papers = Database.get_unenriched_papers(limit=limit)
    if not papers:
        logger.info("No unenriched papers found")
        return 0

    logger.info("Enriching %d papers via OpenAlex (budget: %d/%d used today)",
                len(papers), _daily_counter["count"], DAILY_BUDGET)
    enriched = 0
    for paper in papers:
        doi = paper.get("link_doi")
        if not doi and not _check_budget():
            logger.info("OpenAlex daily budget reached, stopping enrichment")
            break
        success = enrich_publication(
            paper_id=paper["id"],
            title=paper["title"],
            author_name=paper["author_name"],
            existing_abstract=paper.get("abstract"),
            doi=doi,
        )
        if success:
            enriched += 1
        if not doi:
            time.sleep(0.5)  # Only rate-limit title searches

    logger.info("OpenAlex enrichment: %d/%d papers matched", enriched, len(papers))
    return enriched


def fetch_topics_batch(openalex_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch topics for multiple works from OpenAlex.

    Returns dict mapping openalex_id -> list of topic dicts.
    Processes in chunks of 50 (OpenAlex filter limit).
    """
    if not openalex_ids:
        return {}

    if not _check_budget():
        logger.info("OpenAlex daily budget exhausted, skipping topic fetch")
        return {}

    session = _get_session()
    result: dict[str, list[dict]] = {}

    for i in range(0, len(openalex_ids), 50):
        if not _check_budget():
            logger.info("OpenAlex daily budget reached, stopping topic fetch")
            break

        chunk = openalex_ids[i : i + 50]
        full_ids = "|".join(f"https://openalex.org/{oid}" for oid in chunk)
        params: dict[str, str | int] = {
            "filter": f"openalex:{full_ids}",
            "per_page": 50,
            "select": "id,topics",
        }
        if MAILTO:
            params["mailto"] = MAILTO

        try:
            resp = _get_with_retry(session, f"{OPENALEX_BASE_URL}/works", params)
            resp.raise_for_status()
            _increment_budget()

            for work in resp.json().get("results", []):
                oa_id = _strip_prefix(work.get("id"), _OPENALEX_PREFIX)
                topics = _parse_topics(work)
                if topics and oa_id:
                    result[oa_id] = topics
        except (requests.RequestException, ValueError) as e:
            logger.warning("Failed to fetch topics for chunk starting at %d: %s", i, e)

        if i + 50 < len(openalex_ids):
            time.sleep(0.5)

    return result
