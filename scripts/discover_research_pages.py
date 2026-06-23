"""Discover research/publications pages from researcher homepages.

Parses stored HTML from html_content to find navigation links pointing to
research listing pages on the same site. Only matches links where the
research keyword is the terminal path segment (not deep sub-pages or files).

Usage:
    poetry run python scripts/discover_research_pages.py [--dry-run] [--verbose]
"""
import gc
import logging
import os
import re
import sys
import time
import warnings

import requests
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
from backend.database import fetch_all, fetch_one
from backend.database.researchers import add_researcher_url

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Research page detection
# ---------------------------------------------------------------------------

# File extensions that are never research listing pages
_FILE_EXTENSIONS = re.compile(
    r'\.(?:pdf|zip|gz|doc|docx|ps|bib|tex|csv|xlsx?|pptx?|png|jpg|jpeg|gif|svg)$',
    re.IGNORECASE,
)

# Research keyword must be the last meaningful path segment.
# Matches: /research, /research/, /publications, /papers.html, /working-papers/
# Rejects: /research/paper.pdf, /publications/224, /publication/lawyers/
_RESEARCH_LEAF_PATH = re.compile(
    r"""(?ix)
    /(?:
        research(?:[-_]?papers?)?
        | publications?
        | papers?
        | working[-_]?papers?
        | work[-_]?in[-_]?progress
        | selected[-_]?papers?
        | writings?
    )
    (?:\.html?)?    # optional .html extension
    /*              # optional trailing slash(es)
    $               # must be end of path
    """,
)

# Anchor text that unambiguously labels a research listing page
_RESEARCH_ANCHOR = re.compile(
    r"""(?ix)
    ^(?:
        research
        | publications?
        | papers?
        | working\s+papers?
        | work\s+in\s+progress
        | selected\s+papers?
        | my\s+(?:research|papers?|publications?)
        | research\s+(?:papers?|output|page)
        | published\s+(?:papers?|work)
    )$
    """,
)


# Multi-tenant platforms where different researchers live under the same hostname.
# For these, we compare path prefixes (not just hostname) to avoid cross-researcher links.
_MULTI_TENANT_DOMAINS = (
    'sites.google.com',
    'github.io',
    'wixsite.com',
    'wordpress.com',
    'weebly.com',
    'squarespace.com',
    'netlify.app',
    'vercel.app',
    'web.mit.edu',
    'campuspress.yale.edu',
)


def _get_site_root(url: str) -> tuple[str, str]:
    """Return (hostname_normalized, path_prefix) for same-site comparison.

    For multi-tenant platforms, the path prefix includes the user/site segment
    so that sites.google.com/view/alice != sites.google.com/view/bob.
    For regular domains, path_prefix is empty.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path

    for tenant_domain in _MULTI_TENANT_DOMAINS:
        if host == tenant_domain or host.endswith("." + tenant_domain):
            # Extract the user/site segment(s) as prefix
            # sites.google.com/view/USERNAME/... → /view/USERNAME
            # sites.google.com/site/USERNAME/... → /site/USERNAME
            # USERNAME.github.io/... → (host already unique, no prefix needed)
            if host != tenant_domain:
                # Subdomain-based (e.g. user.github.io) — host is already unique
                return host, ""
            segments = [s for s in path.split("/") if s]
            if len(segments) >= 2:
                return host, "/" + "/".join(segments[:2])
            return host, ""

    return host, ""


def _same_site(base_url: str, candidate_url: str) -> bool:
    """Check if candidate is on the same site as base.

    For multi-tenant platforms, also requires the same path prefix
    (so sites.google.com/view/alice and sites.google.com/view/bob are different sites).
    """
    base_host, base_prefix = _get_site_root(base_url)
    cand_host, cand_prefix = _get_site_root(candidate_url)

    if base_host != cand_host:
        return False

    if base_prefix and cand_prefix:
        return base_prefix.lower() == cand_prefix.lower()

    if base_prefix or cand_prefix:
        # One has a prefix, the other doesn't — different sites within the platform
        cand_path = urlparse(candidate_url).path
        if base_prefix:
            return cand_path.lower().startswith(base_prefix.lower() + "/") or cand_path.lower().rstrip("/") == base_prefix.lower()
        return False

    return True


def _is_research_page_link(href: str, anchor_text: str) -> bool:
    """True if the link points to a research listing page (not a file or deep sub-page)."""
    parsed = urlparse(href)
    path = parsed.path

    if _FILE_EXTENSIONS.search(path):
        return False

    if _RESEARCH_LEAF_PATH.search(path):
        return True

    # Anchor-text-based: only if path is short (nav link, not deep content link)
    segments = [s for s in path.split("/") if s]
    if len(segments) <= 4:
        cleaned = anchor_text.strip()
        if cleaned and _RESEARCH_ANCHOR.match(cleaned):
            return True

    return False


def _is_personal_domain(url: str) -> bool:
    """Heuristic: is this a personal website where the researcher owns the whole domain?

    Checks that the domain is NOT a known institutional/multi-user pattern.
    Personal: stefanie-stantcheva.com, econweb.ucsd.edu/~gdahl
    Institutional: maastrichtuniversity.nl/i.wilms, economics.mit.edu/people/acemoglu
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path

    # University/institutional domains are never personal
    institutional_patterns = (
        '.edu', '.ac.uk', '.ac.at', '.ac.jp', '.ac.nz', '.ac.za',
        'university', 'univ.', 'uni-', '.uni.',
        'bundesbank', 'ecb.europa', 'bankofcanada', 'newyorkfed', 'chicagofed',
        'repec.org', 'tse-fr.', 'nber.org',
    )
    for pattern in institutional_patterns:
        if pattern in host:
            # Exception: tilde paths (~/username) are personal faculty pages
            if "/~" in path:
                return True
            return False

    # If the source URL has 0 path segments (just domain root), it's personal
    segments = [s for s in path.split("/") if s]
    return len(segments) <= 1


def _shares_path_context(base_url: str, candidate_url: str) -> bool:
    """Check that the candidate URL is a child or sibling of the source URL.

    For personal domains (researcher owns the whole site), any same-site
    research page is valid. For institutional/multi-user sites, the candidate
    must share the source URL's path prefix.
    """
    if _is_personal_domain(base_url):
        return True

    bp = urlparse(base_url).path.rstrip("/")
    cp = urlparse(candidate_url).path.rstrip("/")

    base_segments = [s for s in bp.split("/") if s]
    cand_segments = [s for s in cp.split("/") if s]

    if not base_segments:
        # Institutional root (e.g. uts.edu.au) — nothing is in path context
        return False

    # Child: candidate path extends the base path
    if cp.startswith(bp + "/") or cp == bp:
        return True

    # Sibling: same parent directory (all but last segment match)
    # Only valid when there IS a parent (not at domain root)
    parent = base_segments[:-1]
    if parent and len(cand_segments) >= len(parent) and cand_segments[:len(parent)] == parent:
        return True

    return False


def find_research_links(html: str, base_url: str) -> list[str]:
    """Parse HTML and return deduplicated research page URLs on the same site."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = {}

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        absolute = urljoin(base_url, href)
        anchor = a.get_text(strip=True)

        if not _same_site(base_url, absolute):
            continue

        if not _is_research_page_link(absolute, anchor):
            continue

        if not _shares_path_context(base_url, absolute):
            continue

        parsed = urlparse(absolute)
        normalized = parsed._replace(fragment="", query="").geturl().rstrip("/")

        base_normalized = urlparse(base_url)._replace(fragment="", query="").geturl().rstrip("/")
        if normalized == base_normalized:
            continue

        if normalized not in candidates:
            candidates[normalized] = absolute

    return list(candidates.values())


def validate_url(url: str, timeout: int = 10) -> bool:
    """HEAD-request a URL to check it returns 200-399."""
    try:
        resp = requests.head(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            },
        )
        return resp.status_code < 400
    except (requests.RequestException, Exception):
        return False


PAGE_SIZE = 2000


def _get_arg(name: str, default: str | None = None) -> str | None:
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    dry_run = "--dry-run" in sys.argv
    skip_validation = "--skip-validation" in sys.argv

    # Paginated scan — fetch URL metadata in pages to avoid OOM
    logger.info("Scanning stored HTML for research page links...")

    total_row = fetch_one(
        "SELECT COUNT(*) AS cnt FROM researcher_urls WHERE is_active = TRUE"
    )
    total = total_row["cnt"]
    logger.info("  %d active URLs to scan", total)

    # Build existing URL set — paginated too
    existing_urls: dict[int, set[str]] = {}
    offset = 0
    while offset < total:
        chunk = fetch_all(
            "SELECT researcher_id, url FROM researcher_urls WHERE is_active = TRUE ORDER BY id LIMIT %s OFFSET %s",
            (PAGE_SIZE, offset),
        )
        for row in chunk:
            rid = row["researcher_id"]
            normalized = urlparse(row["url"])._replace(fragment="", query="").geturl().rstrip("/")
            existing_urls.setdefault(rid, set()).add(normalized)
        offset += PAGE_SIZE
        gc.collect()

    logger.info("  Built existing URL index (%d researchers)", len(existing_urls))

    # Scan pages in chunks
    added = 0
    skipped = 0
    scanned = 0
    offset = 0

    while offset < total:
        url_rows = fetch_all("""
            SELECT ru.id AS url_id, ru.researcher_id, ru.url,
                   r.first_name, r.last_name
            FROM researcher_urls ru
            JOIN researchers r ON r.id = ru.researcher_id
            WHERE ru.is_active = TRUE
            ORDER BY ru.id
            LIMIT %s OFFSET %s
        """, (PAGE_SIZE, offset))

        for row in url_rows:
            url_id = row["url_id"]
            html_row = fetch_one(
                "SELECT raw_html FROM html_content WHERE url_id = %s", (url_id,)
            )
            if not html_row or not html_row["raw_html"]:
                continue

            url = row["url"]
            rid = row["researcher_id"]
            name = f"{row['first_name']} {row['last_name']}"

            candidates = find_research_links(html_row["raw_html"], url)
            del html_row

            for candidate in candidates:
                normalized = urlparse(candidate)._replace(fragment="", query="").geturl().rstrip("/")
                if normalized in existing_urls.get(rid, set()):
                    skipped += 1
                    continue

                if dry_run:
                    logger.info("  WOULD ADD [%d] %s → %s", rid, name, candidate)
                elif skip_validation or validate_url(candidate):
                    add_researcher_url(rid, "RESEARCH", candidate)
                    logger.info("  ADDED [%d] %s → %s", rid, name, candidate)
                    added += 1
                    if not skip_validation:
                        time.sleep(0.2)
                else:
                    logger.info("  INVALID %s → %s", name, candidate)

                existing_urls.setdefault(rid, set()).add(normalized)

        scanned += len(url_rows)
        offset += PAGE_SIZE
        gc.collect()
        logger.info("  ... scanned %d / %d URLs, %d added so far", scanned, total, added)

    logger.info(
        "\n%s: %d added, %d already existed",
        "DRY RUN" if dry_run else "DONE",
        added,
        skipped,
    )


if __name__ == "__main__":
    main()
