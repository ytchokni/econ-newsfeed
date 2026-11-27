"""In-process researcher name index behind the directory and chooser search.

A LIKE scan cannot ignore punctuation ("oneill" vs O'Neill), rank surnames
above first names, mix name and institution words, or forgive typos
("Reiger"), and over ~170k rows with the directory's EXISTS guards it took
1-3 s per query in production. This module keeps one normalised copy of every
researcher's name and affiliation in memory (rebuilt in the background every
few minutes) and ranks matches in Python. SQL is still used for the optional
directory filters and to fetch the rows of the requested page.

Matching works on normalised text (see ``normalize_text``): case, accents,
apostrophes, periods, hyphens and spacing are ignored. Tiers, best first:

  100  the whole name, in either order ("Thomas Rieger", "Rieger, Thomas")
   95  every query word is a whole word of the name, one of them the surname
   90  ... whole words of the first name only ("Chang" -> Chang Suk Bae)
   80  every query word is a whole word of the affiliation ("DIW", "Harvard");
       above name prefixes so "DIW" lists DIW staff before Diwakar
   75  every query word starts a word of the name, the surname among them
   70  ... first name only
   45  name words plus institution words ("Rieger DIW"), surname matched
   40  ... first name matched
   30  every query word starts a word of the affiliation ("Harv")
   20  similar spelling (typo fallback, only when tiers >= 30 find few)
   15  every query word occurs inside the name, the surname among them
   10  ... first name only

Within a tier, researchers with more publications come first, then the
alphabetical order by surname that the directory used before.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 600
FUZZY_MIN_RESULTS = 5      # run the typo fallback when stricter tiers find fewer
FUZZY_MAX_RESULTS = 200
_RESULT_CACHE_SIZE = 64

TIER_EXACT = 100
TIER_WHOLE = 90
TIER_AFFILIATION_WORD = 80
TIER_PREFIX = 70
TIER_MIXED = 40
TIER_AFFILIATION = 30
TIER_SIMILAR = 20
TIER_SUBSTRING = 10
SURNAME_BONUS = 5

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# Letters NFKD does not decompose into a base letter plus accent.
_FOLD = str.maketrans({
    "ß": "ss", "ẞ": "ss", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l", "æ": "ae", "Æ": "ae",
    "œ": "oe", "Œ": "oe", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d", "þ": "th", "Þ": "th",
    "ı": "i", "ħ": "h", "Ħ": "h",
})
_APOSTROPHES = re.compile(r"['’‘`´ʼ]")
_SEPARATORS = re.compile(r"[\W_]+")


def normalize_text(value: str | None) -> str:
    """Fold a name or query for matching: "O’Neill" -> "oneill", "Jean-Pierre" ->
    "jean pierre", "A.Y. Chang" -> "a y chang", "Müller" -> "muller".

    Apostrophes join (O'Neill is one word); other punctuation separates.
    """
    if not value:
        return ""
    if value.isascii():
        text = value.lower()
    else:
        text = unicodedata.normalize("NFKD", value.translate(_FOLD))
        text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
        text = _APOSTROPHES.sub("", text)
    text = text.replace("'", "").replace("`", "")
    return " ".join(_SEPARATORS.sub(" ", text).split())


def query_words(query: str | None) -> list[str]:
    return normalize_text(query).split()


# Institutions people search by acronym although affiliations often spell the
# name out (Thomas Rieger: "German Institute for Economic Research"), or the
# other way round. Each group is normalised text; an affiliation containing any
# member is searchable by all of them.
INSTITUTION_ALIASES: tuple[tuple[str, ...], ...] = (
    ("diw", "german institute for economic research", "deutsches institut fur wirtschaftsforschung"),
    ("zew", "leibniz centre for european economic research", "centre for european economic research",
     "zentrum fur europaische wirtschaftsforschung"),
    ("iza", "institute of labor economics", "institute for the study of labor",
     "forschungsinstitut zur zukunft der arbeit"),
    ("ifo", "ifo institute", "leibniz institute for economic research at the university of munich"),
    ("ifw", "kiel institute for the world economy", "institut fur weltwirtschaft"),
    ("iwh", "halle institute for economic research"),
    ("rwi", "rwi leibniz institute for economic research", "rheinisch westfalisches institut"),
    ("wzb", "berlin social science center", "wissenschaftszentrum berlin"),
    ("nber", "national bureau of economic research"),
    ("cepr", "centre for economic policy research", "center for economic policy research"),
    ("lse", "london school of economics"),
    ("ucl", "university college london"),
    ("mit", "massachusetts institute of technology"),
    ("nyu", "new york university"),
    ("ucla", "university of california los angeles"),
    ("ucsd", "university of california san diego"),
    ("ucsb", "university of california santa barbara"),
    ("uc berkeley", "university of california berkeley"),
    ("upenn", "university of pennsylvania"),
    ("cmu", "carnegie mellon university"),
    ("pse", "paris school of economics"),
    ("tse", "toulouse school of economics"),
    ("eui", "european university institute"),
    ("imf", "international monetary fund"),
    ("ecb", "european central bank"),
    ("oecd", "organisation for economic co operation and development",
     "organization for economic cooperation and development"),
    ("fed", "federal reserve"),
    ("bis", "bank for international settlements"),
    ("cemfi", "centro de estudios monetarios y financieros"),
    ("ifs", "institute for fiscal studies"),
    ("hec", "hautes etudes commerciales"),
)


def _affiliation_search_text(normalized: str) -> str:
    padded = f" {normalized} "
    extra = [
        member
        for group in INSTITUTION_ALIASES
        if any(f" {alias} " in padded for alias in group)
        for member in group
    ]
    return " ".join([normalized, *extra]) if extra else normalized


# Initials-only stub records ("A.", "A. Y.", "J.-P.", "HTT Tran") are kept for
# authorship but hidden from the directory. Short real names (Li, Wu, Bo) are
# not stubs. The directory's SQL guard uses the same patterns (case-sensitive
# REGEXP_LIKE), so both must stay in step.
_LETTER = "A-Za-zÀ-ÖØ-öø-ÿ"
INITIALS_ONLY = rf"^[{_LETTER}]([ .-]+[{_LETTER}])*[ .]*$"
CAPITAL_INITIALS = r"^[A-Z]{2,3}$"   # first name "HTT" / "ET" ...
HAS_LOWERCASE = r"[a-z]"             # ... unless the surname is in capitals too ("HAN QIU")
_INITIALS_ONLY = re.compile(INITIALS_ONLY)
_CAPITAL_INITIALS = re.compile(CAPITAL_INITIALS)
_HAS_LOWERCASE = re.compile(HAS_LOWERCASE)


def is_initials_stub(first_name: str | None, last_name: str | None) -> bool:
    """True for names the directory hides: missing parts or initials only."""
    first, last = (first_name or "").strip(), (last_name or "").strip()
    if not first or not last:
        return True
    if _INITIALS_ONLY.match(first) or _INITIALS_ONLY.match(last):
        return True
    return bool(_CAPITAL_INITIALS.match(first) and _HAS_LOWERCASE.search(last))


# ---------------------------------------------------------------------------
# Matching primitives
# ---------------------------------------------------------------------------

WHOLE, PREFIX, INSIDE = 3, 2, 1


def _word_match(word: str, tokens: list[str], first_count: int) -> tuple[int, bool]:
    """How ``word`` matches a tokenised name: (kind, in_surname).

    The name is compared with its spaces removed, so "vanreenen" is a whole
    match for "Van Reenen" and "jp" for "J. P.". Words shorter than three
    letters only match at the start of a name word (initials).
    """
    compact = "".join(tokens)
    starts, ends, owner = set(), set(), []
    pos = 0
    for i, token in enumerate(tokens):
        starts.add(pos)
        pos += len(token)
        ends.add(pos)
        owner.extend([i] * len(token))
    best, surname = 0, False
    at = compact.find(word)
    while at >= 0:
        if at in starts:
            kind = WHOLE if at + len(word) in ends else PREFIX
        else:
            kind = INSIDE if len(word) >= 3 else 0
        in_surname = owner[at + len(word) - 1] >= first_count
        if (kind, in_surname) > (best, surname):
            best, surname = kind, in_surname
        at = compact.find(word, at + 1)
    return best, surname


def _affiliation_match(word: str, affiliation_tokens: list[str]) -> int:
    if word in affiliation_tokens:
        return WHOLE
    if any(t.startswith(word) for t in affiliation_tokens):
        return PREFIX
    return 0


def _typo_cost(a: str, b: str, limit: float) -> float:
    """Edit distance where swapping two adjacent letters costs 0.5 ("ei"/"ie")."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[float] = []
    prev = [float(j) for j in range(len(b) + 1)]
    for i in range(1, len(a) + 1):
        cur = [float(i)] + [0.0] * len(b)
        for j in range(1, len(b) + 1):
            cost = cur[j - 1] + 1
            cost = min(cost, prev[j] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cost = min(cost, prev2[j - 2] + 0.5)
            cur[j] = cost
        if min(cur) > limit and min(prev) + 0.5 > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _max_typo_cost(word: str) -> float:
    if len(word) <= 5:
        return 1.0
    return 1.5 if len(word) <= 7 else 2.0


def _grams(token: str) -> set[str]:
    padded = f"^{token}$"
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Match:
    researcher_id: int
    tier: int
    similar: bool = False


@dataclass(frozen=True)
class SearchResult:
    matches: tuple[Match, ...]
    # Normalised query words replaced by close name words, and the researcher
    # whose name supplied them (for "Did you mean ...?"); empty if no typo.
    corrections: tuple[tuple[str, str], ...] = ()
    corrected_from: int | None = None

    @property
    def ids(self) -> list[int]:
        return [m.researcher_id for m in self.matches]


@dataclass
class ResearcherIndex:
    # Per researcher ("slot"). The normalised name lives only in _spaced_blob;
    # first_counts says how many of its words are the first name.
    ids: array = field(default_factory=lambda: array("q"))
    first_counts: bytearray = field(default_factory=bytearray)
    pubs: array = field(default_factory=lambda: array("l"))
    listed: bytearray = field(default_factory=bytearray)
    affiliation_of: array = field(default_factory=lambda: array("l"))
    affiliations: list[str] = field(default_factory=list)
    affiliation_members: list[array] = field(default_factory=list)
    built_at: float = 0.0
    _name_blob: str = ""
    _name_offsets: array = field(default_factory=lambda: array("l"))
    _spaced_blob: str = ""
    _spaced_offsets: array = field(default_factory=lambda: array("l"))
    _affiliation_blob: str = ""
    _affiliation_offsets: array = field(default_factory=lambda: array("l"))
    # Distinct name words, the slots using each (flat, sliced by _posting_starts)
    # and, for typo matching, the words containing each letter trigram.
    _vocabulary: list[str] = field(default_factory=list)
    _postings: array = field(default_factory=lambda: array("i"))
    _posting_starts: array = field(default_factory=lambda: array("i"))
    _gram_words: dict[str, array] = field(default_factory=dict)
    _cache: OrderedDict = field(default_factory=OrderedDict)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock)

    # -- construction -------------------------------------------------------

    @classmethod
    def build(cls, rows: Iterable[dict]) -> "ResearcherIndex":
        """Rows: id, first_name, last_name, affiliation, pubs, validated."""
        index = cls()
        affiliation_ids: dict[str, int] = {}
        raw_affiliation_ids: dict[str, int] = {}
        names, spaced = [], []
        token_slots: dict[str, list[int]] = {}
        for row in rows:
            first_raw = row.get("first_name") or ""
            last_raw = row.get("last_name") or ""
            first, last = normalize_text(first_raw), normalize_text(last_raw)
            if not first and not last:
                continue
            slot = len(index.ids)
            pubs = int(row.get("pubs") or 0)
            index.ids.append(int(row["id"]))
            index.first_counts.append(min(len(first.split()), 255))
            index.pubs.append(pubs)
            # The directory's guards: validated, published, not an initials stub.
            index.listed.append(bool(
                row.get("validated") and pubs > 0
                and not is_initials_stub(first_raw, last_raw)
            ))
            raw = row.get("affiliation") or ""
            aff_id = raw_affiliation_ids.get(raw)
            if aff_id is None:
                affiliation = normalize_text(raw)
                aff_id = affiliation_ids.get(affiliation, -1) if affiliation else -1
                if affiliation and aff_id < 0:
                    aff_id = affiliation_ids[affiliation] = len(index.affiliations)
                    index.affiliations.append(_affiliation_search_text(affiliation))
                    index.affiliation_members.append(array("l"))
                raw_affiliation_ids[raw] = aff_id
            if aff_id >= 0:
                index.affiliation_members[aff_id].append(slot)
                index.affiliation_of.append(aff_id)
            else:
                index.affiliation_of.append(-1)
            tokens = f"{first} {last}".split()
            names.append("".join(tokens))
            spaced.append(" " + " ".join(tokens))
            for token in set(tokens):
                posting = token_slots.get(token)
                if posting is None:
                    token_slots[token] = [slot]
                else:
                    posting.append(slot)
        index._vocabulary = sorted(token_slots)
        index._posting_starts.append(0)
        gram_words: dict[str, list[int]] = {}
        for position, token in enumerate(index._vocabulary):
            index._postings.extend(token_slots[token])
            index._posting_starts.append(len(index._postings))
            if len(token) >= 3:
                for gram in _grams(token):
                    gram_words.setdefault(gram, []).append(position)
        del token_slots
        index._gram_words = {g: array("i", words) for g, words in gram_words.items()}
        index._name_blob, index._name_offsets = _blob(names)
        index._spaced_blob, index._spaced_offsets = _blob(spaced)
        index._affiliation_blob, index._affiliation_offsets = _blob(
            " " + a for a in index.affiliations
        )
        index.built_at = time.monotonic()
        return index

    def __len__(self) -> int:
        return len(self.ids)

    # -- candidate generation ----------------------------------------------

    def _name_slots(self, word: str) -> set[int]:
        if len(word) >= 3:
            return _blob_hits(self._name_blob, self._name_offsets, word)
        return _blob_hits(self._spaced_blob, self._spaced_offsets, " " + word)

    def _affiliation_slots(self, word: str) -> set[int]:
        slots: set[int] = set()
        for aff_id in _blob_hits(self._affiliation_blob, self._affiliation_offsets, " " + word):
            slots.update(self.affiliation_members[aff_id])
        return slots

    # -- scoring -------------------------------------------------------------

    def _tokens(self, slot: int) -> tuple[list[str], int]:
        start = self._spaced_offsets[slot]
        end = (self._spaced_offsets[slot + 1] - 1 if slot + 1 < len(self._spaced_offsets)
               else len(self._spaced_blob))
        return self._spaced_blob[start:end].split(), self.first_counts[slot]

    def _sort_name(self, slot: int) -> tuple[str, str]:
        tokens, first_count = self._tokens(slot)
        return " ".join(tokens[first_count:]), " ".join(tokens[:first_count])

    def _slots_with_word(self, token: str) -> array:
        at = bisect_left(self._vocabulary, token)
        if at < len(self._vocabulary) and self._vocabulary[at] == token:
            return self._postings[self._posting_starts[at]:self._posting_starts[at + 1]]
        return array("i")

    def _tier(self, slot: int, words: list[str], phrase: str) -> int:
        tokens, first_count = self._tokens(slot)
        compact_first, compact_last = "".join(tokens[:first_count]), "".join(tokens[first_count:])
        if phrase in (compact_first + compact_last, compact_last + compact_first):
            return TIER_EXACT
        name = [_word_match(w, tokens, first_count) for w in words]
        tier = _name_tier(name)
        if len(words) > 1:
            # "o neill", "van reenen" typed apart or together match either way.
            tier = max(tier, _name_tier([_word_match(phrase, tokens, first_count)]))
        if tier >= TIER_WHOLE:
            return tier
        aff_id = self.affiliation_of[slot]
        if aff_id >= 0:
            aff_tokens = self.affiliations[aff_id].split()
            aff = [_affiliation_match(w, aff_tokens) for w in words]
            named = [kind >= PREFIX for kind, _ in name]
            if all(n or a for n, a in zip(named, aff)):
                if any(named):
                    surname = any(s for (kind, s) in name if kind >= PREFIX)
                    tier = max(tier, TIER_MIXED + SURNAME_BONUS * surname)
                elif all(a == WHOLE for a in aff):
                    tier = max(tier, TIER_AFFILIATION_WORD)
                else:
                    tier = max(tier, TIER_AFFILIATION)
        return tier

    # -- search --------------------------------------------------------------

    def search(self, query: str | None, *, listed_only: bool) -> SearchResult:
        words = query_words(query)
        if not words:
            return SearchResult(())
        key = (" ".join(words), listed_only)
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        result = self._search(words, listed_only)
        with self._cache_lock:
            self._cache[key] = result
            while len(self._cache) > _RESULT_CACHE_SIZE:
                self._cache.popitem(last=False)
        return result

    def _search(self, words: list[str], listed_only: bool) -> SearchResult:
        phrase = "".join(words)
        per_word = []
        for word in sorted(set(words), key=len, reverse=True):
            per_word.append(self._name_slots(word) | self._affiliation_slots(word))
        candidates = _intersect(per_word)
        if len(words) > 1:
            candidates |= self._name_slots(phrase)
        if listed_only:
            candidates = {s for s in candidates if self.listed[s]}

        scored = []
        for slot in candidates:
            tier = self._tier(slot, words, phrase)
            if tier:
                scored.append((tier, slot))
        strong = sum(1 for tier, _ in scored if tier >= TIER_AFFILIATION)

        similar: list[tuple[float, int]] = []
        corrections: tuple[tuple[str, str], ...] = ()
        corrected_from = None
        if strong < FUZZY_MIN_RESULTS:
            seen = {slot for _, slot in scored}
            similar, best_fixes = self._similar(words, listed_only, seen, strict_found=strong > 0)
            if similar:
                corrected_from = int(self.ids[similar[0][1]])
                corrections = best_fixes

        def order(tier: int, slot: int, cost: float = 0.0):
            return (-tier, cost, -self.pubs[slot], *self._sort_name(slot), self.ids[slot])

        ranked = sorted(
            [order(t, s) + (False,) for t, s in scored]
            + [order(TIER_SIMILAR, s, c) + (True,) for c, s in similar]
        )
        matches = tuple(Match(int(r[5]), -r[0], r[6]) for r in ranked)
        return SearchResult(matches, corrections, corrected_from)

    def _similar_tokens(self, word: str) -> dict[str, float]:
        limit = _max_typo_cost(word)
        shared: dict[int, int] = {}
        for gram in _grams(word):
            for position in self._gram_words.get(gram, ()):
                shared[position] = shared.get(position, 0) + 1
        need = 2 if len(word) >= 6 else 1
        out = {}
        for position, count in shared.items():
            token = self._vocabulary[position]
            if count < need or token == word or abs(len(token) - len(word)) > limit:
                continue
            cost = _typo_cost(word, token, limit)
            if cost <= limit:
                out[token] = cost
        return out

    def _starts_a_name_word(self, word: str) -> bool:
        at = bisect_left(self._vocabulary, word)
        return at < len(self._vocabulary) and self._vocabulary[at].startswith(word)

    def _similar(
        self, words: list[str], listed_only: bool, exclude: set[int], *, strict_found: bool
    ) -> tuple[list[tuple[float, int]], tuple[tuple[str, str], ...]]:
        """Researchers whose name matches once misspelt words are corrected.

        Every query word must still match (start a word of the name or the
        affiliation, or be one of the close spellings of a name word), and at
        least one must need a correction. While anything matched strictly, a
        word that starts some name word ("thom") is taken as typed.
        """
        fixes = {
            w: self._similar_tokens(w)
            for w in set(words)
            if len(w) >= 4 and not (strict_found and self._starts_a_name_word(w))
        }
        if not any(fixes.values()):
            return [], ()
        per_word = []
        for word in sorted(set(words), key=len, reverse=True):
            slots = self._name_slots(word) | self._affiliation_slots(word)
            for token in fixes.get(word, {}):
                slots.update(self._slots_with_word(token))
            per_word.append(slots)
        results = []
        for slot in _intersect(per_word) - exclude:
            if listed_only and not self.listed[slot]:
                continue
            tokens, first_count = self._tokens(slot)
            token_set = set(tokens)
            aff_id = self.affiliation_of[slot]
            aff_tokens = self.affiliations[aff_id].split() if aff_id >= 0 else []
            cost, used = 0.0, []
            for word in words:
                if _word_match(word, tokens, first_count)[0] >= PREFIX:
                    continue
                if _affiliation_match(word, aff_tokens):
                    continue
                options = [(c, t) for t, c in fixes.get(word, {}).items() if t in token_set]
                if not options:
                    break
                c, t = min(options)
                cost += c
                used.append((word, t))
            else:
                if used:
                    results.append((cost, slot, tuple(used)))
        results.sort(key=lambda r: (r[0], -self.pubs[r[1]], *self._sort_name(r[1])))
        results = results[:FUZZY_MAX_RESULTS]
        best = results[0][2] if results else ()
        return [(cost, slot) for cost, slot, _ in results], best


def _name_tier(kinds: list[tuple[int, bool]]) -> int:
    if not kinds or min(kind for kind, _ in kinds) == 0:
        return 0
    weakest = min(kind for kind, _ in kinds)
    surname = any(s for _, s in kinds)
    base = {WHOLE: TIER_WHOLE, PREFIX: TIER_PREFIX, INSIDE: TIER_SUBSTRING}[weakest]
    return base + SURNAME_BONUS * surname


def _blob(parts: Iterable[str]) -> tuple[str, array]:
    offsets = array("l")
    chunks = []
    pos = 0
    for part in parts:
        offsets.append(pos)
        chunks.append(part)
        pos += len(part) + 1
    return "\n".join(chunks), offsets


def _blob_hits(blob: str, offsets: array, needle: str) -> set[int]:
    """Slots whose text contains ``needle`` (records are newline-separated)."""
    hits: set[int] = set()
    at = blob.find(needle)
    n = len(offsets)
    while at >= 0:
        slot = bisect_right(offsets, at) - 1
        hits.add(slot)
        at = blob.find(needle, offsets[slot + 1] if slot + 1 < n else len(blob))
    return hits


def _intersect(sets: list[set[int]]) -> set[int]:
    if not sets:
        return set()
    ordered = sorted(sets, key=len)
    out = set(ordered[0])
    for other in ordered[1:]:
        out &= other
        if not out:
            break
    return out


_RAW_WORD = re.compile(r"[^\W_]+(?:['’‘`´ʼ][^\W_]+)*")


def _raw_words(text: str) -> list[str]:
    """Words as typed, split where normalize_text splits ("O'Neill" stays whole)."""
    return _RAW_WORD.findall(text or "")


def suggest_query(query: str, corrections: dict[str, str], display_name: str) -> str:
    """Rewrite ``query`` with corrected words spelt as in ``display_name``.

    suggest_query("thomas Reiger", {"reiger": "rieger"}, "Thomas Rieger")
    -> "thomas Rieger"
    """
    shown = {normalize_text(w): w for w in _raw_words(display_name)}
    typed = {}
    for raw in _raw_words(query):
        typed.setdefault(normalize_text(raw), raw)
    out = []
    for word in query_words(query):
        if word in corrections:
            fixed = corrections[word]
            out.append(shown.get(fixed, fixed))
        else:
            out.append(typed.get(word, word))
    return " ".join(out)


# ---------------------------------------------------------------------------
# Process-wide cache
# ---------------------------------------------------------------------------

INDEX_SQL = """
    SELECT r.id, r.first_name, r.last_name, r.affiliation,
           COALESCE(pc.cnt, 0) AS pubs,
           CASE WHEN r.openalex_author_id IS NOT NULL OR EXISTS
                (SELECT 1 FROM researcher_urls ru WHERE ru.researcher_id = r.id)
           THEN 1 ELSE 0 END AS validated
    FROM researchers r
    LEFT JOIN (SELECT researcher_id, COUNT(*) AS cnt FROM authorship GROUP BY researcher_id) pc
      ON pc.researcher_id = r.id
"""

_index: ResearcherIndex | None = None
_build_lock = threading.Lock()
_refreshing = threading.Event()


def get_index(fetch_all: Callable[..., list[dict]]) -> ResearcherIndex:
    """Return the shared index, building it on first use.

    A stale index keeps serving while one background thread rebuilds it.
    """
    global _index
    current = _index
    if current is None:
        with _build_lock:
            if _index is None:
                _index = _load(fetch_all)
            return _index
    if time.monotonic() - current.built_at > REFRESH_SECONDS and not _refreshing.is_set():
        _refreshing.set()
        threading.Thread(target=_refresh, args=(fetch_all,), daemon=True,
                         name="researcher-index-refresh").start()
    return current


def _load(fetch_all: Callable[..., list[dict]]) -> ResearcherIndex:
    started = time.monotonic()
    index = ResearcherIndex.build(fetch_all(INDEX_SQL))
    logger.info("Researcher search index: %d names in %.2fs", len(index), time.monotonic() - started)
    return index


def _refresh(fetch_all: Callable[..., list[dict]]) -> None:
    global _index
    try:
        fresh = _load(fetch_all)
        with _build_lock:
            _index = fresh
    except Exception:
        logger.exception("Researcher search index refresh failed; keeping the previous index")
    finally:
        _refreshing.clear()


def warm_index(fetch_all: Callable[..., list[dict]]) -> None:
    """Build the index in the background so the first search is fast."""
    def run():
        try:
            get_index(fetch_all)
        except Exception:
            logger.exception("Researcher search index warm-up failed")
    threading.Thread(target=run, daemon=True, name="researcher-index-warmup").start()


def reset_index() -> None:
    """Forget the shared index (tests, or after bulk researcher changes)."""
    global _index
    with _build_lock:
        _index = None
