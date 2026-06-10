from database import Database
from bs4 import BeautifulSoup
from llm_client import get_model, extract_json
from pydantic import BaseModel, field_validator
from typing import Literal, Optional
import re
import logging
import os
from urllib.parse import urlparse

CONTENT_MAX_CHARS = int(os.environ['CONTENT_MAX_CHARS'])

VALID_STATUSES = frozenset({
    'published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper',
})

_VALID_STATUSES = Literal[
    'published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper'
]


class PublicationExtraction(BaseModel):
    title: str
    authors: list[list[str]]  # [[first_name, last_name], ...]
    year: Optional[str] = None
    venue: Optional[str] = None
    status: Optional[_VALID_STATUSES] = None
    draft_url: Optional[str] = None
    abstract: Optional[str] = None

    @field_validator('year', mode='before')
    @classmethod
    def coerce_year_to_str(cls, v: object) -> str | None:
        if v is None:
            return v
        s = str(v).strip()
        if not s:
            return None
        # Extract first 4-digit year if present (handles "2024a", "2023-24", "forthcoming 2024")
        m = re.search(r'(19|20)\d{2}', s)
        if m:
            return m.group(0)
        # Fall back to truncating to 4 chars to satisfy VARCHAR(4)
        return s[:4]

    @field_validator('draft_url', mode='before')
    @classmethod
    def validate_draft_url(cls, v: object) -> str | None:
        if v is None:
            return v
        try:
            parsed = urlparse(str(v))
        except Exception:
            return None
        if parsed.scheme not in ('http', 'https'):
            return None
        return v


class PublicationExtractionList(BaseModel):
    """Wrapper for structured output — OpenAI requires a top-level object."""
    publications: list[PublicationExtraction]


# Words too common to count as author-title overlap
_STOPWORDS = frozenset({
    'a', 'an', 'the', 'of', 'on', 'in', 'and', 'or', 'for', 'to', 'at',
    'by', 'is', 'it', 'as', 'do', 'no', 'not', 'with', 'from', 'but',
})

# Multi-word title phrases indicating software, not academic papers.
_SOFTWARE_INDICATORS = (
    'python package', 'r package', 'npm package', 'pip install',
    'github repository', 'code repository', 'open source software',
)

# Website elements that LLMs sometimes extract as paper titles
_WEBSITE_NOISE = frozenset({
    'cv', 'feed', 'email', 'follow', 'sitemap', 'teaching', 'publications',
    'papers', 'research', 'home', 'contact', 'about', 'links', 'news',
    'jmp', 'bio', 'vita',
})

# Patterns that indicate an LLM hallucination or website snippet, not a paper
_GARBAGE_PATTERNS = (
    'no publications',
    'powered by',
    'welcome to my',
    'i will be on the job market',
    'i am a ',
    'i am an ',
    'my research interests',
    'site last updated',
    'academic webpage',
    'currently, i am',
)


_METADATA_KEYWORDS = (
    r'job\s+market\s+paper'
    r'|jmp'
    r'|working\s+paper'
    r'|work\s+in\s+progress'
    r'|under\s+review'
    r'|submitted'
    r'|forthcoming'
    r'|accepted'
    r'|draft'
    r'|new(?:!)?'
    r'|revised'
    r'|updated'
    r'|r\s*&\s*r'
)

_TITLE_METADATA_SUFFIXES = re.compile(
    r'\s*(?:--|—|–|―)\s*(?:' + _METADATA_KEYWORDS + r')\s*$',
    re.IGNORECASE,
)

_TITLE_BRACKET_SUFFIXES = re.compile(
    r'\s*[\[\(]\s*(?:' + _METADATA_KEYWORDS + r')\s*[\]\)]\s*$',
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    """Strip metadata annotations and ensure title starts with uppercase."""
    title = _TITLE_METADATA_SUFFIXES.sub('', title)
    title = _TITLE_BRACKET_SUFFIXES.sub('', title)
    title = title.strip()
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title


def validate_publication(pub: dict) -> bool:
    """Return False for garbage extractions that should be silently dropped."""
    title = pub.get('title', '')
    authors = pub.get('authors', [])
    draft_url = pub.get('draft_url') or ''

    title_lower = title.lower().strip()

    # Reject empty or very short titles (< 5 chars) unless they have venue/status
    if len(title_lower) < 5 and not pub.get('venue') and not pub.get('status'):
        return False

    # Reject titles that are website elements (exact match after stripping trailing period)
    if title_lower.rstrip('.') in _WEBSITE_NOISE:
        return False

    # Reject LLM hallucinations and website snippets
    if any(pattern in title_lower for pattern in _GARBAGE_PATTERNS):
        return False

    # Reject copyright notices
    if title_lower.startswith('©') or title_lower.startswith('(c)'):
        return False

    # Reject if any author has empty first name or initial-only last name
    for author in authors:
        if not author or len(author) < 2:
            continue
        first = author[0].strip() if author[0] else ""
        last = author[-1].strip() if author[-1] else ""
        if not first:
            return False
        if re.match(r'^[A-Za-z]\.?$', last):
            return False

    # Reject GitHub as venue
    venue = (pub.get('venue') or '').lower()
    if 'github' in venue:
        return False

    # GitHub exclusion: reject draft URLs pointing to github.com (not github.io)
    if 'github.com' in draft_url.lower() and 'github.io' not in draft_url.lower():
        return False

    # Software/package title indicators
    if any(indicator in title_lower for indicator in _SOFTWARE_INDICATORS):
        return False

    # Collect last names (stripped, lowered)
    last_names = []
    for author in authors:
        if not author:
            continue
        last = author[-1].strip().lower() if author else ''
        if last:
            last_names.append(last)

    # Minimum author quality: reject if ALL last names are < 2 chars
    if last_names and all(len(ln) < 2 for ln in last_names):
        return False

    # Author-title overlap: reject if 2+ non-stopword last names appear in the title
    title_words = set(re.sub(r'[^a-z\s]', '', title_lower).split())
    overlap_count = sum(
        1 for ln in last_names
        if ln in title_words and ln not in _STOPWORDS and len(ln) >= 2
    )
    if overlap_count >= 2:
        return False

    return True


class Publication:
    @staticmethod
    def save_publications(
        url: str,
        publications: list[dict],
        is_seed: bool = False,
        event_date=None,
    ) -> None:
        """Save extracted publications. Delegates to PaperSaver + FeedEventEmitter."""
        from paper_saver import PaperSaver
        from feed_events import FeedEventEmitter
        results = PaperSaver.save_publications(url, publications, is_seed=is_seed)
        FeedEventEmitter.emit_new_paper_events(results, url, is_seed=is_seed, event_date=event_date)

    @staticmethod
    def extract_relevant_html(html_content: str) -> str:
        """Extract the relevant parts of the HTML, preserving hyperlinks as inline text.

        Converts <a href="URL">text</a> to 'text (URL)' before stripping HTML,
        so the LLM can see draft/paper URLs that would otherwise be lost.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        for element in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            element.decompose()
        # Inline hyperlinks before stripping tags
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if href and href.startswith(('http://', 'https://')):
                a.replace_with(f"{text} ({href})" if text else href)
        main_content = soup.body
        return main_content.get_text(separator='\n', strip=True)

    @staticmethod
    def build_extraction_prompt(text_content: str, url: str) -> str:
        """Build the LLM prompt for publication extraction."""
        return f"""Extract all academic publications from the following researcher page content from {url}.

For each publication, extract:
- title: the full publication title
- authors: a list of [first_name, last_name] pairs. Use full first names when available (e.g., "John" not "J."). If only an initial appears, use it as given.
- year: the year associated with this paper as a 4-digit string. Use the publication year, working paper release year, revision date, or most recent year shown near the paper entry. Only return null if no year appears anywhere near the paper entry.
- venue: journal or conference name, or null if unknown
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", or null if unknown
- draft_url: a URL to a PDF, SSRN, NBER, or working paper version, or null if not available
- abstract: the paper abstract, or null if not shown on the page

If no publications are found in the content, return an empty list. Do not fabricate publications.

Content:
{text_content[:CONTENT_MAX_CHARS]}"""

    @staticmethod
    def try_extract_publications(text_content: str, url: str, scrape_log_id: int | None = None) -> list[dict] | None:
        """Extract publications via the LLM.

        Returns None when the LLM call failed (API error, schema validation
        failure after retries) — callers should retry the URL later. Returns
        [] when the call succeeded but the page genuinely has no publications.
        """
        prompt = Publication.build_extraction_prompt(text_content, url)
        model = get_model()
        logging.info(f"Extracting publications from {url} using LLM ({model})")

        result = extract_json(prompt, PublicationExtractionList)

        if result.usage is not None:
            Database.log_llm_usage(
                "publication_extraction", model, result.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )

        if result.parsed is None:
            logging.warning(f"Publication extraction returned no parsed result for {url}")
            return None

        validated = []
        for pub in result.parsed.publications:
            d = pub.model_dump()
            if validate_publication(d):
                validated.append(d)
            else:
                logging.info(f"Validation dropped: {d.get('title', '<no title>')}")
        return validated

    @staticmethod
    def extract_publications(text_content: str, url: str, scrape_log_id: int | None = None) -> list[dict]:
        """Legacy wrapper: failure collapses to [] (kept for scheduler/batch callers)."""
        pubs = Publication.try_extract_publications(text_content, url, scrape_log_id=scrape_log_id)
        return pubs if pubs is not None else []



def append_snapshots_for_pubs(pubs: list[dict], source_url: str, event_date=None) -> None:
    """Append paper snapshots for a batch of extracted publications.

    Resolves title_hash → paper_id in a single query, then appends
    a snapshot for each matched paper. Emits status_change events
    via FeedEventEmitter when status changes are detected.
    """
    from feed_events import FeedEventEmitter

    hash_to_pub = {}
    for pub in pubs:
        title = pub.get('title')
        if title:
            hash_to_pub[Database.compute_title_hash(title)] = pub
    if not hash_to_pub:
        return

    placeholders = ",".join(["%s"] * len(hash_to_pub))
    rows = Database.fetch_all(
        f"SELECT id, title_hash FROM papers WHERE title_hash IN ({placeholders})",
        list(hash_to_pub.keys()),
    )
    for row in rows:
        pub = hash_to_pub[row['title_hash']]
        result = Database.append_paper_snapshot(
            paper_id=row['id'],
            status=pub.get('status'),
            venue=pub.get('venue'),
            abstract=pub.get('abstract'),
            draft_url=pub.get('draft_url'),
            year=pub.get('year'),
            source_url=source_url,
            title=pub.get('title'),
        )
        if result.status_changed:
            FeedEventEmitter.emit_status_change(row['id'], result.old_status, result.new_status, event_date=event_date)


def reconcile_title_renames(source_url: str, extracted_pubs: list[dict], event_date=None) -> None:
    """Detect title renames. Delegates to PaperSaver + FeedEventEmitter."""
    from paper_saver import PaperSaver
    from feed_events import FeedEventEmitter
    renames = PaperSaver.reconcile_title_renames(source_url, extracted_pubs)
    for r in renames:
        FeedEventEmitter.emit_title_change(r.paper_id, r.old_title, r.new_title, event_date=event_date)