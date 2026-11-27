"""Researcher identity: same-person names, record merges, OpenAlex IDs, page owners.

One person can reach the database under several spellings ("Andy Garin" /
"Andrew Garin", "I.L. Salle" / "Isabelle Salle", "Maia Guell" / "Maia Güell",
"Xuan" + "S. Tam" / "Xuan S." + "Tam"). This module decides when two names
belong to the same person, merges researcher records without losing URLs,
follows or JEL codes, keeps an OpenAlex author ID on a single record, and
checks that a tracked page belongs to its owner.

Name matching has two strengths:

* default: equal names after normalization, initials, middle names, nickname
  table (Andy/Andrew, Alex/Alexandre), accent and hyphen/space variants.
* ``strong=True`` adds one-letter typos (Torgovitsky/Torgovistky,
  Cristopher/Christopher, mojibake "J?rgen"/Jürgen), compound surnames
  (Johansson Capusan / Capusan) and reversed order (Fu Chao / Chao Fu). It
  also equates some different common surnames (Zhang/Zheng, Meyer/Mayer), so
  it is used only where a person reviews the result or the page owner is the
  evidence: the backed-up repair script, byline routing to a page's owner,
  and the shared-page check. Merges made while the pipeline runs (OpenAlex
  backfill, Layer 2) use the default strength.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

_TRANSLIT = str.maketrans({
    'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'œ': 'oe', 'Œ': 'oe', 'ß': 'ss',
    'ł': 'l', 'Ł': 'l', 'đ': 'd', 'Đ': 'd', 'ð': 'd', 'þ': 'th', 'ı': 'i',
    '�': '?',  # mojibake replacement char: matches one letter (see _dl1)
})
# Cyrillic profile pages (HSE: "Макеева") name owners stored in Latin script.
_CYRILLIC = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
     "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
_TRANSLIT.update({ord(k): v for k, v in _CYRILLIC.items()})
_TRANSLIT.update({ord(k.upper()): v for k, v in _CYRILLIC.items()})

_PARTICLES = frozenset({
    "de", "del", "della", "der", "di", "du", "da", "das", "dos", "do", "den",
    "van", "von", "la", "le", "les", "ten", "ter", "het", "y", "e", "i",
    "al", "el", "bin", "ibn", "ben", "af", "av", "zu", "st",
})


def _norm_text(text: str) -> str:
    text = (text or "").translate(_TRANSLIT)
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    text = text.casefold().translate(_TRANSLIT)
    text = re.sub(r"[’'`´]", "", text)
    return re.sub(r"[^a-z?]+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return _norm_text(text).split()


def _undigraph(token: str) -> str:
    """German transliteration: Groeger -> Groger matches Gröger -> Groger."""
    return token.replace('ae', 'a').replace('oe', 'o').replace('ue', 'u')


def strip_repeated_surname(first_name: str, last_name: str) -> str:
    """Drop a copy of the surname that the LLM appended to the given names.

    Extraction sometimes returns ["Peter Hull", "Hull"]; stored as-is the
    researcher displays as "Peter Hull Hull", and prefix matching would let
    that variant overwrite a correct "Peter". Only a trailing copy is removed,
    and only when given names remain. (Same rule as PR #249.)
    """
    if not first_name or not last_name:
        return first_name

    def norm(text: str) -> str:
        text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
        return ' '.join(text.replace('-', ' ').casefold().split())

    surname = norm(last_name)
    tokens = first_name.split()
    for k in range(1, len(tokens)):
        if norm(' '.join(tokens[-k:])) == surname:
            return ' '.join(tokens[:-k])
    return first_name


def split_name(first_name: str, last_name: str) -> tuple[list[str], list[str]]:
    """Normalized (given tokens, surname tokens) with extraction quirks undone.

    - initials stored in the surname move to the given names ("S. Tam" -> s / tam)
    - a surname repeated at the end of the given names is dropped
      ("Magne Mogstad" + "Mogstad")
    """
    given = _tokens(first_name)
    surname = _tokens(last_name)
    while len(surname) > 1 and len(surname[0]) == 1 and surname[0] not in _PARTICLES:
        given.append(surname.pop(0))
    if surname and len(given) > len(surname) and given[-len(surname):] == surname:
        given = given[:-len(surname)]
    return given, surname


# ---------------------------------------------------------------------------
# Given-name tokens
# ---------------------------------------------------------------------------

_NICKNAME_GROUPS = [
    # Same name in another spelling, or its usual short form. Names of
    # different people (Alexander/Alexandra, Stefan/Stephen) stay apart.
    "andrew andy drew", "andreas andi andy",
    "alexander alexandre alejandro alessandro aleksandr aleksander alexandros alex sasha",
    "alexandra alessandra alejandra alex sasha",
    "nicholas nicolas nikolaos nikolai nick nico nicky",
    "stephen steven steve stevie", "stefan stephan steffen",
    "robert rob robbie bob bobby bert", "william bill billy will willy willie",
    "james jim jimmy jamie", "michael mike mick mickey mikey", "mikhail misha",
    "david dave davy", "daniel daniele dan danny", "thomas tomas tom tommy",
    "joseph josef joe joey", "jose pepe", "benjamin ben benji benny",
    "christopher christoph chris kit", "christian chris", "christina christine chris tina",
    "kristina kristin kris", "kristopher kristoffer kris", "matthew matt matty", "matthias mathias matt",
    "anthony antony tony", "antonio toni tony", "edward ed eddie ted ned",
    "theodore theodor ted theo teddy", "samuel samuele sam sammy", "samantha sam",
    "katherine catherine kathryn katharine kate katie kathy cathy kat",
    "elizabeth elisabeth liz beth betsy eliza libby", "jennifer jen jenn jenny",
    "gregory greg", "jeffrey geoffrey jeff geoff", "jonathan jon jonny",
    "john jack johnny jon", "johannes johann hannes", "joshua josh",
    "lawrence laurence larry", "peter pete", "philip phillip philipp phil",
    "richard rick rich richie dick ricky", "ronald ron ronnie", "timothy tim timmy",
    "zachary zach zack", "frederick frederic fred freddie freddy", "friedrich fritz",
    "gabriel gabe", "gabriella gabrielle gabi gaby", "jacob jakob jake",
    "maximilian maximiliano max", "maxim maxime max", "nathan nathaniel nathanael nate nat",
    "patrick pat paddy", "patricia pat patty trish",
    "susan suzanne susanne susanna sue susie suzy", "abigail abby abbie",
    "margaret margarete meg maggie peggy", "douglas doug", "donald don donnie",
    "kenneth ken kenny", "raymond ray", "charles charlie chuck chas", "carl karl",
    "carlos carlo", "victoria vicky tori", "rebecca becky becca",
    "deborah debra deb debbie", "pamela pam", "jessica jess jessie",
    "stephanie stefanie steph", "albert al bert", "alfred alf fred", "harold harry hal",
    "henry harry hank hal", "heinrich heinz", "leonard leo len lenny", "leonardo leo",
    "vincent vince", "walter walther walt", "gerald gerry jerry", "gerard gerry",
    "jerome jerry", "terence terrence terry", "francis frank fran",
    "francisco paco pancho", "frances fran frankie", "ignacio nacho", "eugene gene",
    "martin marty", "mitchell mitch", "cynthia cindy", "emily emilie em emmy",
    "jacqueline jackie", "judith judy", "kimberly kimberley kim",
    "madeline madeleine madelyn maddie", "melissa mel", "melanie mel",
    "teresa theresa tess terry", "virginia ginny",
    "mohammed muhammad mohamed mohammad mohamad muhammed mo",
    "yusuf yousef youssef yosef", "giovanni gianni", "luis luiz",
]

_NICKNAMES: dict[str, set[int]] = {}
for _i, _group in enumerate(_NICKNAME_GROUPS):
    for _name in _group.split():
        _NICKNAMES.setdefault(_name, set()).add(_i)


def _dl1(a: str, b: str) -> bool:
    """True if a and b differ by at most one edit or adjacent swap ('?' matches any letter)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i] and a[i] != '?' and b[i] != '?']
        if len(diffs) <= 1:
            return True
        return (len(diffs) == 2 and diffs[1] == diffs[0] + 1
                and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]])
    short, long_ = (a, b) if la < lb else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return True
    return False


def given_tokens_match(a: str, b: str, *, strong: bool = False) -> bool:
    """Two normalized given-name tokens name the same person."""
    if a == b or _undigraph(a) == _undigraph(b):
        return True
    if len(a) == 1 or len(b) == 1:
        return a[0] == b[0]
    if _NICKNAMES.get(a, set()) & _NICKNAMES.get(b, set()):
        return True
    # A bare prefix is not enough ("Xiao"/"Xiaoyi" are different people), and
    # a typo never changes the first or last letter: Andrew/Andrea,
    # Daniel/Daniela and Mario/Maria are different people.
    return (strong and min(len(a), len(b)) >= 5 and a[0] == b[0] and a[-1] == b[-1]
            and _dl1(a, b))


def _given_match(ga: list[str], gb: list[str], *, strong: bool) -> bool:
    if not ga or not gb:
        return False
    shorter, longer = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
    return all(given_tokens_match(s, longer[i], strong=strong) for i, s in enumerate(shorter))


def _core(surname: list[str]) -> list[str]:
    core = [t for t in surname if t not in _PARTICLES]
    return core or surname


def _surname_match(sa: list[str], sb: list[str], *, strong: bool) -> bool:
    ca, cb = _core(sa), _core(sb)
    ja, jb = ''.join(ca), ''.join(cb)
    if not ja or not jb:
        return False
    if ja == jb or _undigraph(ja) == _undigraph(jb):
        return True
    if not strong:
        return False
    if min(len(ja), len(jb)) >= 5 and _dl1(ja, jb):
        return True
    # Compound surname written in full on one record only:
    # "Johansson Capusan" / "Capusan", "Queralto Olive" / "Queralto".
    shorter, longer = (ca, cb) if len(ca) < len(cb) else (cb, ca)
    return (len(shorter) < len(longer) and len(''.join(shorter)) >= 4
            and (longer[-len(shorter):] == shorter or longer[:len(shorter)] == shorter))


def same_person(first_a: str, last_a: str, first_b: str, last_b: str, *, strong: bool = False) -> bool:
    """True if two stored names plausibly belong to one person.

    Examples (default strength): "Andy"/"Andrew" Garin, "Amanda Y."/"Amanda" Agan,
    "I.L."/"Isabelle" Salle, "Maria Elena"/"M e" Bontempi, "Mauricio" Perez-Alaniz /
    "M." Perez Alaniz, "Maia" Guell/Güell, "Xuan S." Tam / "Xuan" S. Tam.
    Never: "Marcel"/"Quinn" Fafchamps, "Stephen A."/"Stephen L." Ross, "Erik"/"F. C." Stam.
    """
    ga, sa = split_name(first_a, last_a)
    gb, sb = split_name(first_b, last_b)
    if not sa or not sb:
        return False
    # Same tokens split differently between given names and surname:
    # "Lancelot Henry" + "de Frahan" / "Lancelot" + "Henry de Frahan".
    full_a = [t for t in ga + sa if t not in _PARTICLES]
    full_b = [t for t in gb + sb if t not in _PARTICLES]
    if full_a == full_b and len(full_a) >= 2:
        return True
    # Family name first on one record: "Fu" + "Chao" / "Chao" + "Fu".
    if (strong and len(full_a) >= 2 and sorted(full_a) == sorted(full_b)
            and all(len(t) > 1 for t in full_a)):
        return True
    if not _surname_match(sa, sb, strong=strong):
        return False
    return _given_match(ga, gb, strong=strong)


def match_score(first_a: str, last_a: str, first_b: str, last_b: str) -> int:
    """How specific a match is: 3 same name, 2 full first names agree, 1 initials only."""
    ga, sa = split_name(first_a, last_a)
    gb, sb = split_name(first_b, last_b)
    if ga + sa == gb + sb:
        return 3
    if ga and gb and len(ga[0]) > 1 and len(gb[0]) > 1:
        return 2
    return 1


def _distinctive(tokens: list[str]) -> list[str]:
    kept = [t for t in tokens if t not in _PARTICLES]
    return [t for t in kept if len(t) >= 3] or kept


def owner_named_in(first_name: str, last_name: str, text: str, url: str | None = None) -> bool:
    """True if a page plausibly names its owner.

    The page text (or HTML, which includes the title) contains a distinctive
    surname token as a word ("Adamecz" for Adamecz-Völgyi, "Peña" for
    Piedra-Peña), accent-, case- and hyphen-insensitive, also as German
    transliteration (Häusler/Haeusler) or run together (hanspeters.com);
    or the URL contains the surname or a given name ("nikhil.quest").
    Never flags an empty surname.
    """
    surname = _distinctive(split_name(first_name, last_name)[1] or _tokens(last_name))
    if not surname:
        return True
    hay = ' ' + _norm_text(text or '') + ' '
    hay_undigraph = _undigraph(hay)
    hay_joined = hay.replace(' ', '')
    for tok in surname:
        if '?' in tok:
            pattern = r'(?<![a-z])' + re.escape(tok).replace(r'\?', '[a-z]{1,2}') + r'(?![a-z])'
            if re.search(pattern, hay):
                return True
            continue
        if f' {tok} ' in hay or f' {_undigraph(tok)} ' in hay_undigraph:
            return True
        if len(tok) >= 5 and tok in hay_joined:
            return True
    # The owner's own given name on the page ("Nikhil", "Immanuel").
    given_words = [t for t in _tokens(first_name) if len(t) >= 4 and '?' not in t]
    if any(f' {t} ' in hay for t in given_words):
        return True
    if url:
        url_words = _norm_text(url).split()
        if any(tok in url_words for tok in surname):  # ".../Professoren/Wey"
            return True
        url_hay = ''.join(url_words)
        given = [t for t in _tokens(first_name) if len(t) >= 4]
        for tok in surname + given:
            if len(tok) >= 4 and '?' not in tok and (tok in url_hay or _undigraph(tok) in _undigraph(url_hay)):
                return True
    return False


# ---------------------------------------------------------------------------
# Clustering records that share evidence (an OpenAlex ID or a URL)
# ---------------------------------------------------------------------------

def cluster_same_person(records: list[dict], *, strong: bool = True) -> list[list[dict]]:
    """Split records known to share an identifier into same-person clusters.

    records: dicts with id, first_name, last_name. Returns clusters of two or
    more records whose names all agree pairwise. A record that agrees with two
    records that disagree with each other ("Steve Ross" next to "Stephen A."
    and "Stephen L.") is ambiguous and stays out of every cluster.
    """
    n = len(records)
    ok = [[i == j or same_person(records[i]['first_name'], records[i]['last_name'],
                                 records[j]['first_name'], records[j]['last_name'], strong=strong)
           for j in range(n)] for i in range(n)]
    ambiguous = {
        k for k in range(n)
        if any(ok[k][i] and ok[k][j] and not ok[i][j]
               for i in range(n) for j in range(i + 1, n) if k not in (i, j))
    }
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if ok[i][j] and i not in ambiguous and j not in ambiguous:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        if i not in ambiguous:
            groups.setdefault(find(i), []).append(i)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        if all(ok[i][j] for i in members for j in members):
            clusters.append([records[i] for i in members])
    return clusters


def canonical_order(row: dict) -> tuple:
    """Sort key for the surviving record: tracked URLs, followers, papers, age."""
    return (-int(row.get('n_urls') or 0), -int(row.get('n_follows') or 0),
            0 if row.get('openalex_author_id') else 1,
            -int(row.get('n_pubs') or 0), row['id'])


# ---------------------------------------------------------------------------
# Merging researcher records
# ---------------------------------------------------------------------------

_merge_listeners: list[Callable[[int, int], None]] = []


def on_merge(callback: Callable[[int, int], None]) -> None:
    """Register callback(duplicate_id, canonical_id), called after each merge."""
    _merge_listeners.append(callback)


_RESEARCHER_COLUMNS = ("first_name", "last_name", "position", "affiliation", "description",
                       "description_updated_at", "bio", "openalex_author_id", "has_top5_pub")


def _fetch_researcher(cursor, rid: int) -> dict | None:
    cursor.execute("SELECT * FROM researchers WHERE id = %s", (rid,))
    row = cursor.fetchone()
    if row is None or isinstance(row, dict):
        return row
    names = [d[0] for d in (cursor.description or [])]
    return dict(zip(names, row)) if len(names) == len(row) else dict(zip(_RESEARCHER_COLUMNS, row))


def merge_researcher_into(cursor, canonical_id: int, duplicate_id: int) -> bool:
    """Fold duplicate into canonical inside the caller's transaction (no commit).

    Moves authorship (keeping a real byline position over an owner-attached 0),
    URLs with their cached HTML, follows, JEL codes, fields, snapshots, URL
    discoveries and mining candidates; fills missing profile fields; keeps the
    canonical name unless the duplicate spells the same name out in full.
    Feed events belong to papers, so a merge never adds, removes or re-dates one.
    Returns False if either record no longer exists.
    """
    if canonical_id == duplicate_id:
        raise ValueError(f"Cannot merge researcher into itself (same id={canonical_id})")
    canonical = _fetch_researcher(cursor, canonical_id)
    duplicate = _fetch_researcher(cursor, duplicate_id)
    if not canonical or not duplicate:
        return False

    # Authorship: a shared paper keeps the canonical row, with the duplicate's
    # byline position when the canonical one was only attached as page owner.
    cursor.execute(
        """UPDATE authorship c JOIN authorship d
             ON d.publication_id = c.publication_id AND d.researcher_id = %s
           SET c.author_order = d.author_order
           WHERE c.researcher_id = %s AND COALESCE(c.author_order, 0) = 0
             AND COALESCE(d.author_order, 0) > 0""",
        (duplicate_id, canonical_id))
    cursor.execute(
        "DELETE FROM authorship WHERE researcher_id = %s "
        "AND publication_id IN (SELECT publication_id FROM "
        "(SELECT publication_id FROM authorship WHERE researcher_id = %s) AS tmp)",
        (duplicate_id, canonical_id))
    cursor.execute("UPDATE authorship SET researcher_id = %s WHERE researcher_id = %s",
                   (canonical_id, duplicate_id))

    # URLs: rows for a URL the canonical already tracks stay behind and are
    # removed (with their HTML cache) when the duplicate is deleted.
    cursor.execute("UPDATE IGNORE researcher_urls SET researcher_id = %s WHERE researcher_id = %s",
                   (canonical_id, duplicate_id))
    cursor.execute(
        """UPDATE html_content hc JOIN researcher_urls ru ON ru.id = hc.url_id
           SET hc.researcher_id = ru.researcher_id
           WHERE hc.researcher_id = %s""",
        (duplicate_id,))

    for table in ("user_follows", "researcher_jel_codes", "researcher_fields"):
        cursor.execute(f"UPDATE IGNORE {table} SET researcher_id = %s WHERE researcher_id = %s",
                       (canonical_id, duplicate_id))
    for table in ("researcher_snapshots", "url_discoveries", "site_mining_candidates"):
        try:
            cursor.execute(f"UPDATE {table} SET researcher_id = %s WHERE researcher_id = %s",
                           (canonical_id, duplicate_id))
        except Exception as exc:  # optional tables (mining) may not exist yet
            if getattr(exc, 'errno', None) != 1146:
                raise
    # The merged researcher has more papers: let the JEL job classify it again.
    try:
        cursor.execute("DELETE FROM researcher_jel_state WHERE researcher_id IN (%s, %s)",
                       (canonical_id, duplicate_id))
    except Exception as exc:
        if getattr(exc, 'errno', None) != 1146:
            raise

    # Keep the survivor's name unless the duplicate spells the same name out
    # in full ("J." -> "Jane Q."). Both are compared and stored without a
    # repeated surname: "Peter" + "Peter Hull Hull" stays "Peter", and a
    # survivor stored as "Peter Hull" + "Hull" is repaired to "Peter".
    cur_first = strip_repeated_surname(canonical['first_name'], canonical['last_name'])
    dup_first = strip_repeated_surname(
        strip_repeated_surname(duplicate['first_name'], duplicate['last_name']), canonical['last_name'])
    new_first = cur_first
    cg, cs = split_name(cur_first, canonical['last_name'])
    dg, ds = split_name(dup_first, duplicate['last_name'])
    if (cs == ds and len(dg) >= len(cg) and _given_match(cg, dg, strong=False)
            and all(len(c) == 1 or c == d for c, d in zip(cg, dg))
            and len(dup_first.rstrip('.')) > len(cur_first.rstrip('.'))
            and strip_repeated_surname(dup_first, canonical['last_name']) == dup_first):
        new_first = dup_first

    present = [k for k in _RESEARCHER_COLUMNS if k in canonical or k in duplicate]
    canonical = {k: canonical.get(k) for k in _RESEARCHER_COLUMNS}
    duplicate = {k: duplicate.get(k) for k in _RESEARCHER_COLUMNS}
    oa_id = canonical['openalex_author_id'] or duplicate['openalex_author_id']
    cursor.execute("DELETE FROM researchers WHERE id = %s", (duplicate_id,))
    if new_first != canonical['first_name']:
        cursor.execute("UPDATE researchers SET first_name = %s WHERE id = %s", (new_first, canonical_id))
    # Fill what the survivor lacks (bio exists only on migrated databases).
    sets, params = [], []
    for col in ("position", "affiliation", "description", "description_updated_at", "bio"):
        if col in present:
            sets.append(f"{col} = COALESCE({col}, %s)")
            params.append(duplicate[col])
    if "openalex_author_id" in present:
        sets.append("openalex_author_id = %s")
        params.append(oa_id)
    if "has_top5_pub" in present:
        sets.append("has_top5_pub = has_top5_pub OR %s")
        params.append(bool(duplicate['has_top5_pub']))
    if sets:
        cursor.execute(f"UPDATE researchers SET {', '.join(sets)} WHERE id = %s", (*params, canonical_id))

    logger.info("Merged researcher #%s (%s %s) into #%s (%s %s)",
                duplicate_id, duplicate['first_name'], duplicate['last_name'],
                canonical_id, canonical['first_name'], canonical['last_name'])
    for callback in list(_merge_listeners):
        try:
            callback(duplicate_id, canonical_id)
        except Exception:
            logger.exception("merge listener failed")
    return True


def researcher_weights(cursor, ids: Iterable[int]) -> list[dict]:
    """Rows with the fields canonical_order() ranks by."""
    ids = list(ids)
    if not ids:
        return []
    ph = ",".join(["%s"] * len(ids))
    cursor.execute(
        f"""SELECT r.id, r.first_name, r.last_name, r.openalex_author_id,
              (SELECT COUNT(*) FROM researcher_urls u WHERE u.researcher_id = r.id) AS n_urls,
              (SELECT COUNT(*) FROM user_follows f WHERE f.researcher_id = r.id) AS n_follows,
              (SELECT COUNT(*) FROM authorship a WHERE a.researcher_id = r.id) AS n_pubs
            FROM researchers r WHERE r.id IN ({ph})""",
        tuple(ids))
    cols = ("id", "first_name", "last_name", "openalex_author_id", "n_urls", "n_follows", "n_pubs")
    return [row if isinstance(row, dict) else dict(zip(cols, row)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# OpenAlex author IDs: one record per ID
# ---------------------------------------------------------------------------

def assign_openalex_author_id(conn, researcher_id: int, openalex_author_id: str) -> int | None:
    """Give a researcher an OpenAlex author ID without creating a second holder.

    - nobody holds the ID: store it (only if the researcher has none).
    - a record with a compatible name holds it: merge the two (the better
      record survives) and return the surviving ID.
    - only records with other names hold it: leave everything unchanged.

    Commits. Returns the researcher ID that now carries the OpenAlex ID, or None.
    """
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute("SELECT id, first_name, last_name, openalex_author_id FROM researchers "
                       "WHERE id = %s", (researcher_id,))
        me = cursor.fetchone()
        if not me or (me['openalex_author_id'] and me['openalex_author_id'] != openalex_author_id):
            conn.rollback()
            return None
        cursor.execute("SELECT id, first_name, last_name FROM researchers "
                       "WHERE openalex_author_id = %s AND id != %s",
                       (openalex_author_id, researcher_id))
        holders = cursor.fetchall()
        if not holders:
            cursor.execute("UPDATE researchers SET openalex_author_id = %s "
                           "WHERE id = %s AND openalex_author_id IS NULL",
                           (openalex_author_id, researcher_id))
            conn.commit()
            return researcher_id
        same = [h for h in holders if same_person(me['first_name'], me['last_name'],
                                                   h['first_name'], h['last_name'])]
        if len(same) != len(holders) or len(same) != 1:
            logger.info("Not assigning OpenAlex ID %s to researcher %s (%s %s): held by %s",
                        openalex_author_id, researcher_id, me['first_name'], me['last_name'],
                        [h['id'] for h in holders])
            conn.rollback()
            return None
        ranked = sorted(researcher_weights(cursor, [researcher_id, same[0]['id']]), key=canonical_order)
        canonical, duplicate = ranked[0]['id'], ranked[1]['id']
        merge_researcher_into(cursor, canonical, duplicate)
        cursor.execute("UPDATE researchers SET openalex_author_id = %s WHERE id = %s",
                       (openalex_author_id, canonical))
        conn.commit()
        return canonical
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Page ownership
# ---------------------------------------------------------------------------

OWNER_NOT_NAMED = "owner_not_named"
SHARED_PAGE = "shared_page"
MIN_JUDGED_TEXT = 1000  # characters of page text needed before OWNER_NOT_NAMED


# Pages keyed to one person by construction (often rendered by JavaScript, so
# the stored HTML may not show the name).
_PROFILE_REGISTRIES = re.compile(
    r'^https?://(www\.)?(orcid\.org/|scholar\.google\.[a-z.]+/citations|ideas\.repec\.org/[ef]/|'
    r'researchgate\.net/profile/|papers\.ssrn\.com/sol3/cf_dev/AbsByAuth|'
    r'lattes\.cnpq\.br/|buscatextual\.cnpq\.br/|(www\.)?researcherid\.com/rid/|'
    r'scholar\.archive\.org|dblp\.org/pid/)', re.IGNORECASE)


def _is_profile_registry(url: str | None) -> bool:
    return bool(url and _PROFILE_REGISTRIES.match(url.strip()))


# Personal-site hosts and per-person paths: a page there that does not print
# its owner's name (a Google Sites research tab) is still theirs.
_PERSONAL_PAGES = re.compile(
    r'^https?://([^/]*\.)?(sites\.google\.com|github\.io|weebly\.com|wordpress\.com|wixsite\.com)'
    r'|/~|/people/|/persons?/|/profiles?[/.?]|/staff/[^/?#]+', re.IGNORECASE)
_PERSONAL_PAGE_TYPES = frozenset({"home", "homepage", "personal"})


def _is_personal_page(url: str | None, page_type: str | None) -> bool:
    if page_type and page_type.strip().lower() in _PERSONAL_PAGE_TYPES:
        return True
    return bool(url and _PERSONAL_PAGES.search(url.strip()))


def page_ownership_problem(owners: list[dict], text: str | None, raw_html: str | None = None,
                           url: str | None = None, page_type: str | None = None) -> str | None:
    """Why a tracked page should not be treated as its owners' personal page.

    owners: every researcher tracking the URL (dicts with first_name, last_name).
    - SHARED_PAGE: tracked for people with unrelated names (department, news
      or institute pages such as stanford.edu/research). Extraction stops.
    - OWNER_NOT_NAMED: neither the page text, its HTML nor the URL names any
      owner (see owner_named_in). The page is kept but flagged for review,
      and its owner is not attached to items without a byline.
    Returns None when the page looks like the owner's own, when there is no
    stored page to judge, for pages under MIN_JUDGED_TEXT characters, profile
    registries (ORCID…), personal-site hosts (Google Sites, github.io…),
    per-person paths (/~x, /people/x, /profile) and home/personal page types.
    """
    if len(owners) > 1:
        for i, a in enumerate(owners):
            for b in owners[i + 1:]:
                if not same_person(a['first_name'], a['last_name'], b['first_name'], b['last_name'],
                                   strong=True):
                    return SHARED_PAGE
    text = text if isinstance(text, str) else None
    raw_html = raw_html if isinstance(raw_html, str) else None
    if not owners or not ((text or '').strip() or (raw_html or '').strip()):
        return None
    if _is_profile_registry(url) or _is_personal_page(url, page_type):
        return None
    # Error, login and bot-check pages are short; judge only real content.
    if len(_norm_text(text or raw_html)) < MIN_JUDGED_TEXT:
        return None
    # Text first; the (possibly multi-megabyte) HTML only when the text fails.
    for hay in (text, raw_html):
        if hay and any(owner_named_in(o['first_name'], o['last_name'], hay, url) for o in owners):
            return None
    return OWNER_NOT_NAMED


def matches_display_name(first_name: str, last_name: str, display_name: str, *, strong: bool = True) -> bool:
    """True if a stored name agrees with an OpenAlex display name ("Andrew Garin",
    "Lancelot Henry de Frahan"), trying each plausible surname split."""
    words = (display_name or "").split()
    for k in range(1, min(3, len(words) - 1) + 1):
        if same_person(first_name, last_name, ' '.join(words[:-k]), ' '.join(words[-k:]), strong=strong):
            return True
    return False
