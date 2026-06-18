"""Shared helpers for search query construction."""
from __future__ import annotations

import os

def escape_like(value: str) -> str:
    """Escape LIKE-special characters so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


FT_MIN_TOKEN_SIZE = int(os.environ.get("FT_MIN_TOKEN_SIZE", "3"))

_FT_BOOLEAN_OPERATORS = str.maketrans("", "", '+-~<>()@*"')


def escape_fulltext(value: str) -> str:
    """Strip BOOLEAN MODE operators so user input is matched literally."""
    return value.translate(_FT_BOOLEAN_OPERATORS)


TOP20_DEPT_KEYWORDS = [
    "MIT", "Massachusetts Institute of Technology",
    "Harvard", "Princeton", "Stanford",
    "University of Chicago",
    "UC Berkeley", "University of California, Berkeley",
    "Columbia", "Yale", "Northwestern",
    "University of Pennsylvania",
    "New York University", "NYU",
    "Duke",
    "University of Michigan",
    "University of Minnesota",
    "Cornell",
    "UCLA", "University of California, Los Angeles",
    "UC San Diego", "University of California, San Diego",
    "University of Wisconsin",
    "Boston University",
    "Carnegie Mellon",
]

TOP5_JOURNAL_KEYWORDS = [
    "American Economic Review",
    "AER",
    "Econometrica",
    "Journal of Political Economy",
    "JPE",
    "Quarterly Journal of Economics",
    "QJE",
    "Review of Economic Studies",
    "RESTUD",
    "REStud",
]
