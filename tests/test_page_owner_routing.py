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
    def test_openalex_id_with_typo_surname_matches_holder(self, one, fetch_all, llm):
        from backend.database.researchers import get_researcher_id
        fetch_all.return_value = [{"id": 5, "first_name": "Alexander", "last_name": "Torgovitsky"}]
        assert get_researcher_id("Alexander", "Torgovistky", openalex_author_id="A9") == 5
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
         patch.object(extraction, "deactivate_unowned_url") as deactivate, \
         patch.object(extraction, "_extract_full") as full:
        out = extraction.extract_one_url({"id": 1, "url": "http://www.stanford.edu/research",
                                          "researcher_id": 5655, "page_type": "research"})
    assert out.status == "empty"
    deactivate.assert_called_once_with("http://www.stanford.edu/research", "shared_page")
    mark.assert_called_once_with(1, "h1")
    full.assert_not_called()
