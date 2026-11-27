"""Researcher identity (#259 split records, #260 page owners, #262 unowned pages)."""
from unittest.mock import MagicMock, patch

import pytest

from backend.database import identity
from backend.database.identity import (
    OWNER_NOT_NAMED, SHARED_PAGE, assign_openalex_author_id, cluster_same_person,
    match_score, matches_display_name, merge_researcher_into, owner_named_in,
    page_ownership_problem, same_person,
)


# --------------------------------------------------------------- same_person

@pytest.mark.parametrize("a,b", [
    # paper 74682 (#259)
    (("Andrew", "Garin"), ("Andy", "Garin")),
    (("Amanda Y.", "Agan"), ("Amanda", "Agan")),
    (("Alexandre", "Mas"), ("Alex", "Mas")),
    # other #259 examples
    (("Isabelle", "Salle"), ("I.L.", "Salle")),
    (("Maria Elena", "Bontempi"), ("M e", "Bontempi")),
    (("Mauricio", "Perez-Alaniz"), ("M.", "Perez Alaniz")),
    (("Sigurd Mølster", "Galaasen"), ("S.", "Galaasen")),
    (("Sigurd", "Galaasen"), ("Sigurd Mølster", "Galaasen")),
    (("Maia", "Guell"), ("Maia", "Güell")),
    (("Björn", "Brey"), ("Bjorn", "Brey")),
    (("Andre", "Groeger"), ("Andre", "Gröger")),
    (("Gordon B.", "Dahl"), ("Gordon B.", "Dahl")),
    (("Nicholas W.", "Papageorge"), ("Nick", "Papageorge")),
    # "Supply and Demand with Market Heterogeneity" byline
    (("Lancelot Henry", "de Frahan"), ("Lancelot", "Henry de Frahan")),
    (("Magne", "Mogstad"), ("Magne Mogstad", "Mogstad")),
    # split quirks
    (("Xuan S.", "Tam"), ("Xuan", "S. Tam")),
    (("Emil Anton Libak", "Simonsen"), ("Emil A.", "L. Simonsen")),
    # #260 page owners vs byline stubs
    (("Dora L.", "Costa"), ("Dora", "Costa")),
    (("Morten Overgaard", "Ravn"), ("Morten O.", "Ravn")),
    (("Julia Ingrid", "Lane"), ("Julia I.", "Lane")),
    (("Henri L.F.", "de Groot"), ("H. L. F.", "de Groot")),
])
def test_same_person(a, b):
    assert same_person(*a, *b)
    assert same_person(*b, *a)


@pytest.mark.parametrize("a,b", [
    (("Alexander", "Torgovitsky"), ("Alexander", "Torgovistky")),  # transposed letters
    (("A.", "Torgovitsky"), ("Alexander", "Torgovistky")),
    (("Christopher J.", "Cronin"), ("Cristopher", "Cronin")),
    (("Jürgen W.", "Weibull"), ("J�rgen", "Weibull")),          # mojibake
    (("A.", "Johansson Capusan"), ("A.", "Capusan")),
    (("Albert", "Queralto Olive"), ("Albert", "Queralto")),
    (("Chao", "Fu"), ("Fu", "Chao")),
])
def test_same_person_needs_strong_evidence(a, b):
    assert not same_person(*a, *b)
    assert same_person(*a, *b, strong=True)


@pytest.mark.parametrize("a,b", [
    (("Marcel", "Fafchamps"), ("Quinn", "Fafchamps")),
    (("Stephen A.", "Ross"), ("Stephen L.", "Ross")),
    (("Erik", "Stam"), ("F. C.", "Stam")),
    (("Andrew", "Garin"), ("Andrea", "Garin")),
    (("Daniel", "Smith"), ("Daniela", "Smith")),
    (("Mario", "Rossi"), ("Maria", "Rossi")),
    (("Alexander", "Mayer"), ("Alexandra", "Mayer")),
    (("Xiao", "Han"), ("Xiaoyi", "Han")),
    (("Stefan", "Ross"), ("Stephen", "Ross")),
    (("H. L. F.", "de Groot"), ("S. P. T.", "Groot")),
    (("Joao", "Santos"), ("Joao M.C.", "Silva")),
    (("Jane", ""), ("Jane", "")),
])
def test_different_people(a, b):
    assert not same_person(*a, *b, strong=True)


def test_match_score_prefers_exact_then_full_names():
    assert match_score("Dora L.", "Costa", "Dora L.", "Costa") == 3
    assert match_score("Dora L.", "Costa", "Dora", "Costa") == 2
    assert match_score("Dora L.", "Costa", "D.", "Costa") == 1


def test_matches_openalex_display_name():
    assert matches_display_name("Lancelot", "Henry de Frahan", "Lancelot Henry de Frahan")
    assert matches_display_name("Andy", "Garin", "Andrew Garin")
    assert not matches_display_name("Quinn", "Fafchamps", "Marcel Fafchamps")


# ------------------------------------------------------------ clustering

def _r(rid, first, last, **kw):
    return {"id": rid, "first_name": first, "last_name": last, **kw}


def test_cluster_merges_agreeing_names_only():
    recs = [_r(7869, "Marcel", "Fafchamps"), _r(29938, "Quinn", "Fafchamps"),
            _r(1, "M.", "Fafchamps")]
    # "M." agrees with Marcel only, so it joins Marcel; Quinn stays apart.
    clusters = cluster_same_person(recs)
    assert [{r["id"] for r in c} for c in clusters] == [{7869, 1}]


def test_cluster_leaves_ambiguous_records_alone():
    recs = [_r(5829, "Steve", "Ross"), _r(50249, "Stephen A.", "Ross"), _r(50252, "Stephen L.", "Ross")]
    assert cluster_same_person(recs) == []


def test_cluster_garin_twins():
    recs = [_r(9583, "Andrew", "Garin"), _r(42608, "Andy", "Garin")]
    assert len(cluster_same_person(recs)) == 1


def test_canonical_order_prefers_tracked_then_followed_then_papers():
    rows = [_r(42608, "Andy", "Garin", n_urls=0, n_follows=0, n_pubs=16, openalex_author_id="A1"),
            _r(9583, "Andrew", "Garin", n_urls=2, n_follows=0, n_pubs=22, openalex_author_id="A1")]
    assert sorted(rows, key=identity.canonical_order)[0]["id"] == 9583


# ------------------------------------------------------------ merging

def _merge_cursor(canonical, duplicate):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [canonical, duplicate]
    return cursor


def _row(first, last, **kw):
    base = dict(first_name=first, last_name=last, position=None, affiliation=None, description=None,
                description_updated_at=None, bio=None, openalex_author_id=None, has_top5_pub=0)
    base.update(kw)
    return base


def test_merge_moves_everything_and_never_touches_feed_events():
    cursor = _merge_cursor(_row("Andrew", "Garin", openalex_author_id="A5"), _row("Andy", "Garin"))
    assert merge_researcher_into(cursor, 9583, 42608) is True
    sql = [c.args[0] for c in cursor.execute.call_args_list]
    joined = "\n".join(sql)
    for table in ("authorship", "researcher_urls", "html_content", "user_follows",
                  "researcher_jel_codes", "researcher_fields", "researcher_snapshots",
                  "url_discoveries", "site_mining_candidates"):
        assert table in joined, table
    assert "feed_events" not in joined
    assert any(s.startswith("DELETE FROM researchers") for s in sql)
    # A nickname never replaces the surviving name.
    assert not any("SET first_name" in s for s in sql)


def test_merge_keeps_byline_position_over_owner_attachment():
    cursor = _merge_cursor(_row("Dora L.", "Costa"), _row("Dora", "Costa"))
    merge_researcher_into(cursor, 16478, 67804)
    order_update = [c for c in cursor.execute.call_args_list if "SET c.author_order" in c.args[0]]
    assert order_update and order_update[0].args[1] == (67804, 16478)


def test_merge_spells_out_initials():
    cursor = _merge_cursor(_row("J.", "Smith"), _row("Jane Q.", "Smith"))
    merge_researcher_into(cursor, 1, 2)
    updates = [c.args[1] for c in cursor.execute.call_args_list if "SET first_name" in c.args[0]]
    assert updates == [("Jane Q.", 1)]


def test_merge_tolerates_missing_optional_table():
    cursor = _merge_cursor(_row("A", "B"), _row("A", "B"))
    missing = Exception("no table")
    missing.errno = 1146

    def execute(sql, params=None):
        if "site_mining_candidates" in sql:
            raise missing
    cursor.execute.side_effect = execute
    assert merge_researcher_into(cursor, 1, 2) is True


def test_merge_notifies_listeners():
    seen = []
    identity.on_merge(lambda dup, canon: seen.append((dup, canon)))
    try:
        merge_researcher_into(_merge_cursor(_row("A", "B"), _row("A", "B")), 1, 2)
    finally:
        identity._merge_listeners.pop()
    assert seen == [(2, 1)]


def test_merge_missing_record_is_a_no_op():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [None, None]
    assert merge_researcher_into(cursor, 1, 2) is False
    with pytest.raises(ValueError):
        merge_researcher_into(cursor, 3, 3)


# ------------------------------------------------- OpenAlex ID assignment

def _assign_conn(me, holders, weights=None):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [me]
    cursor.fetchall.side_effect = [holders, weights or []]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_assign_stores_free_id():
    conn, cursor = _assign_conn({"id": 42608, "first_name": "Andy", "last_name": "Garin",
                                 "openalex_author_id": None}, [])
    assert assign_openalex_author_id(conn, 42608, "A5") == 42608
    assert any("SET openalex_author_id" in c.args[0] for c in cursor.execute.call_args_list)
    conn.commit.assert_called_once()


def test_assign_merges_into_compatible_holder_instead_of_duplicating():
    conn, cursor = _assign_conn(
        {"id": 42608, "first_name": "Andy", "last_name": "Garin", "openalex_author_id": None},
        [{"id": 9583, "first_name": "Andrew", "last_name": "Garin"}],
        [{"id": 9583, "n_urls": 2, "n_follows": 0, "n_pubs": 22, "openalex_author_id": "A5"},
         {"id": 42608, "n_urls": 0, "n_follows": 0, "n_pubs": 16, "openalex_author_id": None}])
    with patch.object(identity, "merge_researcher_into") as merge:
        assert assign_openalex_author_id(conn, 42608, "A5") == 9583
    merge.assert_called_once_with(cursor, 9583, 42608)


def test_assign_refuses_id_held_by_someone_else():
    conn, cursor = _assign_conn(
        {"id": 29938, "first_name": "Quinn", "last_name": "Fafchamps", "openalex_author_id": None},
        [{"id": 7869, "first_name": "Marcel", "last_name": "Fafchamps"}])
    assert assign_openalex_author_id(conn, 29938, "A5000030753") is None
    assert not any("UPDATE researchers" in c.args[0] for c in cursor.execute.call_args_list)
    conn.commit.assert_not_called()


# ------------------------------------------------------- page ownership

LONG = "Research and publications of the institute. " * 40


def _owners(*names):
    return [{"id": i, "first_name": f, "last_name": l} for i, (f, l) in enumerate(names, 1)]


def test_stanford_research_page_is_shared():
    owners = _owners(("Ran", "Abramitzky"), ("Nicholas", "Bloom"), ("Kyle", "Bagwell"))
    assert page_ownership_problem(owners, LONG, None, "http://www.stanford.edu/research") == SHARED_PAGE


def test_health_policy_site_is_shared():
    owners = _owners(("Dana P.", "Goldman"), ("Bryan", "Tysinger"))
    assert page_ownership_problem(owners, LONG + " Goldman Tysinger", None,
                                  "http://healthpolicy.usc.edu") == SHARED_PAGE


def test_twins_on_one_page_are_not_a_shared_page():
    owners = _owners(("Gordon B.", "Dahl"), ("Gordon B.", "Dahl"))
    assert page_ownership_problem(owners, LONG + " Gordon Dahl", None,
                                  "https://econweb.ucsd.edu/~gdahl/research.html") is None


def test_page_that_never_names_owner():
    owners = _owners(("Costas", "Milas"))
    assert page_ownership_problem(owners, LONG, None, "http://www.liv.ac.uk/management/") == OWNER_NOT_NAMED


def test_owner_named_in_text_html_or_url():
    owners = _owners(("Dora L.", "Costa"))
    assert page_ownership_problem(owners, LONG + " Dora Costa", None) is None
    assert page_ownership_problem(owners, LONG, "<title>Dora L. Costa</title>") is None
    assert page_ownership_problem(_owners(("Christian", "Wey")), LONG, None,
                                  "http://www.dice.uni-duesseldorf.de/Institut/Professoren/Wey") is None


def test_short_pages_and_profile_registries_are_not_judged():
    owners = _owners(("Costas", "Milas"))
    assert page_ownership_problem(owners, "404 Page not found", None, "http://x.org") is None
    assert page_ownership_problem(owners, LONG, None, "https://orcid.org/0000-0002-3814-4507") is None
    assert page_ownership_problem(owners, None, None) is None


@pytest.mark.parametrize("first,last,text,url", [
    ("Anna", "Adamecz-Völgyi", "Passion meets data Anna Adamecz", None),
    ("Juan", "Piedra-Peña", "Juan A. Piedra Pena welcome", None),
    ("Konstantin", "Häusler", "", "https://hu.berlin/haeusler/publications"),
    ("Hans J.M.", "Peters", "", "https://sites.google.com/view/hanspeters/publications"),
    ("Wei", "Li", "Wei Li, Professor of Economics", None),
])
def test_owner_named_in(first, last, text, url):
    assert owner_named_in(first, last, text, url)


def test_short_surname_needs_a_whole_word():
    assert not owner_named_in("Wei", "Li", "Lisbon policy limits", None)


# ------------------------------------------- review fixes (PR #288 review)

@pytest.mark.parametrize("first,last,expected", [
    ("Peter Hull", "Hull", "Peter"),
    ("Ana Pérez", "Perez", "Ana"),
    ("Ann Marie Smith-Jones", "Smith-Jones", "Ann Marie"),
    ("Hull", "Hull", "Hull"),
    ("Hull Peter", "Hull", "Hull Peter"),
    ("Peter Hullman", "Hull", "Peter Hullman"),
])
def test_strip_repeated_surname(first, last, expected):
    assert identity.strip_repeated_surname(first, last) == expected


def _renames(canonical, duplicate):
    cursor = _merge_cursor(canonical, duplicate)
    merge_researcher_into(cursor, 1, 2)
    return [c.args[1] for c in cursor.execute.call_args_list if "SET first_name" in c.args[0]]


def test_merge_never_adopts_repeated_surname_spelling():
    assert _renames(_row("Peter", "Hull"), _row("Peter Hull", "Hull")) == []
    assert _renames(_row("Paola", "Conconi"), _row("Paola Conconi", "Conconi")) == []
    assert _renames(_row("Ricardo Duque", "Gabriel"), _row("Ricardo Duque Gabriel", "Gabriel")) == []
    assert _renames(_row("Ricardo", "Duque Gabriel"), _row("Ricardo Duque Gabriel", "Gabriel")) == []


def test_merge_repairs_survivor_stored_with_repeated_surname():
    assert _renames(_row("Peter Hull", "Hull"), _row("P.", "Hull")) == [("Peter", 1)]


@pytest.mark.parametrize("a,b", [
    (("Wei", "Zhang"), ("Wei", "Zheng")),
    (("Thomas", "Meyer"), ("Thomas", "Mayer")),
    (("Anna", "Bauer"), ("Anna", "Baier")),
    (("Li", "Wang"), ("Wang", "Li")),
])
def test_runtime_strength_keeps_lookalike_surnames_apart(a, b):
    assert not same_person(*a, *b)


def test_assign_does_not_merge_lookalike_surname_holder():
    conn, cursor = _assign_conn(
        {"id": 2, "first_name": "Wei", "last_name": "Zheng", "openalex_author_id": None},
        [{"id": 1, "first_name": "Wei", "last_name": "Zhang"}])
    with patch.object(identity, "merge_researcher_into") as merge:
        assert assign_openalex_author_id(conn, 2, "A1") is None
    merge.assert_not_called()


@pytest.mark.parametrize("owner,url,page_type,text", [
    (("Jane", "Doe"), "https://sites.google.com/view/janedoe/research", "research", LONG),
    (("Hao", "Dong"), "https://sites.google.com/view/haod/research", "research", LONG),
    (("Karol J.", "Borowiecki"), "https://example.org/~kborowiecki/research", "research", LONG),
    (("Antonis", "Ragkousis"), "https://www.kcl.ac.uk/people/antonis-ragkousis", "research", LONG),
    (("Jane", "Doe"), "https://jdoe.github.io/", "home", LONG),
    (("Olga", "Makeeva"), "https://www.hse.ru/org/hse/123", "research", LONG + " Ольга Макеева"),
    (("Immanuel", "Satya"), "https://example.org/research", "research", LONG + " Immanuel S. Lab"),
])
def test_personal_pages_are_not_flagged(owner, url, page_type, text):
    owners = _owners(owner)
    assert page_ownership_problem(owners, text, None, url, page_type) is None


@pytest.mark.parametrize("url,page_type", [
    # The reviewer's probes: a personal-looking host or path, or HOME, that
    # does not name the owner is still judged on its content.
    ("https://econ.uni.edu/people/faculty", "research"),
    ("https://econ.uni.edu/staff/directory", "research"),
    ("https://sites.google.com/view/econdept/papers", "research"),
    ("https://econ.uni.edu/research", "HOME"),
    ("https://econ.uni.edu/~econ/papers", "research"),
])
def test_personal_looking_urls_without_owner_name_are_flagged(url, page_type):
    owners = _owners(("Jane", "Doe"))
    assert page_ownership_problem(owners, LONG, None, url, page_type) == OWNER_NOT_NAMED


def test_given_name_alone_does_not_name_owner():
    assert not owner_named_in("Immanuel", "Satya", "Welcome, Immanuel Kant reading group", None)
    assert owner_named_in("Immanuel", "Satya", "Immanuel S. — research", None)
    assert not owner_named_in("Nikhil", "Rahangdale", "Home Research", "https://nikhil.quest")


@pytest.mark.parametrize("url,expected", [
    ("https://x.edu/people/publications", False),   # "li" inside a word
    ("https://x.edu/people/wei-li", True),
    ("https://x.edu/~wli/", True),
])
def test_short_surname_needs_name_around_it_in_url(url, expected):
    assert identity.url_names_owner("Wei", "Li", url) is expected
