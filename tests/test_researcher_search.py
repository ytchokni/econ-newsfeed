"""Researcher-tab search: normalisation, surname-first ranking, affiliation
words and the typo fallback (backend/database/researcher_index.py).

Each production probe from the search review is a case here. SQLite stands in
for MySQL as in test_name_search_ranking.py; the index does its own
case/accent folding, so SQLite's collation does not matter.
"""
import re
import sqlite3
from unittest.mock import patch

import pytest

from backend.database import researcher_index, researchers
from backend.database.researcher_index import (
    ResearcherIndex,
    _typo_cost,
    normalize_text,
    suggest_query,
)

# id, first, last, affiliation, publications, validated
PEOPLE = [
    (1, "Thomas", "Rieger", "German Institute for Economic Research", 7, True),
    (2, "Matthias", "Rieger", None, 1, True),
    (3, "Bastian", "Krieger", "ZEW Mannheim", 2, True),
    (4, "Thomas", "Riegert", None, 50, True),
    (5, "Andrea", "Chang", None, 1, True),
    (6, "Briana", "Chang", None, 15, True),
    (7, "Chang Suk", "Bae", None, 12, True),
    (8, "A. Y.", "Chang", None, 2, True),
    (9, "Eoghan", "O'Neill", "University College Dublin", 3, True),
    (10, "Stephen", "O’Neill", None, 1, True),
    (11, "John", "Van Reenen", "London School of Economics", 96, True),
    (12, "Daron", "Acemoglu", "MIT", 156, True),
    (13, "Bharat", "Diwakar", "Indian Institute of Technology Roorkee", 6, True),
    (14, "Anne", "Neumann", "DIW Berlin", 28, True),
    (15, "Jürgen", "Müller", None, 4, True),
    (16, "Paul", "Muller", None, 24, True),
    (17, "J. P.", "Morgan", None, 2, True),
    (18, "Thomas", "Addoah", None, 1, True),
    (19, "Hans", "Rieger", None, 3, False),   # coauthor only: chooser, not directory
    (20, "Mia", "Rieger", None, 0, True),     # no papers: hidden from the directory
    (21, "Shengwu", "Li", None, 9, True),     # two-letter names are real names (#263)
    (22, "Nick", "Wu", None, 2, True),
    (23, "Bo", "Xu", None, 4, True),
    (24, "HTT", "Tran", None, 3, True),       # initials stub in capitals
    (25, "HAN", "QIU", None, 2, True),        # a name written in capitals is not a stub
    (26, "S. A. C.", "Kittelsen", None, 5, True),
    (27, "J.-P.", "Benoit", None, 1, True),
]


def _connect():
    conn = sqlite3.connect(":memory:", check_same_thread=False)  # API test uses a threadpool
    conn.row_factory = sqlite3.Row
    conn.create_function("CONCAT_WS", -1, lambda sep, *parts: sep.join(p for p in parts if p is not None))
    conn.create_function("CHAR_LENGTH", 1, lambda s: len(s) if s is not None else None)
    conn.create_function(
        "REGEXP", 2, lambda pattern, value: value is not None and re.search(pattern, value) is not None
    )
    conn.create_function(
        "REGEXP_LIKE", 3, lambda value, pattern, flags: value is not None and re.search(pattern, value) is not None
    )
    conn.executescript("""
        CREATE TABLE researchers (
            id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, position TEXT,
            affiliation TEXT, description TEXT, openalex_author_id TEXT,
            has_top5_pub INTEGER DEFAULT 0
        );
        CREATE TABLE researcher_urls (researcher_id INTEGER);
        CREATE TABLE authorship (publication_id INTEGER, researcher_id INTEGER);
    """)
    paper = 1000
    for rid, first, last, affiliation, pubs, validated in PEOPLE:
        conn.execute(
            "INSERT INTO researchers (id, first_name, last_name, affiliation, openalex_author_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (rid, first, last, affiliation, "A" if validated else None),
        )
        for _ in range(pubs):
            paper += 1
            conn.execute("INSERT INTO authorship VALUES (?, ?)", (paper, rid))
    return conn


@pytest.fixture
def db(monkeypatch):
    conn = _connect()

    def execute(sql, params=()):
        sql = sql.replace("%s", "?").replace("= TRUE", "= 1")
        return conn.execute(sql, tuple(params))

    monkeypatch.setattr(researchers, "fetch_one", lambda sql, params=(): (lambda r: dict(r) if r else None)(execute(sql, params).fetchone()))
    monkeypatch.setattr(researchers, "fetch_all", lambda sql, params=(): [dict(r) for r in execute(sql, params).fetchall()])
    yield conn
    conn.close()


def ids(query, **filters):
    rows, total = researchers.search_researchers(search=query, limit=100, **filters)
    assert total == len(rows)
    return [r["id"] for r in rows]


def matches(query):
    rows, _ = researchers.search_researchers(search=query, limit=100)
    return [(r["id"], r["match"]) for r in rows]


# ---------------------------------------------------------------------------
# Probes from the production review
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", ["Thomas Rieger", "rieger, thomas", "  THOMAS  RIEGER "])
def test_exact_full_name_first_even_against_a_busier_prefix_match(db, query):
    # Thomas Riegert has 50 papers to Rieger's 7; the exact name still wins.
    assert ids(query) == [1, 4]


def test_partial_words_find_the_person(db):
    # Both Thomases start with "rie"; the busier one leads, no typo fallback.
    assert ids("thom rie") == [4, 1]


def test_typo_falls_back_to_similar_names_with_a_suggestion(db):
    rows = matches("Reiger")
    assert rows[:2] == [(1, "similar"), (2, "similar")]  # more papers first
    assert {rid for rid, _ in rows} >= {1, 2}
    assert researchers.researcher_search_suggestion("Reiger") == "Rieger"
    assert researchers.researcher_search_suggestion("thomas reiger") == "thomas Rieger"


def test_typo_in_famous_surname(db):
    assert matches("Acemglu") == [(12, "similar")]
    assert researchers.researcher_search_suggestion("Acemglu") == "Acemoglu"


def test_no_typo_fallback_when_the_query_already_matches(db):
    assert all(label != "similar" for _, label in matches("Rieger"))
    assert researchers.researcher_search_suggestion("Rieger") is None
    assert researchers.researcher_search_suggestion("thom rie") is None


@pytest.mark.parametrize("query", ["oneill", "O'Neill", "O’Neill", "o neill", "ONEILL"])
def test_apostrophes_and_spacing_are_ignored(db, query):
    assert ids(query) == [9, 10]


@pytest.mark.parametrize("query", ["vanreenen", "Van Reenen", "van-reenen", "Reenen", "John vanreenen"])
def test_particles_and_hyphens_are_ignored(db, query):
    assert ids(query) == [11]


@pytest.mark.parametrize("query", ["D Acemoglu", "D. Acemoglu", "acemoglu, d."])
def test_initials(db, query):
    assert ids(query) == [12]


def chooser(query):
    return [r["id"] for r in researchers.search_researcher_options(search=query)]


@pytest.mark.parametrize("query", ["A.Y. Chang", "A. Y. Chang", "AY Chang", "a y chang"])
def test_dotted_initials_match_exactly(db, query):
    # "A. Y." is an initials stub, so only the chooser lists it.
    assert chooser(query)[0] == 8


@pytest.mark.parametrize("query", ["jp morgan", "J.P. Morgan", "JP Morgan"])
def test_run_together_initials(db, query):
    assert chooser(query) == [17]


def test_affiliation_acronym_lists_institution_before_name_prefixes(db):
    # Thomas Rieger's affiliation is spelt out ("German Institute for Economic
    # Research"); the alias table makes it searchable as DIW. Diwakar only
    # starts with the letters, so he comes after the DIW researchers.
    assert matches("DIW") == [(14, "affiliation"), (1, "affiliation"), (13, "name")]


def test_name_and_institution_combined(db):
    assert ids("Rieger DIW") == [1]
    assert ids("DIW Rieger") == [1]
    assert ids("Van Reenen LSE") == [11]
    assert ids("Acemoglu MIT") == [12]


def test_surname_matches_rank_above_first_name_matches(db):
    # Changs by publications (15, 1 papers), then Chang Suk Bae. A. Y. Chang
    # is an initials stub and stays out of the directory.
    assert ids("Chang") == [6, 5, 7]


@pytest.mark.parametrize("query", ["muller", "Müller", "MULLER", "Muller"])
def test_accents_are_ignored_and_busier_researchers_first(db, query):
    assert ids(query) == [16, 15]


def test_whole_surname_before_prefix_before_inside_word(db):
    # Riegers by papers (7, 1), Riegert (prefix), Krieger (inside a word).
    assert ids("Rieger") == [1, 2, 4, 3]


def test_directory_hides_unlisted_researchers_but_chooser_finds_them(db):
    assert 19 not in ids("Hans Rieger") and 20 not in ids("Mia Rieger")
    chooser = [r["id"] for r in researchers.search_researcher_options(search="Hans Rieger")]
    assert chooser[0] == 19


def test_chooser_uses_the_same_normalisation(db):
    assert [r["id"] for r in researchers.search_researcher_options(search="oneill")] == [9, 10]
    assert [r["id"] for r in researchers.search_researcher_options(search="Reiger")][:3] == [1, 19, 2]
    row = researchers.search_researcher_options(search="vanreenen")[0]
    assert row == {"id": 11, "name": "John Van Reenen", "affiliation": "London School of Economics"}


def test_institution_filter_still_applies_to_search(db):
    assert ids("Rieger", institution="German Institute") == [1]
    assert ids("Chang", institution="Nowhere") == []


def test_pagination_follows_the_ranking(db):
    first, total = researchers.search_researchers(search="Rieger", limit=2, offset=0)
    second, _ = researchers.search_researchers(search="Rieger", limit=2, offset=2)
    assert [r["id"] for r in first + second] == [1, 2, 4, 3]
    assert total == 4


def test_punctuation_is_never_a_wildcard(db):
    assert ids("Rie%") == ids("Rie")
    rows, total = researchers.search_researchers(search="%")
    assert total == 20  # no words left: the plain alphabetical directory


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_returns_suggestion_and_match(client, db):
    with (
        patch("backend.api.get_urls_for_researchers", return_value={}),
        patch("backend.api.get_pub_counts_for_researchers", return_value={}),
        patch("backend.api.get_fields_for_researchers", return_value={}),
        patch("backend.api.get_jel_codes_for_researchers", return_value={}),
    ):
        typo = client.get("/api/researchers?search=Reiger").json()
        exact = client.get("/api/researchers?search=Rieger").json()
    assert typo["suggestion"] == "Rieger"
    assert [(r["id"], r["match"]) for r in typo["items"][:2]] == [(1, "similar"), (2, "similar")]
    assert exact["suggestion"] is None
    assert exact["items"][0]["match"] == "name"


# ---------------------------------------------------------------------------
# Index building blocks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, folded", [
    ("O'Neill", "oneill"),
    ("O’Neill", "oneill"),
    ("Jean-Pierre", "jean pierre"),
    ("A.Y. Chang", "a y chang"),
    ("Müller", "muller"),
    ("Ødegård, Łukasz", "odegard lukasz"),
    ("Straße", "strasse"),
    ("  Van   Reenen ", "van reenen"),
    ("50%_off", "50 off"),
    ("", ""),
    (None, ""),
])
def test_normalize_text(raw, folded):
    assert normalize_text(raw) == folded


def test_transposed_letters_cost_less_than_a_wrong_letter():
    assert _typo_cost("reiger", "rieger", 2) == 0.5
    assert _typo_cost("reiger", "geiger", 2) == 1
    assert _typo_cost("acemglu", "acemoglu", 2) == 1
    assert _typo_cost("smith", "jones", 1) > 1


def test_suggestion_keeps_the_display_spelling():
    assert suggest_query("O Nell", {"nell": "neill"}, "Eoghan O'Neill") == "O neill"
    assert suggest_query("onell", {"onell": "oneill"}, "Eoghan O'Neill") == "O'Neill"


def test_index_is_shared_and_rebuilt_after_reset(db):
    first = researchers._name_index()
    assert researchers._name_index() is first
    researcher_index.reset_index()
    assert researchers._name_index() is not first


def test_stale_index_keeps_serving_while_refreshing(db, monkeypatch):
    index = researchers._name_index()
    monkeypatch.setattr(index, "built_at", index.built_at - researcher_index.REFRESH_SECONDS - 1)
    started = []
    monkeypatch.setattr(researcher_index.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})())
    assert researchers._name_index() is index
    assert started == ["researcher-index-refresh"]
    researcher_index._refreshing.clear()


def test_short_words_only_match_word_starts():
    index = ResearcherIndex.build([
        {"id": 1, "first_name": "Anna", "last_name": "Kahn", "pubs": 1, "validated": 1},
        {"id": 2, "first_name": "Ian", "last_name": "Dean", "pubs": 1, "validated": 1},
    ])
    assert index.search("an", listed_only=True).ids == [1]


# ---------------------------------------------------------------------------
# Initials stubs vs short names (#263)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query, expected", [("Li", 21), ("Wu", 22), ("Xu", 23), ("Bo Xu", 23), ("HAN QIU", 25)])
def test_two_letter_and_capitalised_names_are_listed(db, query, expected):
    assert expected in ids(query)


@pytest.mark.parametrize("query, stub", [
    ("Chang", 8), ("Morgan", 17), ("Tran", 24), ("Kittelsen", 26), ("Benoit", 27),
])
def test_initials_only_stubs_are_hidden_from_directory(db, query, stub):
    assert stub not in ids(query)
    assert stub in chooser(query)


def test_listing_without_search_uses_the_same_rule(db):
    rows, _ = researchers.search_researchers(limit=100)
    listed = {r["id"] for r in rows}
    assert {21, 22, 23, 25} <= listed
    assert not listed & {8, 17, 24, 26, 27}


@pytest.mark.parametrize("first, last, stub", [
    ("A.", "Smith", True),
    ("A. Y.", "Chang", True),
    ("A.Y.", "Chang", True),
    ("J.-P.", "Benoit", True),
    ("B C", "O'Neill", True),
    ("HTT", "Tran", True),
    ("Emily", "O.", True),
    ("É.", "Durand", True),
    ("", "Smith", True),
    (None, "Smith", True),
    ("Shengwu", "Li", False),
    ("Bo", "Xu", False),
    ("Li", "Zhang", False),
    ("HAN", "QIU", False),
    ("Andrew C.", "Chang", False),
])
def test_is_initials_stub(first, last, stub):
    assert researcher_index.is_initials_stub(first, last) is stub
