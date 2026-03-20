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
        resp = session.get(
            f"{OPENALEX_BASE_URL}/works", params=params, timeout=10
        )
        # Retry once on 429 after waiting
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.info("OpenAlex rate limited, waiting %ds", retry_after)
            time.sleep(retry_after)
            resp = session.get(
                f"{OPENALEX_BASE_URL}/works", params=params, timeout=10
            )
        resp.raise_for_status()
        _increment_budget()
        results = resp.json().get("results", [])
    except (requests.RequestException, ValueError) as e:
        logger.warning("OpenAlex search failed for '%s': %s", title[:50], e)
        return None

    if not results:
        return None

    # Find a result where the author name appears in authorships
    author_lower = author_name.lower()
    for work in results:
        for authorship in work.get("authorships", []):
            display = (authorship.get("author", {}).get("display_name") or "").lower()
            if author_lower in display or display in author_lower:
                return _parse_work(work)

    return None


def _strip_prefix(url: str | None, prefix: str) -> str | None:
    """Strip a URL prefix and return the identifier, or None if empty."""
    return (url or "").replace(prefix, "") or None


def _parse_work(work: dict) -> dict:
    """Parse an OpenAlex work object into our enrichment dict."""
    doi = _strip_prefix(work.get("doi"), _DOI_PREFIX)
    openalex_id = _strip_prefix(work.get("id"), _OPENALEX_PREFIX)

    coauthors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        coauthors.append({
            "display_name": author.get("display_name", ""),
            "openalex_author_id": _strip_prefix(author.get("id"), _OPENALEX_PREFIX),
        })

    abstract = None
    inverted_index = work.get("abstract_inverted_index")
    if inverted_index:
        abstract = reconstruct_abstract(inverted_index)

    return {
        "doi": doi,
        "openalex_id": openalex_id,
        "coauthors": coauthors,
        "abstract": abstract,
    }


def enrich_publication(paper_id, title, author_name, existing_abstract=None):
    """Enrich a single publication with OpenAlex data.

    Returns True if enrichment data was found and stored, False otherwise.
    """
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
    )
    return True


def enrich_new_publications(limit=50):
    """Enrich unenriched publications with OpenAlex data.

    Stops early if the daily API budget is exhausted.
    Returns the number of papers processed (matched or not).
    """
    if not _check_budget():
        remaining = DAILY_BUDGET - _daily_counter["count"]
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
        if not _check_budget():
            logger.info("OpenAlex daily budget reached, stopping enrichment")
            break
        success = enrich_publication(
            paper_id=paper["id"],
            title=paper["title"],
            author_name=paper["author_name"],
            existing_abstract=paper.get("abstract"),
        )
        if success:
            enriched += 1
        time.sleep(0.5)  # ~2 req/s — safe for API key tier

    logger.info("OpenAlex enrichment: %d/%d papers matched", enriched, len(papers))
    return len(papers)
