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
_YEAR = re.compile(r"^(1[89]|20|21)\d\d$")


def _is_counter_swap(old: str, new: str) -> bool:
    a, b = _NUMBER_TOKEN.match(old), _NUMBER_TOKEN.match(new)
    if not a or not b:
        return False
    if a.group(1) != b.group(1) or a.group(3) != b.group(3):
        return False
    digits_a, digits_b = a.group(2), b.group(2)
    if _YEAR.match(digits_a.replace(",", "")) or _YEAR.match(digits_b.replace(",", "")):
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
    return all(old == new or _is_counter_swap(old, new)
               for old, new in zip(old_tokens, new_tokens))
