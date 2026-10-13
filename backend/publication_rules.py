"""Shared publication rules, independent of extraction and database clients."""
import re


_SERIES_ONLY = re.compile(
    r"(?:NBER|National Bureau of Economic Research|CEPR|Centre for Economic Policy Research|"
    r"IZA|RIETI|SSRN|CESifo|CES-ifo|IMF|International Monetary Fund|ECB|"
    r"European Central Bank|World Bank|World Bank Policy Research|"
    r"HBS|Harvard Business School)"
    r"(?:\s+(?:(?:policy\s+)?research\s+|working\s+|discussion\s+)?papers?)?"
    r"(?:\s+series)?(?:\s*(?:no\.?|number|#)?\s*[0-9][0-9./-]*)?",
    re.IGNORECASE,
)


def is_working_paper_series(venue: str | None) -> bool:
    """Recognize a series-only venue; mixed journal/series labels stay untouched."""
    if not venue:
        return False
    return _SERIES_ONLY.fullmatch(venue.strip()) is not None
