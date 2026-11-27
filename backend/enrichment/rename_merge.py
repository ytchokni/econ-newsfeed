"""Rename-aware duplicate detection: papers sharing an SSRN/NBER/arXiv/DOI link.

A working paper keeps its SSRN abstract, NBER WP number, arXiv ID or DOI when
its title is revised, so the old and new titles end up as separate papers that
share a link (#265). Spelling variants are #251's job (same_paper_title); this
module covers the renames it deliberately refuses.

A shared link is not proof on its own. The link extractor attaches a link to
the nearest title, so a neighbouring entry on the same page can carry another
paper's link (#266). A pair is a rename only when:

- both papers carry a link to the same strong identifier,
- their author sets are equal or one contains the other,
- neither has a conflicting identifier of the same kind (or OpenAlex ID),
- the titles are not translations of each other, and
- no stored page lists both titles at once (a neighbour mix-up shows both).

Identifiers the re-verification cleared from a paper because the work shares no
author or is a book (openalex_identifier_checks) are never evidence for it.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote

from backend.database import fetch_all
from backend.database.papers import has_title_identity, normalize_title
from backend.enrichment.doi_resolver import normalize_doi

logger = logging.getLogger(__name__)

_SSRN_URL = re.compile(r'abstract_?id=(\d{4,9})|abstract=(\d{4,9})', re.I)
_NBER_URL = re.compile(r'/(?:papers|working_papers)/w(\d{3,6})\b|/w(\d{3,6})\.pdf', re.I)
_ARXIV_URL = re.compile(r'arxiv\.org/(?:abs|pdf)/((?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7})|\d{4}\.\d{4,5})', re.I)
_DOI_IN_URL = re.compile(r'(10\.\d{4,9}/[^\s?#&]+)')
# A bare ISBN DOI (10.1007/978-3-319-13746-9) names a whole book shared by its
# chapters; it is no evidence that two chapters are one paper.
_BOOK_DOI = re.compile(r'/(?:978|979)[-\d]{9,}[\dx]$', re.I)

STRONG_KINDS = ('ssrn', 'nber', 'arxiv', 'doi')


def identifier_key_from_doi(doi: str | None) -> str | None:
    """Canonical key for a DOI; SSRN/NBER/arXiv DOIs map to their series IDs."""
    doi = normalize_doi((doi or '').strip().rstrip('&.,;'))
    if not doi:
        return None
    doi = doi.casefold()
    if m := re.fullmatch(r'10\.2139/ssrn\.(\d+)', doi):
        return f'ssrn:{int(m.group(1))}'
    if m := re.fullmatch(r'10\.3386/w(\d+)', doi):
        return f'nber:{int(m.group(1))}'
    if m := re.fullmatch(r'10\.48550/arxiv\.(.+?)(?:v\d+)?', doi):
        return f'arxiv:{m.group(1)}'
    if _BOOK_DOI.search(doi):
        return None
    return f'doi:{doi}'


def link_identifier_keys(url: str | None, doi: str | None = None) -> set[str]:
    """Strong identifiers named by a paper link (its URL and resolved DOI)."""
    keys = set()
    if key := identifier_key_from_doi(doi):
        keys.add(key)
    text = unquote(url or '')
    lower = text.casefold()
    if 'ssrn' in lower and (m := _SSRN_URL.search(text)):
        keys.add(f'ssrn:{int(m.group(1) or m.group(2))}')
    if 'nber.org' in lower and (m := _NBER_URL.search(text)):
        keys.add(f'nber:{int(m.group(1) or m.group(2))}')
    if 'arxiv.org' in lower and (m := _ARXIV_URL.search(text)):
        keys.add(f'arxiv:{m.group(1).casefold()}')
    if ('doi' in lower or not keys) and (m := _DOI_IN_URL.search(text)):
        if key := identifier_key_from_doi(m.group(1)):
            keys.add(key)
    return keys


def _kind(key: str) -> str:
    return key.split(':', 1)[0]


# --- translations -----------------------------------------------------------

_LANGUAGE_WORDS = {
    'en': {'the', 'of', 'and', 'in', 'on', 'for', 'with', 'from', 'evidence', 'effect', 'effects'},
    'es': {'el', 'los', 'las', 'del', 'y', 'en', 'para', 'por', 'una', 'evidencia', 'efecto', 'efectos', 'sobre'},
    'fr': {'le', 'les', 'des', 'du', 'et', 'une', 'dans', 'pour', 'sur', 'au', 'aux', 'effet', 'effets'},
    'de': {'der', 'die', 'das', 'und', 'von', 'im', 'zur', 'zum', 'mit', 'auf', 'für', 'eine', 'den'},
    'pt': {'os', 'as', 'do', 'dos', 'das', 'e', 'em', 'para', 'uma', 'no', 'na', 'evidência', 'efeito', 'efeitos'},
    'it': {'il', 'gli', 'della', 'delle', 'dei', 'di', 'e', 'nel', 'nella', 'per', 'una', 'effetto', 'effetti'},
}


def _script(title: str) -> str:
    """'latin' for Latin-script titles, else a coarse non-Latin script tag."""
    import unicodedata
    for ch in title:
        if ch.isalpha():
            name = unicodedata.name(ch, '')
            if 'LATIN' not in name:
                return name.split()[0]
    return 'latin'


def title_language(title: str | None) -> str | None:
    """Best-guess language from function words; None when there is no signal."""
    if not title:
        return None
    script = _script(title)
    if script != 'latin':
        return script
    words = normalize_title(title).split()
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in _LANGUAGE_WORDS.items()}
    best = max(scores.values())
    if best == 0:
        return None
    leaders = [lang for lang, score in scores.items() if score == best]
    return leaders[0] if len(leaders) == 1 else None


def is_translation_pair(t1: str | None, t2: str | None) -> bool:
    """Titles in two different languages (or scripts) are kept apart."""
    l1, l2 = title_language(t1), title_language(t2)
    return bool(l1 and l2 and l1 != l2)


# --- pair compatibility -------------------------------------------------------

def compatible_rename(a: dict, b: dict) -> bool:
    """Whether two papers that share a strong link identifier are one renamed paper.

    Each dict carries: title, openalex_id, author_ids, keys (strong identifier
    keys from its links, already minus cleared ones), paper_keys (keys from
    papers.doi). Page co-occurrence is checked separately (it needs HTML).
    """
    if not has_title_identity(a.get('title')) or not has_title_identity(b.get('title')):
        return False
    if not (set(a.get('keys') or ()) & set(b.get('keys') or ())):
        return False
    aa, ba = set(a.get('author_ids') or ()), set(b.get('author_ids') or ())
    if not aa or not ba or not (aa <= ba or ba <= aa):
        return False
    oa, ob = a.get('openalex_id'), b.get('openalex_id')
    if oa and ob and oa.casefold() != ob.casefold():
        return False
    all_a = set(a.get('keys') or ()) | set(a.get('paper_keys') or ())
    all_b = set(b.get('keys') or ()) | set(b.get('paper_keys') or ())
    for kind in STRONG_KINDS:
        ka = {k for k in all_a if _kind(k) == kind}
        kb = {k for k in all_b if _kind(k) == kind}
        if ka and kb and not (ka & kb):
            return False
    return not is_translation_pair(a['title'], b['title'])


# --- page co-occurrence -------------------------------------------------------

# "Previously circulated as ...", "earlier titles: ..." name the old title of the
# same paper; such a mention is evidence for the rename, not a second entry.
_QUALIFIER = r'( version| versions| s| title| titles| titled| circulated| entitled)'
_RENAME_CUE = re.compile(
    rf'\b(previously|formerly|earlier){_QUALIFIER}*( as| under)?\s*$|'
    rf'\b(old|original|prior|previous){_QUALIFIER}+( as| under)?\s*$|'
    r'\bcirculated (as|under)( the title)?\s*$|\b(supersedes|replaces|titled)\s*$'
)


def _occurrences(needle: str, haystack: str) -> list[int]:
    found, start = [], haystack.find(needle)
    while start != -1:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return found


def _listed(title_norm: str, norm_html: str) -> bool:
    """The title appears on the page other than as a cited former title."""
    return any(not _RENAME_CUE.search(norm_html[max(0, i - 60):i])
               for i in _occurrences(title_norm, norm_html))


def titles_cooccur(t1: str, t2: str, norm_html: str) -> bool:
    """Whether a normalized page lists both titles as separate entries.

    Exact (normalized) matches only: fuzzy matching finds a title's words in a
    neighbouring abstract. Near-identical titles ("... Shocks" / "... Shocks on
    Hours") match inside each other, so a title counts only where it is not part
    of the other; a title cited as a former title does not count.
    """
    n1, n2 = normalize_title(t1), normalize_title(t2)
    if not n1 or not n2 or n1 == n2:
        return False
    longer, shorter = (n1, n2) if len(n1) >= len(n2) else (n2, n1)
    if not _listed(longer, norm_html):
        return False
    rest = norm_html.replace(longer, ' | ') if shorter in longer else norm_html
    return _listed(shorter, rest)


def title_on_pages(title: str, pages: list[str]) -> bool:
    norm = normalize_title(title)
    return bool(norm) and any(norm in page for page in pages)


def _source_pages(paper_ids: list[int]) -> dict[int, list[str]]:
    """Normalized current and archived HTML of every page each paper was found on.

    Archived snapshots matter: a neighbour mix-up whose second entry has since
    left the page still shows both titles in an older snapshot.
    """
    import zlib

    from backend.pipeline.feed_events import _normalize_html_for_matching

    pages: dict[int, list[str]] = {pid: [] for pid in paper_ids}
    if not paper_ids:
        return pages
    cache: dict[tuple[str, int], str] = {}
    for start in range(0, len(paper_ids), 200):
        chunk = paper_ids[start:start + 200]
        placeholders = ','.join(['%s'] * len(chunk))
        current = fetch_all(
            f"""SELECT pu.paper_id, hc.url_id AS source_id, hc.content AS html
                FROM paper_urls pu
                JOIN researcher_urls ru ON ru.url = pu.url
                JOIN html_content hc ON hc.url_id = ru.id
                WHERE pu.paper_id IN ({placeholders}) AND hc.content IS NOT NULL""",
            tuple(chunk),
        )
        archived = fetch_all(
            f"""SELECT pu.paper_id, hs.id AS source_id, hs.raw_html_compressed AS html
                FROM paper_urls pu
                JOIN researcher_urls ru ON ru.url = pu.url
                JOIN html_snapshots hs ON hs.url_id = ru.id
                WHERE pu.paper_id IN ({placeholders})""",
            tuple(chunk),
        )
        for kind, rows in (('current', current), ('archived', archived)):
            for row in rows:
                cache_key = (kind, row['source_id'])
                if cache_key not in cache:
                    html = row['html']
                    if kind == 'archived':
                        try:
                            html = zlib.decompress(html).decode('utf-8', errors='replace')
                        except (zlib.error, TypeError):
                            html = ''
                    cache[cache_key] = _normalize_html_for_matching(html or '')
                if cache[cache_key] not in pages[row['paper_id']]:
                    pages[row['paper_id']].append(cache[cache_key])
    return pages


# --- candidate discovery ------------------------------------------------------

# A work cleared because its title is another paper's may be that paper's
# rename (81297 "Fiscal Insurance" -> 125578 "Cross-Policy Risk Pricing"), so its
# links stay evidence and the page check decides. Works sharing no author or a
# book mix-up are never this paper.
CLEARED_EVIDENCE_SQL = ("verdict = 'cleared' AND doi IS NOT NULL "
                        "AND reason <> 'belongs_to_other_paper'")


def _cleared_keys() -> dict[int, set[str]]:
    """Identifiers re-verification removed from a paper are not its evidence."""
    try:
        rows = fetch_all(
            "SELECT paper_id, doi FROM openalex_identifier_checks "
            f"WHERE {CLEARED_EVIDENCE_SQL}"
        )
    except Exception:  # table missing on an unmigrated database
        logger.warning('openalex_identifier_checks unavailable; no cleared identifiers excluded')
        return {}
    cleared: dict[int, set[str]] = {}
    for row in rows:
        if key := identifier_key_from_doi(row['doi']):
            cleared.setdefault(row['paper_id'], set()).add(key)
    return cleared


def load_rename_candidates() -> tuple[list[tuple[int, int, str]], dict[int, dict]]:
    """Pairs of different-title papers sharing a strong link identifier.

    Returns (pairs, papers) where each pair is (id_a, id_b, shared_key) and
    papers maps id -> paper dict ready for compatible_rename.
    """
    from backend.enrichment.paper_merge import same_paper_title

    links = fetch_all(
        """SELECT paper_id, url, doi FROM paper_links
           WHERE doi IS NOT NULL OR url LIKE '%%ssrn%%' OR url LIKE '%%nber.org%%'
              OR url LIKE '%%arxiv.org%%' OR url LIKE '%%doi%%'"""
    )
    cleared = _cleared_keys()
    keys_by_paper: dict[int, set[str]] = {}
    for link in links:
        keys = link_identifier_keys(link['url'], link['doi']) - cleared.get(link['paper_id'], set())
        if keys:
            keys_by_paper.setdefault(link['paper_id'], set()).update(keys)
    papers_by_key: dict[str, set[int]] = {}
    for pid, keys in keys_by_paper.items():
        for key in keys:
            papers_by_key.setdefault(key, set()).add(pid)
    shared = {key: ids for key, ids in papers_by_key.items() if len(ids) > 1}
    ids = sorted({pid for group in shared.values() for pid in group})
    if not ids:
        return [], {}

    papers: dict[int, dict] = {}
    for start in range(0, len(ids), 1000):
        chunk = ids[start:start + 1000]
        rows = fetch_all(
            f"""SELECT p.id, p.title, p.doi, p.openalex_id, p.status, p.discovered_at,
                       GROUP_CONCAT(a.researcher_id ORDER BY a.researcher_id) AS authors
                FROM papers p LEFT JOIN authorship a ON a.publication_id = p.id
                WHERE p.id IN ({','.join(['%s'] * len(chunk))})
                GROUP BY p.id, p.title, p.doi, p.openalex_id, p.status, p.discovered_at""",
            tuple(chunk),
        )
        for row in rows:
            row['author_ids'] = {int(x) for x in (row.pop('authors') or '').split(',') if x}
            row['keys'] = keys_by_paper.get(row['id'], set())
            paper_key = identifier_key_from_doi(row['doi'])
            row['paper_keys'] = {paper_key} - cleared.get(row['id'], set()) if paper_key else set()
            papers[row['id']] = row

    pairs = []
    seen = set()
    for key, group in sorted(shared.items()):
        members = sorted(pid for pid in group if pid in papers)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if (a, b) in seen:
                    continue
                if same_paper_title(papers[a]['title'], papers[b]['title']):
                    continue  # spelling variants: #251's merge
                seen.add((a, b))
                pairs.append((a, b, key))
    return pairs, papers


def classify_rename_pair(a: dict, b: dict, pages_a: list[str], pages_b: list[str]) -> tuple[bool, str]:
    """(mergeable, reason) for one candidate pair."""
    if is_translation_pair(a['title'], b['title']):
        return False, 'translation'
    if not compatible_rename(a, b):
        return False, 'identity_not_verified'
    if not title_on_pages(a['title'], pages_a) or not title_on_pages(b['title'], pages_b):
        # Without each title on its own page, a missing co-occurrence proves nothing.
        return False, 'title_not_on_source_page'
    pages = list(dict.fromkeys(pages_a + pages_b))
    if any(titles_cooccur(a['title'], b['title'], page) for page in pages):
        return False, 'both_titles_on_one_page'
    return True, 'rename'


def find_rename_groups() -> tuple[list[list[int]], list[dict]]:
    """Connected groups of verified renames, plus every refused pair for review."""
    from backend.enrichment.paper_merge import _connected_groups

    pairs, papers = load_rename_candidates()
    pages = _source_pages(sorted({pid for a, b, _ in pairs for pid in (a, b)}))
    accepted, refused = [], []
    for a, b, key in pairs:
        ok, reason = classify_rename_pair(papers[a], papers[b], pages.get(a, []), pages.get(b, []))
        if ok:
            accepted.append([a, b])
        else:
            refused.append({'paper_ids': [a, b], 'key': key, 'reason': reason})
    groups = _connected_groups(accepted)
    # A group is merged only if every pair in it is a verified rename.
    ok_pairs = {tuple(sorted(p)) for p in accepted}
    verified = []
    for group in groups:
        complete = all((x, y) in ok_pairs or _spelling_pair(papers, x, y)
                       for i, x in enumerate(group) for y in group[i + 1:])
        if complete:
            verified.append(group)
        else:
            refused.append({'paper_ids': group, 'key': None, 'reason': 'incomplete_group'})
    return verified, refused


def _spelling_pair(papers: dict, x: int, y: int) -> bool:
    from backend.enrichment.paper_merge import same_paper_title
    return same_paper_title(papers[x]['title'], papers[y]['title'])
