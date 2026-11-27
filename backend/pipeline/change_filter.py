"""Recognise page changes that cannot change any publication.

The fetcher already hashes normalised page text, not raw HTML, and pages
whose normalised text equals the previous snapshot skip the LLM (19% of the
changed pages pending on 2026-11-27). Of the rest, about one in eight
differed only in spacing ("( Michael" / "(Michael", "+431 7982601" /
"+4317982601") or in counters such as citation or view counts ("35" -> "36").
Those are skipped here too. Everything else goes to diff extraction:
years are never treated as counters (a working paper's year moving is a
revision), and any change of letters, or any inserted or deleted token,
counts as a real change.
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")
# A number with optional surrounding punctuation: "35", "(35)", "1,234", "[12]".
_NUMBER_TOKEN = re.compile(r"^(\W*)(\d[\d,.]*)(\W*)$")
# Any 4-digit 1800-2199 run counts as a year, also inside dotted dates and
# with punctuation ("2025.", "11.2025", "03.2025-04.2026").
_YEAR = re.compile(r"(?<!\d)(1[89]|20|21)\d\d(?!\d)")
# A number after one of these is part of a citation, not a counter.
_CITATION_LABEL = re.compile(r"^\W*(vol|volume|no|nr|num|number|issue|iss|pp?|pages?)\.?\W*$",
                             re.IGNORECASE)


def _is_counter_swap(old: str, new: str) -> bool:
    a, b = _NUMBER_TOKEN.match(old), _NUMBER_TOKEN.match(new)
    if not a or not b:
        return False
    if a.group(1) != b.group(1) or a.group(3) != b.group(3):
        return False
    if _YEAR.search(old) or _YEAR.search(new):
        return False
    return True


def is_insignificant_change(old_text: str, new_text: str) -> bool:
    """True when the two (normalised) page texts can hold no different paper data."""
    if old_text == new_text:
        return True
    if _WHITESPACE.sub("", old_text) == _WHITESPACE.sub("", new_text):
        return True
    old_tokens, new_tokens = old_text.split(), new_text.split()
    if len(old_tokens) != len(new_tokens):
        return False  # something was inserted or removed
    for i, (old, new) in enumerate(zip(old_tokens, new_tokens)):
        if old == new:
            continue
        if not _is_counter_swap(old, new):
            return False
        if i and _CITATION_LABEL.match(old_tokens[i - 1]):
            return False  # "Vol. 12" -> "Vol. 13": the citation itself changed
    return True
