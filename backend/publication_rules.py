"""Shared publication rules, independent of extraction and database clients."""
import re
from datetime import datetime, timedelta, timezone


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


# ── Feed event plausibility ──
#
# Shared by the extraction pipeline (which decides whether to emit an event),
# scripts/cleanup_spurious_feed_events.py and the data-quality invariants, so
# all three agree on what a spurious event is.

# A new_paper event is suppressed when the paper is dated more than this many
# years before the event (issue #261: 2002 ZEW papers posted as new in 2026).
NEW_PAPER_MAX_AGE_YEARS = 2

# A paper already in the database can still earn a new_paper event the first
# time another URL lists it, but only within this window of its discovery
# (e.g. a coauthor's page catching up a few days later).
NEW_PAPER_MAX_DISCOVERY_LAG_DAYS = 7

# A "now published" event is suppressed when the publication year is more
# than this many years before the event (issue #264).
PUBLISHED_MAX_AGE_YEARS = 1

# Other forward transitions (R&R, accepted) carry the working-paper year,
# which can legitimately be several years old.
STATUS_CHANGE_MAX_AGE_YEARS = 10

# Statuses that claim a journal outcome and so must name a journal venue.
JOURNAL_STATUSES = frozenset({"accepted", "published"})

_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")

# Venue words that mark a series, outlet or format rather than a journal.
_NON_JOURNAL_VENUE = re.compile(
    r"\b(?:working|discussion|policy|occasional|staff|background|technical|research|conference|"
    r"job\s+market|seminar)\s+(?:papers?|notes?|briefs?|reports?|series)\b"
    r"|\b(?:policy|economic|research|issue)\s+(?:briefs?|memos?|insights?|letters?\s+from)\b"
    r"|\bbriefs?\b|\bblogs?\b|\bop-?eds?\b|\bcolumns?\b|\bnewsletters?\b|\bpodcasts?\b"
    r"|\bpreprints?\b|\bmimeo\b|\bmanuscripts?\b|\bunpublished\b|\bthesis\b|\bdissertation\b"
    r"|\bpress\s+releases?\b|\bcommentary\b|\bsubstack\b|\bmedium\.com\b|\bvoxeu\b|\bvox\s+column\b"
    r"|\bssrn\b|\barxiv\b|\brepec\b|\bideas\s+for\s+india\b|\bW\.?P\.?\b|\bD\.?P\.?\b"
    r"|\bedited\s+volumes?\b|\bin\s+preparation\b|\bsubmitted\b|\bunder\s+review\b"
    r"|\bFEDS\s+notes\b|\bfinancial\s+times\b|\bresearch\s+memoranda\b|\bdocumentos\s+de\s+trabajo\b"
    r"|\bborradores\s+de\b|\bquaderni\b|\bensaios\s+econ",
    re.IGNORECASE,
)

# Words that identify a journal even when a series is named alongside it,
# e.g. "NBER WP 123; published in Journal of Finance".
_JOURNAL_HINT = re.compile(
    r"\bjournal\b|\breview\b|\bquarterly\b|\beconometrica\b|\beconomica\b|\bannals\b"
    r"|\bletters\b|\bproceedings\b|\btransactions\b|\bAEJ\b|\bAER\b|\bQJE\b|\bJPE\b|\bREStud\b|\bReStat\b",
    re.IGNORECASE,
)


def parse_year(value) -> int | None:
    """First plausible four-digit year in a stored year value ('2024', '2023-24')."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1800 <= value <= 2100 else None
    match = _YEAR.search(str(value))
    return int(match.group(1)) if match else None


def is_stale_year(year, reference_year: int, max_age_years: int) -> bool:
    """True when year is known and more than max_age_years before reference_year."""
    parsed = parse_year(year)
    return parsed is not None and reference_year - parsed > max_age_years


def is_journal_venue(venue: str | None) -> bool:
    """Whether a venue plausibly names a journal (or book) outlet.

    Blank venues, working-paper series and blog/brief/column outlets are not
    journals. A label that mentions both a series and a journal word counts
    as a journal, because the journal is the newer, more specific outcome.
    """
    if not venue or not venue.strip():
        return False
    venue = venue.strip()
    if _JOURNAL_HINT.search(venue) and not re.search(
            r"\b(?:working|discussion)\s+papers?\b", venue, re.IGNORECASE):
        return True
    if is_working_paper_series(venue):
        return False
    if _JOURNAL_HINT.search(venue):
        # "Review of X; NBER Working Paper 123" — journal named alongside a series.
        return bool(re.search(r"\bjournal\b|\breview\s+of\b|\beconometrica\b", venue, re.IGNORECASE))
    return _NON_JOURNAL_VENUE.search(venue) is None


def status_change_suppression_reason(new_status: str | None, venue: str | None,
                                     year, reference_year: int) -> str | None:
    """Why a forward status_change event would be spurious, or None if plausible."""
    if new_status in JOURNAL_STATUSES and not is_journal_venue(venue):
        return "no journal venue"
    max_age = PUBLISHED_MAX_AGE_YEARS if new_status == "published" else STATUS_CHANGE_MAX_AGE_YEARS
    if is_stale_year(year, reference_year, max_age):
        return f"year {parse_year(year)} older than {max_age}y"
    return None


def _as_utc(value: datetime) -> datetime:
    """MySQL returns naive UTC datetimes; the pipeline passes aware ones."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def new_paper_suppression_reason(*, is_new: bool, is_seed: bool, discovered_at: datetime | None,
                                 year, already_announced: bool, event_at: datetime) -> str | None:
    """Why a new_paper event would be spurious, or None if the paper is genuinely new.

    A paper is new when this save created it, or when it was discovered (not
    as a seed) only days before another URL listed it. Seed and backfilled
    papers, papers known for longer, and papers dated years before the event
    are not news (issue #261).
    """
    if is_seed:
        return "seed paper"
    if already_announced:
        return "already announced"
    if (not is_new and discovered_at is not None
            and _as_utc(event_at) - _as_utc(discovered_at) > timedelta(days=NEW_PAPER_MAX_DISCOVERY_LAG_DAYS)):
        return f"known since {discovered_at:%Y-%m-%d}"
    if is_stale_year(year, event_at.year, NEW_PAPER_MAX_AGE_YEARS):
        return f"year {parse_year(year)} older than {NEW_PAPER_MAX_AGE_YEARS}y"
    return None
