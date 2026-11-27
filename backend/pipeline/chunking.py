"""Split long researcher pages into extraction-sized chunks without losing text.

Pages whose text fits CONTENT_MAX_CHARS are sent to the LLM unchanged, in one
call. Longer pages used to be cut at that length, so papers at the bottom of
long publication lists never reached the extractor. Here they are split into
chunks of at most that length instead:

- Boundaries fall between HTML block elements (paragraphs, list items, table
  rows, headings, divs), never inside one, so an entry is not cut in half.
  A chunk closes early, once half full, before a publication-section title.
- A single block longer than the budget (e.g. a whole list in one <p> with
  <br> separators) is split between its lines, repeating the last lines of
  one piece at the start of the next so an entry cut at the seam is seen
  whole at least once. Duplicates are merged after extraction.
- Each chunk records the publication-section headings in effect where it
  starts ("Working Papers", "Work in Progress", ...), because the extraction
  prompt uses section headers to decide a paper's status.

Nothing is dropped apart from a page so long it needs more than
EXTRACTION_MAX_CHUNKS calls; that is logged.
"""
import logging
import os
import re
from dataclasses import dataclass

from backend.pipeline.html_fetcher import HTMLFetcher

logger = logging.getLogger(__name__)

EXTRACTION_MAX_CHUNKS = int(os.environ.get('EXTRACTION_MAX_CHUNKS', '12'))

_BLOCK_TAGS = (
    'address', 'article', 'aside', 'blockquote', 'dd', 'div', 'dl', 'dt',
    'figcaption', 'figure', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'main',
    'ol', 'p', 'pre', 'section', 'table', 'tbody', 'td', 'th', 'tr', 'ul',
)
_HEADING_LEVELS = {f'h{n}': n for n in range(1, 7)}
# A short block that reads like a section title but is not an <hN>, e.g. a
# bold "Working Papers" paragraph. It nests below every real heading.
_PSEUDO_HEADING_LEVEL = 7
_PSEUDO_HEADING_MAX_CHARS = 60

# Private-use code points: not whitespace (get_text(strip=True) would drop
# U+001E-style separators) and never present in real page text.
_BLOCK_MARK = ''
_HEADING_MARK = ''

_SECTION_RE = re.compile(
    r"""^(?:[\dIVX]+[.)]?\s+)?
    (?:(?:selected|recent|refereed|peer[- ]reviewed|published|journal|other|
          current|ongoing|older|policy|non[- ]refereed|academic)\s+)*
    (?:publications?|papers?|working\s+papers?|works?\s+in\s+progress|
       research(?:\s+in\s+progress|\s+papers|\s+projects)?|articles?|
       book\s+chapters?|books?|chapters|discussion\s+papers?|pre-?prints?|
       reports?|writings?|projects|job\s+market\s+paper|
       forthcoming(?:\s+papers)?|accepted(?:\s+papers)?|under\s+review|
       revise\s+(?:and|&)\s+resubmit|dormant(?:\s+papers)?|resting\s+papers)
    \s*:?$""",
    re.I | re.X,
)
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')
_SEAM_OVERLAP_LINES = 2
_SEAM_OVERLAP_MAX_CHARS = 1000
_SPLIT_AT_HEADING_FILL = 0.5


@dataclass
class Block:
    """A run of page text between block-element boundaries."""
    text: str
    lines: list[str]
    heading_level: int | None = None


@dataclass
class Chunk:
    """Text sent to one extraction call.

    section is the chain of publication-section headings in effect where the
    chunk starts (e.g. "Research > Working Papers"), or None.
    """
    text: str
    section: str | None = None


def is_section_title(text: str) -> bool:
    """True for a block that names a publication section, e.g. 'Working Papers'."""
    return len(text) <= _PSEUDO_HEADING_MAX_CHARS and bool(_SECTION_RE.match(text))


def html_to_blocks(raw_html: str) -> list[Block]:
    """Split a page's content text (as extract_text_content sees it) into blocks."""
    soup = HTMLFetcher.content_soup(raw_html)
    for element in soup.find_all(_BLOCK_TAGS):
        level = _HEADING_LEVELS.get(element.name)
        element.insert_before(_BLOCK_MARK + (f'{_HEADING_MARK}{level}' if level else ''))
        element.insert_after(_BLOCK_MARK)
    raw = soup.get_text(separator='\n', strip=True)

    blocks = []
    for segment in raw.split(_BLOCK_MARK):
        level = None
        if segment.startswith(_HEADING_MARK):
            level = int(segment[1])
            segment = segment[2:]
        lines = [HTMLFetcher.normalize_text(line) for line in segment.split('\n')]
        lines = [line for line in lines if line]
        if not lines:
            continue
        text = HTMLFetcher.normalize_text(' '.join(lines))
        if level is None and is_section_title(text):
            level = _PSEUDO_HEADING_LEVEL
        blocks.append(Block(text, lines, level))
    return blocks


def text_to_blocks(text: str) -> list[Block]:
    """Fallback for pages stored as text only: one block per sentence."""
    blocks = []
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if sentence:
            blocks.append(Block(sentence, [sentence]))
    return blocks


def _hard_split(line: str, budget: int) -> list[str]:
    """Split one over-long line at spaces, overlapping consecutive pieces."""
    overlap = min(_SEAM_OVERLAP_MAX_CHARS, budget // 4)
    pieces, start = [], 0
    while start < len(line):
        end = min(len(line), start + budget)
        if end < len(line):
            space = line.rfind(' ', start + budget // 2, end)
            if space > start:
                end = space
        pieces.append(line[start:end].strip())
        if end >= len(line):
            break
        start = max(start + 1, end - overlap)
        space = line.find(' ', start, end)
        if space != -1:
            start = space + 1
    return [p for p in pieces if p]


def _split_block(block: Block, budget: int) -> list[str]:
    """Split a block longer than the budget between its lines."""
    lines = []
    for line in block.lines:
        lines.extend(_hard_split(line, budget) if len(line) > budget else [line])

    pieces, current = [], []
    for line in lines:
        if current and len(' '.join(current + [line])) > budget:
            pieces.append(' '.join(current))
            carry = current[-_SEAM_OVERLAP_LINES:]
            while carry and (len(' '.join(carry)) > _SEAM_OVERLAP_MAX_CHARS
                             or len(' '.join(carry + [line])) > budget):
                carry = carry[1:]
            current = carry
        current.append(line)
    if current:
        pieces.append(' '.join(current))
    return pieces


def pack_blocks(blocks: list[Block], budget: int) -> list[Chunk]:
    """Pack blocks into chunks of at most budget characters, in page order."""
    chunks: list[Chunk] = []
    current: list[str] = []
    current_len = 0
    current_section: str | None = None
    sections: list[tuple[int, str]] = []  # (heading level, title), outermost first

    def section_at(level: int | None) -> str | None:
        chain = [title for lvl, title in sections if level is None or lvl < level]
        return ' > '.join(chain) or None

    for block in blocks:
        pieces = [block.text] if len(block.text) <= budget else _split_block(block, budget)
        for index, piece in enumerate(pieces):
            opens_section = index == 0 and block.heading_level is not None
            # Close early only before a publication-section title: other
            # headings are often paper titles, and splitting at each would
            # add calls without adding context.
            if current and (current_len + 1 + len(piece) > budget
                            or (opens_section and is_section_title(block.text)
                                and current_len >= budget * _SPLIT_AT_HEADING_FILL)):
                chunks.append(Chunk(' '.join(current), current_section))
                current, current_len = [], 0
            if not current:
                current_section = section_at(block.heading_level if opens_section else None)
            current.append(piece)
            current_len += len(piece) + (1 if current_len else 0)
        if block.heading_level is not None:
            sections = [(lvl, title) for lvl, title in sections if lvl < block.heading_level]
            if is_section_title(block.text):
                sections.append((block.heading_level, block.text))
    if current:
        chunks.append(Chunk(' '.join(current), current_section))
    return chunks


def chunk_page(text: str, raw_html: str | None, budget: int,
               max_chunks: int | None = None) -> list[Chunk]:
    """Return the extraction chunks for a page.

    text is the normalized page text the extractor would otherwise receive.
    Within the budget it is returned as the single chunk, unchanged.
    """
    if len(text) <= budget:
        return [Chunk(text)]
    blocks = html_to_blocks(raw_html) if raw_html else text_to_blocks(text)
    chunks = pack_blocks(blocks, budget) or [Chunk(text[:budget])]
    limit = EXTRACTION_MAX_CHUNKS if max_chunks is None else max_chunks
    if len(chunks) > limit:
        dropped = sum(len(c.text) for c in chunks[limit:])
        logger.warning("Page needs %d extraction chunks; extracting the first %d "
                       "(%d chars not extracted)", len(chunks), limit, dropped)
        chunks = chunks[:limit]
    return chunks
