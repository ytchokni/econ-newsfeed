"""Extract paper-related links from researcher web pages and match them to papers.

Programmatic link extraction — no LLM involvement. Parses <a> tags from HTML,
filters to trusted academic domains, and matches links to papers by anchor text.
"""
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString

from database import Database
from html_fetcher import HTMLFetcher

# ---------------------------------------------------------------------------
# Trusted domains
# ---------------------------------------------------------------------------

TRUSTED_LINK_DOMAINS = (
    # Paper repositories
    'ssrn.com', 'nber.org', 'arxiv.org', 'doi.org',
    'ideas.repec.org', 'econpapers.repec.org', 'econstor.eu', 'cepr.org',
    # File hosting
    'drive.google.com', 'docs.google.com', 'dropbox.com',
    'files.cargocollective.com',
    # Major journal publishers
    'aeaweb.org', 'academic.oup.com', 'journals.uchicago.edu',
    'onlinelibrary.wiley.com', 'sciencedirect.com', 'link.springer.com',
    'rdcu.be',
    'tandfonline.com', 'taylorfrancis.com',
    'jstor.org', 'cambridge.org',
    'degruyterbrill.com', 'degruyter.com',
    'annualreviews.org',
    'journals.sagepub.com',
    'mohrsiebeck.com',
    'muse.jhu.edu',
    'izajoels.com',
    'direct.mit.edu',       # MIT Press (Review of Economics and Statistics)
    'econometricsociety.org',  # Econometrica
    'restud.com',           # Review of Economic Studies
    'iza.org',              # IZA Discussion Papers
    # Institutional working paper series
    'diw.de',               # DIW Berlin
    'ifo.de',               # ifo Institute
    'cesifo-group.de',      # CESifo
)

STOP_WORDS = frozenset({
    'a', 'an', 'the', 'of', 'and', 'in', 'on', 'to', 'for', 'is', 'are',
    'was', 'with', 'by', 'from', 'or', 'at', 'as', 'it', 'its', 'be',
    'do', 'does', 'did', 'not', 'no', 'but', 'if', 'so', 'can', 'how',
})

# Non-article paths on journal sites (topic pages, help pages, etc.)
_JOURNAL_NON_ARTICLE_PATTERNS = (
    '/topics/', '/about/', '/help/', '/search', '/browse/',
    '/login', '/register', '/cart', '/account',
)

# Generic anchor text patterns — trigger parent-text fallback
_GENERIC_ANCHOR_PATTERNS = re.compile(
    r'^(ssrn\s*(version)?|nber\s*working\s*paper|working\s*paper|'
    r'paper|pdf|draft|download|link|preprint|manuscript|'
    r'published\s*version|journal\s*version|accepted\s*version|'
    r'online\s*appendix|appendix|code|replication|data|'
    r'slides|presentation|abstract|bibtex|citation)$',
    re.IGNORECASE,
)

# Inline elements to skip during sibling traversal
_INLINE_TAGS = frozenset({
    'span', 'em', 'strong', 'b', 'i', 'u', 'small', 'sub', 'sup',
    'img', 'br', 'wbr', 'mark', 'abbr', 'code',
})

# Block-level elements for parent-text extraction
_BLOCK_TAGS = frozenset({
    'p', 'li', 'dd', 'td', 'th', 'blockquote', 'figcaption',
})


# ---------------------------------------------------------------------------
# Link classification
# ---------------------------------------------------------------------------

def classify_link_type(url):
    hostname = urlparse(url).hostname or ''
    if 'ssrn.com' in hostname: return 'ssrn'
    if 'nber.org' in hostname: return 'nber'
    if 'arxiv.org' in hostname: return 'arxiv'
    if 'doi.org' in hostname: return 'doi'
    if 'drive.google.com' in hostname or 'docs.google.com' in hostname: return 'drive'
    if 'dropbox.com' in hostname: return 'dropbox'
    # Repositories (not journals — don't apply non-article path filter)
    if any(d in hostname for d in ('repec.org', 'econstor.eu', 'cepr.org', 'iza.org', 'diw.de', 'ifo.de', 'cesifo-group.de')):
        return 'repository'
    return 'journal'


def is_trusted_domain(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        path = parsed.path.lower()
    except Exception:
        return False
    if not hostname:
        return False
    for domain in TRUSTED_LINK_DOMAINS:
        if hostname == domain or hostname.endswith('.' + domain):
            # Only filter non-article paths for journal publisher domains
            link_type = classify_link_type(url)
            if link_type == 'journal':
                if any(pat in path for pat in _JOURNAL_NON_ARTICLE_PATTERNS):
                    return False
            return True
    return False


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def _strip_to_alnum(text):
    """Strip to alphanumeric, handling accented characters via transliteration."""
    # Transliterate accents: é→e, ö→o, etc.
    normalized = unicodedata.normalize('NFKD', text)
    ascii_text = normalized.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]', '', ascii_text.lower())


def _meaningful_words(text):
    """Non-stop words with length > 1."""
    normalized = unicodedata.normalize('NFKD', text)
    ascii_text = normalized.encode('ascii', 'ignore').decode('ascii')
    return set(w for w in re.findall(r'\w+', ascii_text.lower()) if w not in STOP_WORDS and len(w) > 1)


# ---------------------------------------------------------------------------
# Anchor text resolution
# ---------------------------------------------------------------------------

def _is_generic_anchor(text):
    """Check if anchor text is a generic label rather than a paper title."""
    stripped = text.strip()
    if len(stripped) < 5:
        return True
    # Check against known generic patterns
    if _GENERIC_ANCHOR_PATTERNS.match(stripped):
        return True
    # Filenames ending in .pdf
    if stripped.lower().endswith('.pdf'):
        return True
    return False


def _get_sibling_anchor_text(a_tag):
    """Look at sibling <a> tags for a paper title.

    Traverses past inline elements (span, em, strong, etc.) to find
    an adjacent anchor with a paper-title-length text.
    """
    # Check previous siblings
    for sibling in a_tag.previous_siblings:
        if isinstance(sibling, NavigableString):
            continue  # skip text nodes
        name = getattr(sibling, 'name', None)
        if name == 'a':
            text = sibling.get_text(strip=True)
            if len(text) > 10 and not _is_generic_anchor(text):
                return text
            break
        if name in _INLINE_TAGS:
            continue  # skip inline wrappers
        break  # stop at block elements or unknown tags

    # Check next siblings
    for sibling in a_tag.next_siblings:
        if isinstance(sibling, NavigableString):
            continue
        name = getattr(sibling, 'name', None)
        if name == 'a':
            text = sibling.get_text(strip=True)
            if len(text) > 10 and not _is_generic_anchor(text):
                return text
            break
        if name in _INLINE_TAGS:
            continue
        break

    return ''


def _get_parent_title_text(a_tag):
    """Extract paper title from the nearest block-level parent element.

    Common pattern: <p>Paper Title [SSRN Version]</p>
    The paper title is the leading text before any bracketed links.
    """
    # Walk up to nearest block-level ancestor
    parent = a_tag.parent
    while parent and getattr(parent, 'name', None) not in _BLOCK_TAGS:
        parent = parent.parent
    if not parent:
        return ''

    # Get the full text, then extract the leading portion (before brackets/links)
    full_text = parent.get_text(separator=' ', strip=True)
    if not full_text or len(full_text) < 10:
        return ''

    # Strip common trailing patterns: [Abstract | PDF | SSRN], (with coauthor), etc.
    # Take text before first bracket or common separator
    # But be careful — paper titles can contain parentheses
    # Strategy: get text up to the first occurrence of common link labels
    label_markers = [
        '[ Abstract', '[Abstract', '[ Draft', '[Draft', '[ PDF', '[PDF',
        '[ SSRN', '[SSRN', '[ NBER', '[NBER', '[ Link', '[Link',
        '[ Working Paper', '[Working Paper',
    ]
    title_text = full_text
    for marker in label_markers:
        idx = full_text.find(marker)
        if idx > 10:  # must have meaningful text before the marker
            title_text = full_text[:idx]
            break

    # Clean up: strip trailing whitespace, commas, periods from coauthor lists
    title_text = title_text.strip().rstrip('.,;:')

    # If the text contains "(with " — strip the coauthor part
    with_idx = title_text.find('(with ')
    if with_idx > 10:
        title_text = title_text[:with_idx].strip().rstrip('.,;:')

    # Also handle "with Author Name" without parens at the end
    with_idx = title_text.rfind(' with ')
    if with_idx > len(title_text) * 0.5:  # only if "with" is in the latter half
        title_text = title_text[:with_idx].strip().rstrip('.,;:')

    return title_text if len(title_text) > 10 else ''


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

def extract_trusted_links(html):
    """Extract trusted-domain links with resolved anchor text.

    Resolution order for anchor text:
    1. The link's own anchor text (if it looks like a paper title)
    2. Adjacent sibling <a> tag's text (handles: title→local PDF, next→NBER)
    3. Parent block element's leading text (handles: <p>Title [SSRN Version]</p>)

    Collects ALL anchor texts per URL and keeps the best for matching.
    """
    soup = BeautifulSoup(html, 'html.parser')
    for el in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        el.decompose()

    # Collect all anchor texts per URL (fixes URL-dedup-picks-wrong-occurrence bug)
    url_anchors = {}  # url -> [(anchor_text, link_type), ...]
    for a in soup.find_all('a', href=True):
        url = a['href']
        if not url.startswith(('http://', 'https://')):
            continue
        if not is_trusted_domain(url):
            continue

        anchor = a.get_text(strip=True)
        link_type = classify_link_type(url)

        # Try resolution chain for generic/empty anchors
        if _is_generic_anchor(anchor):
            sibling_text = _get_sibling_anchor_text(a)
            if sibling_text:
                anchor = sibling_text
            else:
                parent_text = _get_parent_title_text(a)
                if parent_text:
                    anchor = parent_text

        url_anchors.setdefault(url, []).append((anchor, link_type))

    # For each URL, pick the best (longest non-generic) anchor text
    links = []
    for url, anchors in url_anchors.items():
        # Prefer non-generic anchors, then longest
        best_anchor = ''
        best_type = anchors[0][1]  # link_type is same for all occurrences
        for anchor, lt in anchors:
            if not _is_generic_anchor(anchor) and len(anchor) > len(best_anchor):
                best_anchor = anchor
                best_type = lt
        # If all anchors are generic, use the longest one anyway
        if not best_anchor:
            best_anchor = max((a for a, _ in anchors), key=len, default='')

        links.append({
            'url': url,
            'anchor_text': best_anchor,
            'link_type': best_type,
        })

    return links


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_link_to_paper(anchor_text, paper_titles, threshold=0.75):
    """Match a link's anchor text to a paper title. Zero false positives.

    Two strategies:
    1. Strip all whitespace/punctuation, check substring containment
       with length ratio > 0.75 (handles CSS spacing artifacts)
    2. Word-level bidirectional set overlap (handles normal text)

    Returns (best_title, score) or (None, 0.0).
    """
    if not anchor_text or len(anchor_text.strip()) < 5:
        return None, 0.0

    anchor_stripped = _strip_to_alnum(anchor_text)
    if len(anchor_stripped) < 5:
        return None, 0.0

    best_title, best_score = None, 0.0

    for title in paper_titles:
        title_stripped = _strip_to_alnum(title)
        if not title_stripped:
            continue

        # Strategy 1: stripped substring match (handles CSS concatenation)
        if title_stripped in anchor_stripped or anchor_stripped in title_stripped:
            ratio = min(len(anchor_stripped), len(title_stripped)) / max(len(anchor_stripped), len(title_stripped))
            if ratio > 0.75:  # raised from 0.5
                if 1.0 > best_score:
                    best_score, best_title = 1.0, title
                continue

        # Strategy 2: word-level bidirectional SET overlap (bug fix: was substring)
        title_words = _meaningful_words(title)
        anchor_words = _meaningful_words(anchor_text)
        if len(title_words) < 2 or len(anchor_words) < 2:
            continue

        overlap = title_words & anchor_words
        if not overlap:
            continue

        recall = len(overlap) / len(title_words)
        precision = len(overlap) / len(anchor_words)
        score = min(recall, precision)

        if score > best_score and score >= threshold:
            best_score, best_title = score, title

    return best_title, best_score


# ---------------------------------------------------------------------------
# Persist matched links
# ---------------------------------------------------------------------------

def match_and_save_paper_links(url_id, publications):
    """Match page links to papers by anchor text, save to paper_links.

    Called after save_publications(). Extracts trusted-domain links from
    stored raw HTML, matches each to the best paper by anchor text,
    and persists matches.
    """
    raw_html = HTMLFetcher.get_raw_html(url_id)
    if not raw_html:
        return

    page_links = extract_trusted_links(raw_html)
    if not page_links:
        return

    paper_ids = {}
    for pub in publications:
        title = (pub.get('title') or '').strip()
        if not title:
            continue
        title_hash = Database.compute_title_hash(title)
        row = Database.fetch_one("SELECT id FROM papers WHERE title_hash = %s", (title_hash,))
        if row:
            paper_ids[title] = row['id']

    if not paper_ids:
        return

    for link in page_links:
        matched_title, _ = match_link_to_paper(link['anchor_text'], list(paper_ids.keys()))
        if matched_title:
            try:
                Database.execute_query(
                    """INSERT IGNORE INTO paper_links (paper_id, url, link_type, discovered_at)
                       VALUES (%s, %s, %s, %s)""",
                    (paper_ids[matched_title], link['url'], link['link_type'],
                     datetime.now(timezone.utc)))
            except Exception as e:
                logging.warning("Error saving paper link: %s", e)


# ---------------------------------------------------------------------------
# Untrusted domain discovery
# ---------------------------------------------------------------------------

def discover_untrusted_domains(html, min_anchor_len=20):
    """Find untrusted domains with paper-title-length anchor text.

    Returns {domain: count} of domains that have links with anchor text
    long enough to be a paper title but are not in the trusted list.
    Useful for expanding the trusted domain list over time.
    """
    from collections import Counter
    soup = BeautifulSoup(html, 'html.parser')
    for el in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        el.decompose()

    domains = Counter()
    for a in soup.find_all('a', href=True):
        url = a['href']
        if not url.startswith(('http://', 'https://')):
            continue
        if is_trusted_domain(url):
            continue
        anchor = a.get_text(strip=True)
        if len(anchor) >= min_anchor_len:
            try:
                hostname = urlparse(url).hostname
                if hostname:
                    domains[hostname] += 1
            except Exception:
                pass
    return dict(domains)
