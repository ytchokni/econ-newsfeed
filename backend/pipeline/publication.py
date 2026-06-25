from dataclasses import dataclass
from backend.database import log_llm_usage
from bs4 import BeautifulSoup
from backend.llm.client import get_model, extract_json
from pydantic import BaseModel, field_validator
from typing import Literal, Optional
import difflib
import re
import logging
import os
from urllib.parse import urlparse

CONTENT_MAX_CHARS = int(os.environ['CONTENT_MAX_CHARS'])


@dataclass
class ExtractionLLMResult:
    """Result of an LLM extraction call.

    pubs is None on failure, [] on success with no publications.
    retry_after is set when the failure was a rate limit.
    """
    pubs: list[dict] | None
    retry_after: float | None = None


VALID_STATUSES = frozenset({
    'published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper',
    'work_in_progress',
})

_VALID_STATUSES = Literal[
    'published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper',
    'work_in_progress',
]


def _coerce_year(v: object) -> str | None:
    if v is None:
        return v
    s = str(v).strip()
    if not s:
        return None
    m = re.search(r'(19|20)\d{2}', s)
    if m:
        return m.group(0)
    return s[:4]


def _validate_url_scheme(v: object) -> str | None:
    if v is None:
        return v
    try:
        parsed = urlparse(str(v))
    except Exception:
        return None
    if parsed.scheme not in ('http', 'https'):
        return None
    return v


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
        return _coerce_year(v)

    @field_validator('draft_url', mode='before')
    @classmethod
    def validate_draft_url(cls, v: object) -> str | None:
        return _validate_url_scheme(v)


class PublicationExtractionList(BaseModel):
    """Wrapper for structured output — OpenAI requires a top-level object."""
    publications: list[PublicationExtraction]


class PublicationChange(BaseModel):
    """A single detected change on a researcher's publication page."""
    change_type: Literal["new_paper", "status_change", "title_change", "removed"]
    title: str
    authors: list[list[str]]
    year: Optional[str] = None
    venue: Optional[str] = None
    status: Optional[_VALID_STATUSES] = None
    draft_url: Optional[str] = None
    abstract: Optional[str] = None
    old_status: Optional[str] = None
    old_title: Optional[str] = None

    @field_validator('year', mode='before')
    @classmethod
    def coerce_year_to_str(cls, v: object) -> str | None:
        return _coerce_year(v)

    @field_validator('draft_url', mode='before')
    @classmethod
    def validate_draft_url(cls, v: object) -> str | None:
        return _validate_url_scheme(v)


class PublicationChangeList(BaseModel):
    """Structured output for diff-based extraction."""
    changes: list[PublicationChange]


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


def _normalize_for_token_match(text: str) -> str:
    from backend.pipeline.feed_events import _normalize_for_matching
    return _normalize_for_matching(text)


def verify_title_in_html(title: str, html_text: str, *, _norm_html: str | None = None) -> bool:
    """Return True if the extracted title plausibly appears in the source HTML.

    Pass _norm_html to avoid re-normalizing the same HTML for every title on a page.
    """
    norm_title = _normalize_for_token_match(title)
    norm_html = _norm_html if _norm_html is not None else _normalize_for_token_match(html_text)

    if not norm_title:
        return False

    if norm_title in norm_html:
        return True

    title_tokens = [t for t in norm_title.split() if len(t) >= 4]

    if len(title_tokens) <= 3:
        return False

    html_all_tokens = norm_html.split()
    window_size = len(title_tokens) + 1

    best_ratio = 0.0
    for i in range(max(0, len(html_all_tokens) - window_size + 1)):
        window = set(html_all_tokens[i:i + window_size])
        matches = sum(1 for t in title_tokens if t in window)
        ratio = matches / len(title_tokens)
        if ratio > best_ratio:
            best_ratio = ratio

    return best_ratio >= 0.80


_CHANGE_OUTPUT_FIELDS = """\
For each change, provide:
- change_type: one of "new_paper", "status_change", "title_change", "removed"
- title: the publication title (current/new version), copied verbatim from the page
- authors: a list of [first_name, last_name] pairs
- year, venue, status, draft_url, abstract: from the NEW version (or OLD version for "removed")
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", "work_in_progress", or null. Use "work_in_progress" ONLY for early-stage research under explicit "Work in Progress" / "Research in Progress" sections without a working paper series. If a paper has a working paper series (e.g. "NBER WP", "CEPR Discussion Paper"), use "working_paper" regardless of section. Default to "working_paper" for unpublished papers when neither signal is present.
- old_status: previous status (for status_change only)
- old_title: previous title (for title_change only)"""


class Publication:
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
- title: the full publication title, copied EXACTLY as it appears on the page. Do not add subtitles, correct spelling, or reword. Do not use your prior knowledge of the paper — only extract what is written in the content below.
- authors: a list of [first_name, last_name] pairs. Use full first names when available (e.g., "John" not "J."). If only an initial appears, use it as given.
- year: the year associated with this paper as a 4-digit string. Use the publication year, working paper release year, revision date, or most recent year shown near the paper entry. Only return null if no year appears anywhere near the paper entry.
- venue: journal or conference name, or null if unknown
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", "work_in_progress", or null if unknown. Determine status using these rules in priority order:
  1. Paper-level labels FIRST (e.g., "Forthcoming" means "accepted", NOT "published"; "NBER Working Paper No. 12345" or "HBS Working Paper" means "working_paper").
  2. Section headers as FALLBACK if no paper-level label exists: "Working Papers" or "Discussion Papers" → "working_paper". "Work in Progress", "Works in Progress", "Research in Progress", "Selected Work in Progress" → "work_in_progress".
  3. When NEITHER signal is present (no working paper series AND no clear section header), default to "working_paper" for unpublished papers.
  Use "work_in_progress" ONLY for early-stage research that lacks a formal working paper series affiliation — papers listed under explicit "Work in Progress" sections, typically without a working paper number or institutional series. Use "reject_and_resubmit" ONLY if the page explicitly mentions rejection or R&R for this specific paper.
- draft_url: a URL to a PDF, SSRN, NBER, or working paper version, or null if not available
- abstract: the paper abstract, or null if not shown on the page

IMPORTANT: Extract titles VERBATIM from the page content. Do not use your knowledge of papers to add subtitles, correct titles, or fill in information not present on the page. If no publications are found, return an empty list.

Content:
{text_content[:CONTENT_MAX_CHARS]}"""

    _SEGMENT_RE = re.compile(
        r'(?<=[.!?])\s+(?=[A-Z])'   # sentence boundary
        r'|(?<=\n)'                  # existing newline
        r'|(?<=\s)(?=(?:'            # before common section headers
        r'Abstract|Working [Pp]aper|Publications?|Research'
        r'|Forthcoming|Accepted|References|Slides|Codes'
        r')\b)',
    )

    @staticmethod
    def _segment_text(text: str) -> list[str]:
        """Split page text into diff-friendly segments.

        Stored page text is often a single long line. Splitting on sentence
        boundaries produces meaningful chunks so difflib can isolate the
        actual change instead of replacing the entire line.
        """
        parts = Publication._SEGMENT_RE.split(text)
        return [p + '\n' for p in parts if p]

    @staticmethod
    def _compute_compact_diff(old_text: str, new_text: str, context_lines: int = 10) -> str | None:
        """Compute a unified diff between old and new text.

        Returns the diff string, or None if the diff is larger than the new
        text alone (meaning the page was substantially rewritten and sending
        the full texts would be more token-efficient).

        Returns "" (empty string) when the texts are identical.
        """
        old_lines = Publication._segment_text(old_text)
        new_lines = Publication._segment_text(new_text)

        # Scale context down for short pages so it doesn't swallow the
        # whole document. For a 20-segment page, n=10 means 100% context.
        n_segments = max(len(old_lines), len(new_lines))
        ctx = min(context_lines, max(3, n_segments // 4))

        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile='OLD', tofile='NEW',
            n=ctx,
        ))

        if not diff_lines:
            return ""

        diff_text = "".join(diff_lines)

        # Fall back to full prompt if the diff is larger than the new text
        if len(diff_text) > len(new_text):
            return None

        return diff_text

    @staticmethod
    def _build_compact_diff_prompt(diff_text: str, url: str) -> str:
        """Build the LLM prompt using a compact unified diff."""
        return f"""I have a unified diff of changes to a researcher's publication page at {url}.

The diff is in unified diff format:
- Lines starting with "---" and "+++" are file headers (OLD and NEW versions)
- Lines starting with "@@" indicate the position of each change hunk
- Lines starting with "-" were REMOVED from the old version
- Lines starting with "+" were ADDED in the new version
- Lines with no prefix are unchanged context lines

Analyze this diff and identify ALL changes to academic publications:

1. **new_paper**: A publication that appears in added lines but not in removed/context lines.
2. **status_change**: A publication whose status or section changed (e.g. moved from "Working Papers" to "Publications", or gained "forthcoming"/"accepted"). Set old_status to the previous status.
3. **title_change**: A publication whose title was modified. Set old_title to the previous title.
4. **removed**: A publication that appears only in removed lines and is not present in added/context lines.

If a paper moved sections (e.g. "Working Papers" to "Refereed Publications", or "Work in Progress" to "Working Papers"), that is a status_change, NOT a new_paper.

IMPORTANT: Ignore changes to navigation, dates, layout, boilerplate, or non-publication content. Only report changes that affect academic publications.

Do NOT report publications that are unchanged (appearing only in context lines).

DIFF:
{diff_text[:CONTENT_MAX_CHARS]}

{_CHANGE_OUTPUT_FIELDS}

If no publication changes were detected, return an empty changes list."""

    @staticmethod
    def _build_full_diff_prompt(old_text: str, new_text: str, url: str) -> str:
        """Build the full old+new LLM prompt (fallback for large rewrites)."""
        return f"""I have two versions of a researcher's publication page at {url}.
Compare the OLD and NEW versions and identify ALL changes to publications:

1. **new_paper**: A publication that appears in NEW but is completely absent from OLD.
2. **status_change**: A publication that existed in OLD but moved sections (e.g. from "Working Papers" to "Publications", or "Work in Progress" to "Working Papers"), or gained a venue/status it didn't have before. Set old_status to the previous status. IMPORTANT: Determine status from paper-level labels FIRST (e.g., "Forthcoming" means "accepted", NOT "published"; a working paper series means "working_paper"). Only fall back to section headers if no paper-level label exists.
3. **title_change**: A publication whose title was modified. Set old_title to the previous title. Only report if the core title text actually changed — ignore differences in capitalization, punctuation, or bracketed annotations.
4. **removed**: A publication that was in OLD but is completely absent from NEW.

If a paper moved from "Working Papers" to "Refereed Publications", or from "Work in Progress" to "Working Papers", or gained "forthcoming"/"accepted", that is a status_change, NOT a new_paper.

IMPORTANT: Copy all titles EXACTLY as they appear on the page — verbatim. Do not use your prior knowledge of papers to add subtitles, correct titles, or fill in information not present. Do NOT report publications that are identical in both versions.

Use "reject_and_resubmit" ONLY if the page explicitly mentions rejection or R&R for the specific paper.

OLD VERSION:
{old_text[:CONTENT_MAX_CHARS]}

NEW VERSION:
{new_text[:CONTENT_MAX_CHARS]}

{_CHANGE_OUTPUT_FIELDS}

If no changes were detected, return an empty changes list."""

    @staticmethod
    def build_diff_extraction_prompt(old_text: str, new_text: str, url: str) -> str:
        """Build the LLM prompt for diff-based change detection.

        Tries a compact unified diff first (much smaller token footprint).
        Falls back to the full old+new prompt when the diff is larger than
        the new text (i.e. the page was substantially rewritten).
        """
        diff_text = Publication._compute_compact_diff(old_text, new_text)

        if diff_text is not None:
            if diff_text == "":
                # Texts are identical — tell the LLM there are no changes
                return (
                    f"The researcher's publication page at {url} has not changed. "
                    "The old and new versions are identical. "
                    "Return an empty changes list."
                )
            return Publication._build_compact_diff_prompt(diff_text, url)

        return Publication._build_full_diff_prompt(old_text, new_text, url)

    @staticmethod
    def try_extract_changes(old_text: str, new_text: str, url: str,
                            scrape_log_id: int | None = None) -> ExtractionLLMResult:
        """Detect publication changes by comparing old and new page text.

        Returns ExtractionLLMResult with pubs=None on LLM failure, pubs=[]
        when no changes detected. Each dict has a 'change_type' key.
        """
        prompt = Publication.build_diff_extraction_prompt(old_text, new_text, url)
        model = get_model()
        logging.info("Diff-extracting changes from %s using LLM (%s)", url, model)

        result = extract_json(prompt, PublicationChangeList)

        if result.usage is not None:
            log_llm_usage(
                "diff_extraction", model, result.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )

        if result.parsed is None:
            logging.warning("Diff extraction returned no parsed result for %s", url)
            return ExtractionLLMResult(pubs=None, retry_after=result.retry_after)

        validated = []
        norm_html = _normalize_for_token_match(new_text)
        for change in result.parsed.changes:
            d = change.model_dump()
            if d['change_type'] == 'removed':
                validated.append(d)
            elif validate_publication(d):
                if not verify_title_in_html(d['title'], new_text, _norm_html=norm_html):
                    logging.info("HTML verification dropped change: '%s'",
                                 d.get('title', '<no title>')[:80])
                    continue
                validated.append(d)
            else:
                logging.info("Validation dropped change: %s", d.get('title', '<no title>'))
        return ExtractionLLMResult(pubs=validated)

    @staticmethod
    def try_extract_publications(text_content: str, url: str, scrape_log_id: int | None = None) -> ExtractionLLMResult:
        """Extract publications via the LLM.

        Returns ExtractionLLMResult with pubs=None on LLM failure, pubs=[]
        when the page genuinely has no publications.
        """
        prompt = Publication.build_extraction_prompt(text_content, url)
        model = get_model()
        logging.info(f"Extracting publications from {url} using LLM ({model})")

        result = extract_json(prompt, PublicationExtractionList, max_tokens=32000)

        if result.usage is not None:
            log_llm_usage(
                "publication_extraction", model, result.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )

        if result.parsed is None:
            logging.warning(f"Publication extraction returned no parsed result for {url}")
            return ExtractionLLMResult(pubs=None, retry_after=result.retry_after)

        validated = []
        norm_html = _normalize_for_token_match(text_content)
        for pub in result.parsed.publications:
            d = pub.model_dump()
            if not validate_publication(d):
                logging.info(f"Validation dropped: {d.get('title', '<no title>')}")
                continue
            if not verify_title_in_html(d['title'], text_content, _norm_html=norm_html):
                logging.info("HTML verification dropped: '%s' (not found in source)",
                             d.get('title', '<no title>')[:80])
                continue
            validated.append(d)
        return ExtractionLLMResult(pubs=validated)

    @staticmethod
    def extract_publications(text_content: str, url: str, scrape_log_id: int | None = None) -> list[dict]:
        """Legacy wrapper: failure collapses to [] (kept for scheduler/batch callers)."""
        result = Publication.try_extract_publications(text_content, url, scrape_log_id=scrape_log_id)
        return result.pubs if result.pubs is not None else []

