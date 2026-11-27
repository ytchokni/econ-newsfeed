"""Page owners and byline routing in the pipeline (#259, #260, #262)."""
from unittest.mock import MagicMock, patch

import pytest

from backend.pipeline.paper_saver import (
    PaperSaver, _author_id_cache, _current_id, _forget_merged, _merged_into,
    _merge_openalex_twins, _owner_byline_index,
)

PAGE = "Dora L. Costa, Professor of Economics, UCLA. Papers and publications. " * 20


@pytest.fixture(autouse=True)
def _clear_caches():
    _author_id_cache.clear()
    _merged_into.clear()
    yield
    _author_id_cache.clear()
    _merged_into.clear()


def _conn(owners, page=(PAGE, None)):
    """Mock connection: owner lookup, stored page, then the save itself."""
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchone.return_value = None
    results = iter([owners, [page] if page else []])
    cursor.fetchall.side_effect = lambda *a, **k: next(results, [])
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cursor


def _authorships(cursor):
    return [c.args[1] for c in cursor.execute.call_args_list
            if "INSERT IGNORE INTO authorship" in c.args[0]]


def _save(conn, authors, url="http://www.econ.ucla.edu/costa/papers.html", **pub):
    with patch("backend.pipeline.paper_saver.get_connection", return_value=conn), \
         patch("backend.pipeline.paper_saver.compute_title_hash", return_value="h"), \
         patch("backend.pipeline.paper_saver._merge_compatible_authors"), \
         patch("backend.pipeline.paper_saver.get_researcher_id", side_effect=[500, 501, 502]) as rid:
        PaperSaver.save_publications(url, [{"title": "A Paper", "authors": authors, **pub}])
    return rid


def test_byline_name_of_owner_resolves_to_owner_not_stub():
    """Dora L. Costa (16478) owns the page; the byline says "Dora Costa"."""
    conn, cursor = _conn([(16478, "Dora L.", "Costa")])
    rid = _save(conn, [["Dora", "Costa"], ["Ziqi", "Zhao"]])
    assert _authorships(cursor) == [(16478, 1, 1), (500, 1, 2)]
    rid.assert_called_once_with("Ziqi", "Zhao", conn=conn)


def test_owner_middle_initial_variant_resolves_to_owner():
    """Event 16137: "Morten O. Ravn" on Morten Overgaard Ravn's page."""
    page = ("Morten Overgaard Ravn, UCL " * 60, None)
    conn, cursor = _conn([(8259, "Morten Overgaard", "Ravn")], page)
    _save(conn, [["Justinas", "Markauskas"], ["Morten O.", "Ravn"]], url="https://sites.google.com/ravn")
    assert (8259, 1, 2) in _authorships(cursor)
    assert not any(a[0] == 8259 and a[2] == 0 for a in _authorships(cursor))


def test_twin_owners_route_to_one_record_and_do_not_double_attach():
    """Gordon B. Dahl 24 and 17140 both track the page."""
    page = ("Gordon B. Dahl research " * 60, None)
    conn, cursor = _conn([(24, "Gordon B.", "Dahl"), (17140, "Gordon B.", "Dahl")], page)
    _save(conn, [["Gordon B.", "Dahl"], ["Anne C.", "Gielen"]],
          url="https://econweb.ucsd.edu/~gdahl/research.html")
    assert _authorships(cursor) == [(24, 1, 1), (500, 1, 2)]


def test_owner_attached_when_byline_lacks_them():
    conn, cursor = _conn([(16478, "Dora L.", "Costa")])
    _save(conn, [["Joseph", "Price"]])
    assert _authorships(cursor) == [(500, 1, 1), (16478, 1, 0)]


def test_shared_institution_page_credits_nobody_by_ownership():
    """stanford.edu/research is tracked for Abramitzky, Bloom and Bagwell."""
    owners = [(5655, "Ran", "Abramitzky"), (18349, "Nicholas", "Bloom"), (18796, "Kyle", "Bagwell")]
    conn, cursor = _conn(owners, ("Stanford news " * 100, None))
    _save(conn, [], url="http://www.stanford.edu/research")
    assert _authorships(cursor) == []


def test_page_that_never_names_owner_does_not_attach_owner():
    conn, cursor = _conn([(13000, "Costas", "Milas")], ("Management School news " * 100, None))
    _save(conn, [["Jane", "Doe"]], url="http://www.liv.ac.uk/management/")
    assert _authorships(cursor) == [(500, 1, 1)]


def test_page_without_stored_html_keeps_owner_attachment():
    conn, cursor = _conn([(16478, "Dora L.", "Costa")], page=None)
    _save(conn, [])
    assert _authorships(cursor) == [(16478, 1, 0)]


def test_owner_byline_index_takes_most_specific_match():
    owners = [{"id": 9, "first_name": "Dora L.", "last_name": "Costa"}]
    assert _owner_byline_index(owners, [("D.", "Costa"), ("Dora L.", "Costa")]) == {1: 9}
    assert _owner_byline_index(owners, [("", ""), ("Dora", "Costa")]) == {1: 9}
    assert _owner_byline_index(owners, [("Maria", "Costa")]) == {}


def test_merged_ids_are_followed_and_cache_repointed():
    _author_id_cache[("Andy", "Garin")] = 42608
    _forget_merged(42608, 9583)
    assert _author_id_cache[("Andy", "Garin")] == 9583
    assert _current_id(42608) == 9583
    assert _current_id(7) == 7


def test_layer2_merges_openalex_twins_on_one_paper():
    authors = [
        {"researcher_id": 9583, "first_name": "Andrew", "last_name": "Garin", "openalex_author_id": "A1"},
        {"researcher_id": 42608, "first_name": "Andy", "last_name": "Garin", "openalex_author_id": "A1"},
        {"researcher_id": 1, "first_name": "Crystal S.", "last_name": "Yang", "openalex_author_id": "A2"},
    ]
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    with patch("backend.pipeline.paper_saver.get_connection", return_value=conn), \
         patch("backend.database.identity.researcher_weights", return_value=[
             {"id": 9583, "n_urls": 2, "n_follows": 0, "n_pubs": 22, "openalex_author_id": "A1"},
             {"id": 42608, "n_urls": 0, "n_follows": 0, "n_pubs": 16, "openalex_author_id": "A1"}]), \
         patch("backend.database.identity.merge_researcher_into") as merge:
        assert _merge_openalex_twins(authors) is True
    assert merge.call_args.args[1:] == (9583, 42608)
    conn.commit.assert_called_once()


def test_layer2_leaves_different_people_with_one_id_alone():
    authors = [
        {"researcher_id": 7869, "first_name": "Marcel", "last_name": "Fafchamps", "openalex_author_id": "A1"},
        {"researcher_id": 29938, "first_name": "Quinn", "last_name": "Fafchamps", "openalex_author_id": "A1"},
    ]
    with patch("backend.pipeline.paper_saver.get_connection") as get_conn:
        assert _merge_openalex_twins(authors) is False
    get_conn.assert_not_called()


# ------------------------------------------------------------ resolution

class TestGetResearcherIdRoutesVariants:
    @patch("backend.database.researchers._disambiguate_researcher")
    @patch("backend.database.researchers.execute_query")
    @patch("backend.database.researchers.fetch_all")
    @patch("backend.database.researchers.fetch_one", return_value=None)
    def test_nickname_resolves_to_existing_record_without_renaming(self, one, fetch_all, execute, llm):
        from backend.database.researchers import get_researcher_id
        fetch_all.return_value = [{"id": 9583, "first_name": "Andrew", "last_name": "Garin"}]
        assert get_researcher_id("Andy", "Garin") == 9583
        llm.assert_not_called()
        assert not any("INSERT INTO researchers" in c.args[0] or "SET first_name" in c.args[0]
                       for c in execute.call_args_list)

    @patch("backend.database.researchers._disambiguate_researcher")
    @patch("backend.database.researchers.fetch_all")
    @patch("backend.database.researchers.fetch_one", return_value=None)
    def test_openalex_id_holder_with_variant_name_matches(self, one, fetch_all, llm):
        from backend.database.researchers import get_researcher_id
        fetch_all.return_value = [{"id": 5, "first_name": "Alexander", "last_name": "Torgovitsky"}]
        assert get_researcher_id("A.", "Torgovitsky", openalex_author_id="A9") == 5
        llm.assert_not_called()


class TestAddResearcherUrlRefusesSharedPages:
    @patch("backend.database.researchers.execute_query")
    @patch("backend.database.researchers.fetch_one",
           return_value={"first_name": "Ran", "last_name": "Abramitzky"})
    @patch("backend.database.researchers.fetch_all",
           return_value=[{"id": 18349, "first_name": "Nicholas", "last_name": "Bloom"}])
    def test_refuses_url_of_unrelated_researcher(self, fetch_all, one, execute):
        from backend.database.researchers import add_researcher_url
        add_researcher_url(5655, "personal", "http://www.stanford.edu/research")
        execute.assert_not_called()

    @patch("backend.database.researchers.execute_query")
    @patch("backend.database.researchers.fetch_one",
           return_value={"first_name": "Gordon B.", "last_name": "Dahl"})
    @patch("backend.database.researchers.fetch_all", return_value=[])
    def test_adds_unshared_url(self, fetch_all, one, execute):
        from backend.database.researchers import add_researcher_url
        add_researcher_url(24, "personal", "https://econweb.ucsd.edu/~gdahl/")
        execute.assert_called_once()


# ------------------------------------------------------------ extraction

@pytest.mark.real_page_owners
def test_extraction_deactivates_unowned_page_without_llm_call():
    from backend.pipeline import extraction
    payload = {"content": "Stanford news. " * 200, "raw_html": None, "content_hash": "h1",
               "timestamp": None, "extracted_at": None}
    owners = [{"id": 5655, "first_name": "Ran", "last_name": "Abramitzky"},
              {"id": 18349, "first_name": "Nicholas", "last_name": "Bloom"}]
    with patch.object(extraction.HTMLFetcher, "get_extraction_payload", return_value=payload), \
         patch.object(extraction.HTMLFetcher, "mark_extracted") as mark, \
         patch.object(extraction, "page_owners", return_value=owners), \
         patch.object(extraction, "url_owner_confirmed", return_value=False), \
         patch.object(extraction, "deactivate_unowned_url") as deactivate, \
         patch.object(extraction, "_extract_full") as full:
        out = extraction.extract_one_url({"id": 1, "url": "http://www.stanford.edu/research",
                                          "researcher_id": 5655, "page_type": "research"})
    assert out.status == "empty"
    deactivate.assert_called_once_with("http://www.stanford.edu/research", "shared_page")
    mark.assert_called_once_with(1, "h1")
    full.assert_not_called()


@pytest.mark.real_page_owners
def test_extraction_flags_page_that_never_names_owner_and_keeps_extracting():
    from backend.pipeline import extraction
    payload = {"content": "Management School news. " * 200, "raw_html": None, "content_hash": "h1",
               "timestamp": None, "extracted_at": None}
    with patch.object(extraction.HTMLFetcher, "get_extraction_payload", return_value=payload), \
         patch.object(extraction.HTMLFetcher, "mark_extracted"), \
         patch.object(extraction, "page_owners",
                      return_value=[{"id": 1, "first_name": "Costas", "last_name": "Milas"}]), \
         patch.object(extraction, "url_owner_confirmed", return_value=False), \
         patch.object(extraction, "deactivate_unowned_url") as deactivate, \
         patch.object(extraction, "flag_url_ownership") as flag, \
         patch.object(extraction, "_extract_full", return_value=extraction.ExtractionOutcome("empty")) as full:
        extraction.extract_one_url({"id": 1, "url": "http://www.liv.ac.uk/management/",
                                    "researcher_id": 1, "page_type": "research"})
    deactivate.assert_not_called()
    flag.assert_called_once_with("http://www.liv.ac.uk/management/", "owner_not_named")
    full.assert_called_once()


def test_byline_repeated_surname_is_stripped_before_lookup():
    conn, cursor = _conn([])
    rid = _save(conn, [["Peter Hull", "Hull"]], url="https://other.example")
    rid.assert_called_once_with("Peter", "Hull", conn=conn)


def test_owner_not_named_page_still_routes_byline_to_owner_but_never_attaches():
    page = ("Management School news " * 100, None, "research")
    conn, cursor = _conn([(13000, "Costas", "Milas")], page)
    _save(conn, [["Costas", "Milas"], ["Jane", "Doe"]], url="http://www.liv.ac.uk/management/")
    assert _authorships(cursor) == [(13000, 1, 1), (500, 1, 2)]
    conn2, cursor2 = _conn([(13000, "Costas", "Milas")], page)
    _save(conn2, [["Jane", "Doe"]], url="http://www.liv.ac.uk/management/")
    assert _authorships(cursor2) == [(500, 1, 1)]


@patch("backend.database.researchers._disambiguate_researcher")
@patch("backend.database.researchers.fetch_all", return_value=[])
@patch("backend.database.researchers.fetch_one", return_value={"id": 6330})
def test_get_researcher_id_strips_repeated_surname(one, fetch_all, llm):
    from backend.database.researchers import get_researcher_id
    assert get_researcher_id("Peter Hull", "Hull") == 6330
    assert one.call_args_list[0].args[1] == ("Peter", "Hull")



# ------------------------------------------------- admin-confirmed owner pages

@pytest.mark.real_page_owners
def test_confirmed_page_is_not_flagged_and_keeps_extracting():
    from backend.pipeline import extraction
    payload = {"content": "Research tab. " * 200, "raw_html": None, "content_hash": "h1",
               "timestamp": None, "extracted_at": None}
    with patch.object(extraction.HTMLFetcher, "get_extraction_payload", return_value=payload), \
         patch.object(extraction.HTMLFetcher, "mark_extracted"), \
         patch.object(extraction, "page_owners",
                      return_value=[{"id": 1, "first_name": "Christopher A.", "last_name": "Hartwell"}]), \
         patch.object(extraction, "url_owner_confirmed", return_value=True), \
         patch.object(extraction, "flag_url_ownership") as flag, \
         patch.object(extraction, "_extract_full", return_value=extraction.ExtractionOutcome("empty")):
        extraction.extract_one_url({"id": 1, "url": "https://sites.google.com/view/institutionaleconomist",
                                    "researcher_id": 1, "page_type": "research"})
    flag.assert_called_once_with("https://sites.google.com/view/institutionaleconomist", None)


def test_confirmed_page_credits_owner_for_bylineless_items():
    page = ("Research tab " * 100, None, "research", "owner_confirmed")
    conn, cursor = _conn([(77, "Christopher A.", "Hartwell")], page)
    _save(conn, [], url="https://sites.google.com/view/institutionaleconomist")
    assert _authorships(cursor) == [(77, 1, 0)]


def test_confirmed_shared_page_is_still_shared():
    from backend.database.identity import SHARED_PAGE, page_ownership_problem
    owners = [{"first_name": "Ran", "last_name": "Abramitzky"}, {"first_name": "Nicholas", "last_name": "Bloom"}]
    assert page_ownership_problem(owners, "x " * 2000, None, "http://www.stanford.edu/research",
                                  confirmed=True) == SHARED_PAGE


class TestConfirmUrlOwner:
    @patch("backend.database.researchers.refresh_has_top5_for")
    @patch("backend.database.researchers.execute_query")
    @patch("backend.database.researchers.fetch_all")
    @patch("backend.database.researchers.fetch_one", return_value={"url": "https://u", "researcher_id": 77})
    def test_confirms_and_credits_only_bylineless_items(self, one, fetch_all, execute, refresh):
        from backend.database.researchers import confirm_url_owner
        fetch_all.side_effect = [[{"researcher_id": 77}], [{"paper_id": 5}, {"paper_id": 6}]]
        assert confirm_url_owner(10) == {"url": "https://u", "papers_credited": 2}
        sql = [c.args for c in execute.call_args_list]
        assert "SET ownership_flag = 'owner_confirmed'" in sql[0][0] and sql[0][1] == ("https://u",)
        assert [a[1] for a in sql[1:]] == [(77, 5), (77, 6)]
        orphan_query = fetch_all.call_args_list[1].args[0]
        assert "NOT EXISTS (SELECT 1 FROM authorship" in orphan_query
        refresh.assert_called_once_with([77])

    @patch("backend.database.researchers.execute_query")
    @patch("backend.database.researchers.fetch_one", return_value=None)
    def test_unknown_url_returns_none(self, one, execute):
        from backend.database.researchers import confirm_url_owner
        assert confirm_url_owner(999) is None
        execute.assert_not_called()

    @patch("backend.database.researchers.execute_query")
    def test_flagging_never_overrides_confirmation(self, execute):
        from backend.database.researchers import flag_url_ownership
        flag_url_ownership("https://u", "owner_not_named")
        flag_url_ownership("https://u", None)
        for c in execute.call_args_list:
            assert "owner_confirmed" in c.args[0]
