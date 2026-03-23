"""Resolve DOIs from publisher URLs via regex extraction and Crossref API."""
import logging
import re

import requests

logger = logging.getLogger(__name__)

_CROSSREF_BASE = "https://api.crossref.org"
_session = None


def _get_session():
    """Lazily create a requests session for Crossref API calls."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "econ-newsfeed/1.0"})
    return _session


def extract_doi_from_url(url: str) -> str | None:
    """Extract a DOI from a publisher URL using regex. No API calls.

    Returns the DOI string (e.g. '10.1257/aer.20181234') or None.
    Only extracts DOIs that appear to be article-level identifiers.
    Synthesizes DOIs for SSRN URLs from the abstract_id parameter.
    """
    if not url:
        return None

    # Reject supplementary material / asset URLs
    if '/asset/' in url or '/supinfo/' in url or '/supp/' in url:
        return None

    # Strip fragment
    url_clean = url.split('#')[0]

    # SSRN URLs: synthesize DOI from abstract_id parameter
    ssrn_match = re.search(r'ssrn\.com/.*abstract_id=(\d+)', url_clean)
    if ssrn_match:
        return f"10.2139/ssrn.{ssrn_match.group(1)}"

    # Match DOI pattern: 10.NNNN/anything-except-whitespace-?-#
    match = re.search(r'(?:^|[/=])(10\.\d{4,}/[^\s?#]+)', url_clean)
    if not match:
        return None

    doi = match.group(1).rstrip('/')
    return doi


def extract_pii_from_url(url: str) -> str | None:
    """Extract a ScienceDirect PII from a URL. Returns PII string or None."""
    if not url:
        return None
    match = re.search(r'/pii/([A-Z0-9]+)', url)
    return match.group(1) if match else None


def resolve_pii_via_crossref(pii: str) -> str | None:
    """Resolve a ScienceDirect PII to a DOI via Crossref alternative-id filter."""
    try:
        resp = _get_session().get(
            f"{_CROSSREF_BASE}/works",
            params={"filter": f"alternative-id:{pii}", "rows": 1},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
        if items:
            return items[0].get("DOI")
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("Crossref PII lookup failed for %s: %s", pii, e)
    return None


def resolve_doi(url: str) -> str | None:
    """Resolve a DOI from a URL. Tries regex first, then PII→Crossref."""
    doi = extract_doi_from_url(url)
    if doi:
        return doi

    pii = extract_pii_from_url(url)
    if pii:
        return resolve_pii_via_crossref(pii)

    return None
