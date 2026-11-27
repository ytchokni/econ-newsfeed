"""Researcher bio extraction and validation.

The bio is a single free-text field, but the free-tier Gemma models write
their reasoning ("<thought>* Source: ... * Task: Extract a professional
description ...") before the answer. Stored unchanged, that reasoning
replaced 3,502 bios on prod (issue #258). The call now asks for a one-field
JSON object, and every result passes `clean_description()`, which strips
thought blocks and rejects anything that still reads like reasoning. A
rejected result returns None, and callers keep the existing bio.

`looks_like_reasoning()` is also used by the cleanup script and the
data-quality invariant, so the three agree on what a polluted bio is.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.database.llm import log_llm_usage

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_WORDS = 200
MIN_DESCRIPTION_WORDS = 5
# Room for a reasoning preamble before the JSON object plus a 200-word bio.
_MAX_TOKENS = 4096

# Closed reasoning blocks: <thought>…</thought>, <think>…</think>, <thinking>…</thinking>.
_THOUGHT_BLOCK_RE = re.compile(
    r"<(thought|think|thinking|reasoning)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Any leftover tag means the block was cut off or malformed.
_THOUGHT_TAG_RE = re.compile(r"</?\s*(thought|think|thinking|reasoning)\b", re.IGNORECASE)

# Phrases that appear in the model's planning notes but never in a bio.
# Matched case-insensitively, on word boundaries where the phrase starts or
# ends with a letter, so "Delhi need to" does not match "i need to".
REASONING_MARKERS = (
    "<thought",
    "</thought",
    "<think",
    "</think",
    "* task:",
    "* source:",
    "* input:",
    "* goal:",
    "* constraint",
    "* output",
    "* format:",
    "* requirements:",
    "requirements:",
    "constraints:",
    "output format",
    "word count:",
    "draft 1",
    "draft:",
    "self-correction",
    "extract a professional description",
    "professional description (",
    "max 200 words",
    "up to 200 words",
    "the user wants",
    "the user is asking",
    "the prompt",
    "the provided text",
    "the text provided",
    "the homepage text",
    "let me",
    "i need to",
    "i will extract",
    "i'll extract",
    "reply with exactly",
    "return only the description",
)


def _marker_pattern(marker: str) -> str:
    head = r"\b" if marker[0].isalnum() else ""
    tail = r"\b" if marker[-1].isalnum() else ""
    return head + re.escape(marker) + tail


_MARKER_RE = re.compile("|".join(_marker_pattern(m) for m in REASONING_MARKERS), re.IGNORECASE)
# A line that starts like a planning bullet: "* Task:", "- Source:", "1. Goal:".
_BULLET_LABEL_RE = re.compile(r"(?m)^\s*(?:[*\-•]|\d+\.)\s+\**[A-Z][A-Za-z ]{1,30}\**:\s")

_NULL_ANSWERS = {"", "null", "none", "n/a", "na", "unknown"}


class ResearcherDescription(BaseModel):
    """Structured answer for the bio call."""
    description: str | None = Field(
        default=None,
        description="Third-person bio, at most 200 words, or null if the page has none.",
    )


def strip_thought_blocks(text: str) -> str:
    """Remove closed <thought>/<think> blocks and surrounding whitespace."""
    return _THOUGHT_BLOCK_RE.sub(" ", text).strip()


def looks_like_reasoning(text: str | None) -> bool:
    """True if `text` contains the model's reasoning rather than a bio."""
    if not text:
        return False
    if _MARKER_RE.search(text):
        return True
    return bool(_BULLET_LABEL_RE.search(text))


# Words ending in "." that do not end a sentence.
_ABBREVIATIONS = frozenset({
    "dr", "mr", "mrs", "ms", "prof", "st", "jr", "sr", "inc", "co", "corp", "ltd",
    "vs", "etc", "e.g", "i.e", "dept", "univ", "no", "vol", "approx", "assoc", "asst",
})
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s+[\"'(\[]?[A-Z0-9])")


def _is_abbreviation(word: str) -> bool:
    word = word.lstrip("(\"'")
    return len(word) <= 1 or "." in word or word.lower() in _ABBREVIATIONS


def _ends_sentence(text: str) -> bool:
    """True if `text` ends with sentence punctuation that is not an abbreviation."""
    if text[-1:] in "!?":
        return True
    if text[-1:] != ".":
        return False
    return not _is_abbreviation(text[:-1].rsplit(None, 1)[-1] if text[:-1].strip() else "")


def _last_sentence_end(text: str) -> int:
    """Index of the last sentence-ending punctuation followed by more text, or -1.

    A "." after an initial ("John L."), an abbreviation ("Prof.") or a dotted
    token ("Ph.D.", "U.S.") does not end a sentence.
    """
    for m in reversed(list(_SENTENCE_END_RE.finditer(text))):
        if m.group() != ".":
            return m.start()
        word = text[: m.start()].rsplit(None, 1)[-1] if text[: m.start()].strip() else ""
        if _is_abbreviation(word):
            continue
        return m.start()
    return -1


def _truncate_words(text: str, max_words: int = MAX_DESCRIPTION_WORDS) -> str:
    """Cap at `max_words`, ending on the last full sentence when one fits."""
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    clipped = " ".join(words[:max_words])
    end = _last_sentence_end(clipped)
    if clipped[-1:] in ".!?":
        return clipped
    if end > len(clipped) // 2:
        return clipped[: end + 1]
    return clipped


def clean_description(raw: str | None) -> str | None:
    """Return a storable bio, or None if `raw` is empty or not a bio.

    Strips closed thought blocks first, so "<thought>…</thought> Jane Doe
    is…" keeps the answer. Anything still showing reasoning — an unclosed
    tag, planning bullets, prompt echoes — is rejected outright rather than
    trimmed, because a partial strip can still leave notes in the bio.
    """
    if raw is None:
        return None
    text = strip_thought_blocks(str(raw))
    text = text.strip().strip('"').strip()
    if text.lower().rstrip(".") in _NULL_ANSWERS:
        return None
    if _THOUGHT_TAG_RE.search(text) or looks_like_reasoning(text):
        return None
    if text.startswith(("{", "[", "```")):
        return None
    if len(text.split()) < MIN_DESCRIPTION_WORDS:
        return None
    return _truncate_words(text)


def salvage_description(stored: str | None) -> str | None:
    """Recover the real bio from a polluted stored one, or None.

    Many stored bios hold a closed ``<thought>…</thought>`` block followed by
    the actual answer, cut at 200 words by the old code. Keep the answer if
    it ends a sentence; otherwise trim to its last full sentence, provided
    that leaves at least MIN_DESCRIPTION_WORDS words.
    """
    text = clean_description(stored)
    if not text:
        return None
    if _ends_sentence(text):
        return text
    end = _last_sentence_end(text)
    if end <= 0:
        return None
    trimmed = text[: end + 1]
    return trimmed if len(trimmed.split()) >= MIN_DESCRIPTION_WORDS else None


def _build_prompt(text_content: str, url: str) -> str:
    return (
        f"From the following text from a researcher's homepage at {url}, "
        "write a short professional bio in the third person (at most "
        f"{MAX_DESCRIPTION_WORDS} words) saying who this person is, their "
        "current position and affiliation, and their research interests. "
        "Use only facts stated in the text. "
        'Respond with a JSON object {"description": "<bio>"}. '
        'If the text has no such information, respond with {"description": null}. '
        "Do not include notes, reasoning or a draft in the JSON.\n\n"
        f"Content:\n{text_content}"
    )


@dataclass
class DescriptionResult:
    """Outcome of one bio call.

    `description` is a clean bio or None. `retry_after` is set when the
    provider refused the call for quota or account reasons, so a background
    job can stop and leave the shared key to extraction. `rejected` is True
    when the model answered but the answer was not a bio.
    """
    description: str | None = None
    retry_after: float | None = None
    rejected: bool = False
    failed: bool = False


def request_description(text_content: str, url: str, scrape_log_id=None) -> DescriptionResult:
    """Ask the LLM for a researcher bio and validate the answer."""
    from backend.llm.client import extract_json, get_model

    try:
        result = extract_json(
            _build_prompt(text_content, url), ResearcherDescription, max_tokens=_MAX_TOKENS,
        )
    except Exception as e:  # best-effort: never break extraction over a bio
        logger.error("Error extracting description from %s: %s: %s", url, type(e).__name__, e)
        return DescriptionResult(failed=True)

    if result.usage is not None:
        try:
            log_llm_usage(
                "description_extraction", get_model(), result.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )
        except Exception as e:
            logger.warning("Could not log description LLM usage for %s: %s", url, e)

    if result.parsed is None:
        return DescriptionResult(retry_after=result.retry_after, failed=True)
    raw = result.parsed.description
    desc = clean_description(raw)
    if raw and desc is None:
        logger.warning("Rejected description for %s (not a bio): %.120r", url, raw)
        return DescriptionResult(rejected=True)
    return DescriptionResult(description=desc)


def extract_description(text_content: str, url: str, scrape_log_id=None) -> str | None:
    """Ask the LLM for a researcher bio; return it only if it is clean.

    Returns None when the call fails, the model finds no bio, or the answer
    is rejected by `clean_description()`. Callers must treat None as "keep
    what is stored", never as "clear the bio".
    """
    return request_description(text_content, url, scrape_log_id=scrape_log_id).description


# MySQL REGEXP twin of _BULLET_LABEL_RE (ICU syntax, MySQL 8).
_BULLET_LABEL_SQL = r"(?m)^\s*(?:[*\-•]|\d+\.)\s+\**[A-Za-z][A-Za-z ]{1,30}\**:\s"


def reasoning_sql_condition(column: str = "description") -> tuple[str, tuple]:
    """SQL prefilter for rows that may hold reasoning text.

    Over-selects slightly (the column collation is case-insensitive, the
    Python check is not always); confirm each row with looks_like_reasoning().
    """
    likes = " OR ".join(f"{column} LIKE %s" for _ in REASONING_MARKERS)
    params = tuple(f"%{m}%" for m in REASONING_MARKERS) + (_BULLET_LABEL_SQL,)
    return f"({likes} OR {column} REGEXP %s)", params
