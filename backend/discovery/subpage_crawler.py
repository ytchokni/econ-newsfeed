"""Crawl a personal website's root page to find research/publication subpages."""
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "econ-newsfeed/1.0 (mailto:yogamtchokni@googlemail.com)"}

_MULTI_TENANT_DOMAINS = (
    "sites.google.com", "github.io", "wixsite.com", "wordpress.com",
    "weebly.com", "squarespace.com", "netlify.app", "vercel.app",
)

_SUBPAGE_PATTERNS = {
    "research": re.compile(r"(?i)/(?:research|working[-_]?papers?|work[-_]?in[-_]?progress)(?:\.html?)?/*$"),
    "publications": re.compile(r"(?i)/(?:publications?|papers?|selected[-_]?papers?)(?:\.html?)?/*$"),
}


class _LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href and not href.startswith(("#", "mailto:", "javascript:", "tel:")):
                self.links.append(urljoin(self.base_url, href))


def _same_site(base_url: str, candidate_url: str) -> bool:
    """Check if candidate is on the same site as base."""
    base_p = urlparse(base_url)
    cand_p = urlparse(candidate_url)
    base_host = (base_p.hostname or "").removeprefix("www.")
    cand_host = (cand_p.hostname or "").removeprefix("www.")

    if base_host != cand_host:
        return False

    for tenant in _MULTI_TENANT_DOMAINS:
        if base_host == tenant or base_host.endswith("." + tenant):
            base_segs = [s for s in base_p.path.split("/") if s]
            cand_segs = [s for s in cand_p.path.split("/") if s]
            if len(base_segs) >= 2 and len(cand_segs) >= 2:
                return base_segs[:2] == cand_segs[:2]
    return True


def crawl_subpages(root_url: str) -> list[dict]:
    """Fetch root_url HTML and find research/publications subpages.

    Returns list of {"page_type": str, "url": str}.
    """
    try:
        resp = requests.get(root_url, timeout=15, headers=_HEADERS, allow_redirects=True)
        if resp.status_code >= 400:
            return []
    except Exception as e:
        logger.debug("Failed to crawl %s: %s", root_url, e)
        return []

    parser = _LinkExtractor(root_url)
    try:
        parser.feed(resp.text)
    except Exception:
        return []

    found: dict[str, str] = {}
    for link in parser.links:
        if link.rstrip("/") == root_url.rstrip("/"):
            continue
        if not _same_site(root_url, link):
            continue
        path = urlparse(link).path.lower()
        for page_type, pattern in _SUBPAGE_PATTERNS.items():
            if pattern.search(path) and page_type not in found:
                found[page_type] = link

    return [{"page_type": pt, "url": url} for pt, url in found.items()]
