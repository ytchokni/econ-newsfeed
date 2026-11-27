"""Exact matching for the "Top-5" journal and "Top-20" department presets.

Both presets used to match by substring, which let in sister and look-alike
journals (AER: Insights, JPE Micro, Scottish Journal of Political Economy) and
wrong institutions ("MIT" in "Smith College", "Columbia" in "University of
British Columbia").

Each matcher is one regular expression applied to a normalized string. Python
(``re``) and MySQL 8 (ICU ``REGEXP_LIKE``) evaluate the same pattern on the same
normalization, so the SQL filters and the Python helpers agree:

* lower-case, ``&`` becomes `` and ``;
* every run of characters other than ``a-z``, ``0-9`` and ``;`` becomes one
  space (accented letters included, which none of the names use).

The patterns avoid backslash escapes so they pass through SQL parameters
unchanged.
"""
from __future__ import annotations

import re

# Exactly five journals count. Sister journals (AER: Insights, AER/AEA Papers
# and Proceedings, JPE Micro/Macro, the AEJs) and look-alikes (Scottish /
# International / European Journal of Political Economy, Latin American
# Economic Review, AERA Open, AERC) do not.
TOP5_JOURNALS: dict[str, tuple[str, ...]] = {
    "American Economic Review": (
        "american economic review", "aer",
        "am econ rev", "amer econ rev", "american econ rev", "american economic rev",
    ),
    "Econometrica": (
        "econometrica", "ecta",
        "econometrica journal of the econometric society",
        "econometrica econometric society",
    ),
    "Journal of Political Economy": (
        "journal of political economy", "jpe",
        "j polit econ", "j polit economy", "j political econ", "j pol econ",
        "jour polit econ",
    ),
    "Quarterly Journal of Economics": (
        "quarterly journal of economics", "qje",
        "q j econ", "quart j econ", "quarterly j econ", "q j of econ",
    ),
    "Review of Economic Studies": (
        "review of economic studies", "restud",
        "rev econ stud", "rev econ studies", "rev of econ stud",
        "review of econ studies", "rev of economic studies",
    ),
}

# The abbreviation that may follow or precede the full name ("Journal of
# Political Economy (JPE)", "American Economic Review (AER)").
_TOP5_ABBREVIATIONS = {
    "American Economic Review": "aer",
    "Econometrica": "ecta",
    "Journal of Political Economy": "jpe",
    "Quarterly Journal of Economics": "qje",
    "Review of Economic Studies": "restud",
}

# A top-5 paper is published or accepted (forthcoming is stored as accepted).
# Revise-and-resubmit, submitted and working papers are not top-5 publications.
TOP5_PUBLISHED_STATUSES = ("published", "accepted")

_MONTH = "(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
# Citation detail after the journal name: volume, issue, pages, year, month,
# or a forthcoming/lead-article note. Words like "pp" or "no" count only when a
# number follows, so "AER P&P" never reduces to "AER".
_TAIL = (
    "( ((vol|volume|no|nr|number|issue|pages|page|pp|p|pg) ?)?[0-9]+[a-z]?"
    f"| {_MONTH}"
    "| forthcoming| accepted| in press| lead article| lead paper)"
)
_PREFIX = (
    "((forthcoming|accepted|conditionally accepted|to appear|published|appeared)"
    "( (in|at|for publication in))? )"
)


def _split_tolerant(word: str) -> str:
    """Allow stray spaces inside long words ("Polit ical", "Stud ies")."""
    if len(word) < 4:
        return word
    return " ?".join(word)


def _alias_regex(alias: str) -> str:
    return " ".join(_split_tolerant(w) for w in alias.split())


def _top5_names_regex() -> str:
    names: list[str] = []
    for journal, aliases in TOP5_JOURNALS.items():
        full = aliases[0]
        abbr = _TOP5_ABBREVIATIONS[journal]
        variants = [*aliases, f"{full} {abbr}", f"{abbr} {full}"]
        names.extend(_alias_regex(v) for v in variants)
    # Longest first: ICU and Python both take the first alternative that fits.
    names.sort(key=len, reverse=True)
    return "(" + "|".join(names) + ")"


# A venue string may list several venues separated by semicolons
# ("CEPR Discussion Paper 15539; The Review of Economic Studies").
TOP5_VENUE_PATTERN = (
    f"(^|;) *{_PREFIX}*(the )?{_top5_names_regex()}{_TAIL}* *(;|$)"
)

_NON_ALNUM = re.compile(r"[^a-z0-9;]+")
_TOP5_RE = re.compile(TOP5_VENUE_PATTERN)


def normalize_text(value: str | None) -> str:
    """Python mirror of :func:`normalized_sql`."""
    if not value:
        return ""
    return _NON_ALNUM.sub(" ", value.lower().replace("&", " and "))


def normalized_sql(col: str) -> str:
    """SQL expression normalizing ``col`` like :func:`normalize_text`.

    ``col`` is an internal SQL expression, never a request parameter.
    """
    return f"REGEXP_REPLACE(REPLACE(LOWER({col}), '&', ' and '), '[^a-z0-9;]+', ' ')"


def is_top5_venue(venue: str | None) -> bool:
    """True when the venue names one of the five core journals."""
    return bool(_TOP5_RE.search(normalize_text(venue)))


def top5_venue_clause(col: str = "p.venue") -> tuple[str, list[str]]:
    """SQL filter: ``col`` names one of the five core journals."""
    return f"REGEXP_LIKE({normalized_sql(col)}, %s)", [TOP5_VENUE_PATTERN]


def top5_publication_clause(
    venue_col: str = "p.venue", status_col: str = "p.status",
) -> tuple[str, list[str]]:
    """SQL filter: a published or accepted paper in one of the five journals."""
    venue_clause, params = top5_venue_clause(venue_col)
    statuses = ", ".join(f"'{s}'" for s in TOP5_PUBLISHED_STATUSES)
    return f"({status_col} IN ({statuses}) AND {venue_clause})", params


def is_top5_publication(venue: str | None, status: str | None) -> bool:
    return status in TOP5_PUBLISHED_STATUSES and is_top5_venue(venue)


# ---------------------------------------------------------------------------
# Top-20 economics departments
# ---------------------------------------------------------------------------

# Whole-word phrases (normalized). Curated, not an official ranking.
TOP20_DEPARTMENTS: dict[str, tuple[str, ...]] = {
    "MIT": ("mit", "massachusetts institute of technology"),
    "Harvard": ("harvard",),
    "Princeton": ("princeton",),
    "Stanford": ("stanford",),
    "Chicago": ("university of chicago", "uchicago", "chicago booth"),
    "Berkeley": ("uc berkeley", "university of california berkeley"),
    "Columbia": ("columbia",),
    "Yale": ("yale",),
    "Northwestern": ("northwestern",),
    "Penn": ("university of pennsylvania", "upenn"),
    "NYU": ("new york university", "nyu"),
    "Duke": ("duke",),
    "Michigan": ("university of michigan",),
    "Minnesota": ("university of minnesota",),
    "Cornell": ("cornell",),
    "UCLA": ("ucla", "university of california los angeles"),
    "UCSD": ("uc san diego", "ucsd", "university of california san diego"),
    "Wisconsin": ("university of wisconsin",),
    "Boston University": ("boston university",),
    "Carnegie Mellon": ("carnegie mellon",),
}

# Other institutions whose names contain a top-20 phrase. They are blanked out
# before the phrases are matched, so "UBC and Columbia" still counts.
TOP20_LOOKALIKES: tuple[str, ...] = (
    "british columbia", "district of columbia", "columbia college",
    "columbia basin", "columbia southern", "columbia international",
    "columbia state", "missouri columbia", "columbia missouri",
    "carolina columbia",
    "yale nus",
    "northwestern polytechnical", "northwestern a and f", "northwestern state",
    "northwestern oklahoma", "northwestern college", "northwestern health",
    "nyu abu dhabi", "nyu shanghai", "new york university abu dhabi",
    "new york university shanghai",
    "duke kunshan",
    "university of michigan dearborn", "university of michigan flint",
    "university of minnesota duluth", "university of minnesota morris",
    "university of minnesota crookston", "university of minnesota rochester",
    "university of wisconsin (milwaukee|whitewater|la crosse|eau claire|green bay"
    "|oshkosh|parkside|platteville|river falls|stevens point|stout|superior)",
    "(indiana|california|edinboro|slippery rock|clarion|bloomsburg|east stroudsburg"
    "|kutztown|lock haven|mansfield|millersville|shippensburg|west chester|cheyney"
    "|commonwealth) university of pennsylvania",
)

TOP20_LOOKALIKE_PATTERN = " (" + "|".join(TOP20_LOOKALIKES) + ") "
TOP20_DEPARTMENT_PATTERN = (
    " (" + "|".join(p for phrases in TOP20_DEPARTMENTS.values() for p in phrases) + ") "
)
_TOP20_LOOKALIKE_RE = re.compile(TOP20_LOOKALIKE_PATTERN)
_TOP20_RE = re.compile(TOP20_DEPARTMENT_PATTERN)


def is_top20_affiliation(affiliation: str | None) -> bool:
    """True when the affiliation names a top-20 department as whole words."""
    padded = f" {normalize_text(affiliation)} "
    return bool(_TOP20_RE.search(_TOP20_LOOKALIKE_RE.sub(" ", padded)))


def top20_affiliation_clause(col: str = "r.affiliation") -> tuple[str, list[str]]:
    """SQL filter mirroring :func:`is_top20_affiliation`."""
    padded = f"CONCAT(' ', {normalized_sql(col)}, ' ')"
    return (
        f"REGEXP_LIKE(REGEXP_REPLACE({padded}, %s, ' '), %s)",
        [TOP20_LOOKALIKE_PATTERN, TOP20_DEPARTMENT_PATTERN],
    )
