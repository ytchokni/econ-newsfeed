"""Google Custom Search Engine client for finding researcher websites."""
import logging
import os

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://www.googleapis.com/customsearch/v1"


class QuotaExhaustedError(Exception):
    """Raised when Google CSE daily quota is exhausted (HTTP 429)."""


def search_researcher(
    first_name: str,
    last_name: str,
    affiliation: str | None = None,
) -> tuple[str, list[dict]]:
    """Search Google for a researcher's personal website.

    Returns (query_used, results) where each result is
    {"title": str, "url": str, "snippet": str}.
    Returns (query, []) if API key not configured or quota exhausted.
    """
    api_key = os.environ.get("GOOGLE_CSE_API_KEY")
    cse_cx = os.environ.get("GOOGLE_CSE_CX")
    if not api_key or not cse_cx:
        return "", []

    query = f'"{first_name} {last_name}" economist personal website'
    if affiliation:
        query = f'"{first_name} {last_name}" {affiliation} economist'

    try:
        resp = requests.get(
            _API_URL,
            params={"key": api_key, "cx": cse_cx, "q": query, "num": 5},
            timeout=10,
        )
        if resp.status_code == 429:
            logger.warning("Google CSE daily quota exhausted")
            raise QuotaExhaustedError("Google CSE daily quota exhausted")
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        return query, [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items
        ]
    except Exception as e:
        logger.warning("Google CSE search failed for %s %s: %s", first_name, last_name, e)
        return query, []
