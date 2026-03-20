"""OpenAlex API client and publication enrichment."""
import logging
import os
import time

import requests

from database import Database

logger = logging.getLogger(__name__)

OPENALEX_BASE_URL = "https://api.openalex.org"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")

_session = None


def _get_session():
    """Lazily create a requests session with polite-pool headers."""
    global _session
    if _session is None:
        _session = requests.Session()
        ua = f"econ-newsfeed/1.0 (mailto:{MAILTO})" if MAILTO else "econ-newsfeed/1.0"
        _session.headers.update({"User-Agent": ua})
    return _session


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
    or None if no match is found.
    """
    session = _get_session()
    params = {"search": title, "per_page": 5}
    if MAILTO:
        params["mailto"] = MAILTO

    try:
        resp = session.get(
            f"{OPENALEX_BASE_URL}/works", params=params, timeout=10
        )
        resp.raise_for_status()
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


def _parse_work(work: dict) -> dict:
    """Parse an OpenAlex work object into our enrichment dict."""
    raw_doi = work.get("doi") or ""
    doi = raw_doi.replace("https://doi.org/", "") if raw_doi else None

    openalex_id = work.get("id", "").replace("https://openalex.org/", "") or None

    coauthors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        coauthors.append({
            "display_name": author.get("display_name", ""),
            "openalex_author_id": (
                author.get("id", "").replace("https://openalex.org/", "") or None
            ),
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
