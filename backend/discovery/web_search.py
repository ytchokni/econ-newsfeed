"""Searlo web search client for finding researcher websites."""
import logging
import os

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.searlo.tech/api/v1/search/web"


class QuotaExhaustedError(Exception):
    """Raised when search API quota/credits are exhausted."""


def _extract_result_items(data: dict) -> list[dict]:
    """Return organic result rows across Searlo's documented response aliases."""
    for key in ("organic", "organic_results", "items"):
        items = data.get(key)
        if isinstance(items, list):
            return items
    return []


def search_researcher(
    first_name: str,
    last_name: str,
    affiliation: str | None = None,
) -> tuple[str, list[dict]]:
    """Search for a researcher's personal website via Searlo.

    Returns (query_used, results) where each result is
    {"title": str, "url": str, "snippet": str}.
    Returns (query, []) if API key not configured.
    """
    api_key = os.environ.get("SEARLO_API_KEY")
    if not api_key:
        return "", []

    query = f'"{first_name} {last_name}" economist personal website'
    if affiliation:
        query = f'"{first_name} {last_name}" {affiliation} economist'

    try:
        resp = requests.get(
            _API_URL,
            params={"q": query, "limit": 5},
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        if resp.status_code == 429:
            raise QuotaExhaustedError("Searlo rate limit exceeded")
        if resp.status_code == 402:
            raise QuotaExhaustedError("Searlo credits exhausted")
        resp.raise_for_status()
        data = resp.json()
        items = _extract_result_items(data)
        return query, [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items
        ]
    except QuotaExhaustedError:
        raise
    except Exception as e:
        logger.warning("Searlo search failed for %s %s: %s", first_name, last_name, e)
        return query, []
