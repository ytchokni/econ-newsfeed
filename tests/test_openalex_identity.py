"""Regression coverage for DOI/author cross-contamination in enrichment."""
from unittest.mock import MagicMock, patch

import pytest

from backend.enrichment import openalex
from backend.database.researchers import get_researcher_id, matches_author_name


TITLE = "Do Managerial Incentives Facilitate Anti-Competitive Behavior? Evidence from Collusion"
NASA_TITLE = "Reaching for the Society: The Commercialization Effects of NASA Technology Licensing"


def work(title=TITLE, author="Anja Rösner", doi="10.1016/j.jebo.2026.107625"):
    return {"title": title, "doi": doi, "openalex_id": "W123",
            "coauthors": [{"display_name": author, "openalex_author_id": "A123"}],
            "abstract": None, "year": "2026"}


@pytest.mark.parametrize("first,last,display,expected", [
    ("Johannes", "Huber", "Christoph Huber", False),
    ("Stefanie Jeanette", "Huber", "Christoph Huber", False),
    ("Jürgen", "Huber", "Jurgen Huber", True),
    ("C.", "Huber", "Christoph Huber", True),
    ("", "Huber", "Christoph Huber", False),
    ("Max", "Steinhardt", "Max Friedrich Steinhardt", True),
    ("Jan", "van der Meer", "Jan Pieter van der Meer", True),
    ("Jane", "Doe", "Jane Doe Smith", False),
])
def test_given_name_and_complete_surname_are_required(first, last, display, expected):
    assert matches_author_name(first, last, display) is expected


@pytest.mark.parametrize("candidate,title,author", [
    (work(title=NASA_TITLE), TITLE, "Anja Rösner"),
    (work(author="Christoph Huber"), TITLE, "Johannes Huber"),
    (work(), TITLE, "Rösner"),
    (work(title="Trade in 2025"), "Trade in 2024", "Anja Rösner"),
    (work(title="日本の金融政策"), "中国の金融政策", "Anja Rösner"),
    (work(title=""), "", "Anja Rösner"),
])
def test_unrelated_work_is_not_an_identity(candidate, title, author):
    assert not openalex.work_matches_paper(candidate, title, author)


def test_punctuation_case_and_initials_can_agree():
    assert openalex.work_matches_paper(work(title="TRADE & WAGES", author="A. Rösner"),
                                      "Trade and Wages", "Anja Rosner")


def test_wrong_doi_does_not_write_and_next_candidate_is_verified():
    bad = work(title=NASA_TITLE, doi="10.2139/ssrn.5008782")
    good = work()
    with (
        patch.object(openalex, "lookup_by_doi", side_effect=[bad, good]) as lookup,
        patch.object(openalex, "search_work") as search,
        patch.object(openalex, "update_openalex_data") as update,
        patch.object(openalex, "_backfill_researcher_openalex_ids"),
    ):
        assert openalex.enrich_publication(71119, TITLE, "Anja Rösner", doi=bad['doi'],
                                          doi_candidates=[bad['doi'], good['doi']])
    assert lookup.call_count == 2
    search.assert_not_called()
    assert update.call_args.kwargs['doi'] == good['doi']


def test_mismatch_cannot_mutate_paper_even_if_doi_resolves():
    with (
        patch.object(openalex, "lookup_by_doi", return_value=work(title=NASA_TITLE)),
        patch.object(openalex, "search_work", return_value=None),
        patch.object(openalex, "update_openalex_data") as update,
        patch.object(openalex, "_backfill_researcher_openalex_ids") as backfill,
    ):
        assert not openalex.enrich_publication(71119, TITLE, "Anja Rösner", doi="10.2139/ssrn.5008782")
    update.assert_not_called()
    backfill.assert_not_called()


def test_doi_candidates_are_bounded():
    with (
        patch.object(openalex, "lookup_by_doi", return_value=None) as lookup,
        patch.object(openalex, "search_work", return_value=None),
    ):
        assert not openalex.enrich_publication(1, TITLE, "Anja Rösner",
                                              doi_candidates=[f"10.2139/ssrn.{i}" for i in range(20)])
    assert lookup.call_count == 5


def test_title_search_rejects_same_author_unrelated_title():
    response = MagicMock(status_code=200)
    response.json.return_value = {"results": [{
        "id": "https://openalex.org/W4404576306", "title": NASA_TITLE,
        "authorships": [{"author": {"display_name": "Anja Rösner"}}],
    }]}
    session = MagicMock()
    session.get.return_value = response
    with (
        patch.object(openalex, "_get_session", return_value=session),
        patch.object(openalex, "_check_budget", return_value=True),
        patch.object(openalex, "_reserve_budget", return_value=True),
    ):
        assert openalex.search_work(TITLE, "Anja Rösner") is None


def test_backfill_does_not_spread_christoph_id_to_other_hubers():
    rows = [{"id": i, "first_name": first, "last_name": "Huber"}
            for i, first in enumerate(["Johannes", "Christoph", "Stefanie Jeanette", "Jürgen"], 1)]
    with (
        patch.object(openalex, "fetch_all", return_value=rows),
        patch.object(openalex, "execute_query") as update,
    ):
        openalex._backfill_researcher_openalex_ids(1, [
            {"display_name": "Christoph Huber", "openalex_author_id": "A5049313917"},
        ])
    update.assert_called_once()
    assert update.call_args.args[1] == ("A5049313917", 2)
    assert "AND openalex_author_id IS NULL" in update.call_args.args[0]


def test_ambiguous_initial_backfill_is_skipped():
    with (
        patch.object(openalex, "fetch_all", return_value=[{"id": 1, "first_name": "C", "last_name": "Huber"}]),
        patch.object(openalex, "execute_query") as update,
    ):
        openalex._backfill_researcher_openalex_ids(1, [
            {"display_name": "Christoph Huber", "openalex_author_id": "A1"},
            {"display_name": "Carla Huber", "openalex_author_id": "A2"},
        ])
    update.assert_not_called()


def test_poisoned_id_cannot_override_a_conflicting_name():
    with (
        patch("backend.database.researchers.fetch_one", return_value=None),
        patch("backend.database.researchers.fetch_all", side_effect=[
            [{"id": 1, "first_name": "Christoph", "last_name": "Huber"}], [],
        ]),
        patch("backend.database.researchers.execute_query", return_value=2) as insert,
    ):
        assert get_researcher_id("Johannes", "Huber", openalex_author_id="A5049313917") == 2
    assert insert.call_args.args[1][-1] is None


def test_shared_id_selects_only_one_name_compatible_record():
    with (
        patch("backend.database.researchers.fetch_one", return_value=None),
        patch("backend.database.researchers.fetch_all", return_value=[
            {"id": 1, "first_name": "Johannes", "last_name": "Huber"},
            {"id": 2, "first_name": "Christoph", "last_name": "Huber"},
        ]),
        patch("backend.database.researchers.execute_query") as update,
    ):
        assert get_researcher_id("Christoph M.", "Huber", openalex_author_id="A5049313917") == 2
    update.assert_not_called()


def test_queue_returns_all_linked_candidates_without_lexicographic_choice():
    from backend.database.papers import get_unenriched_papers
    with patch("backend.database.papers.fetch_all", side_effect=[
        [{"id": 71119, "link_doi": None}],
        [{"paper_id": 71119, "doi": doi} for doi in ["10.2139/ssrn.5008778", "10.1016/j.jebo.2026.107625", "10.2139/ssrn.5008778"]],
    ]) as fetch:
        row = get_unenriched_papers()[0]
    assert "MAX(pl.doi)" not in fetch.call_args_list[0].args[0]
    assert row['link_doi'] is None
    assert row['doi_candidates'] == ["10.2139/ssrn.5008778", "10.1016/j.jebo.2026.107625"]


def test_free_doi_lookup_still_runs_after_search_budget_exhaustion():
    with (
        patch.object(openalex, "_check_budget", return_value=False),
        patch.object(openalex, "get_unenriched_papers", return_value=[
            {"id": 1, "title": TITLE, "author_name": "Anja Rösner"},
            {"id": 2, "title": TITLE, "author_name": "Anja Rösner", "doi_candidates": ["10.1234/test"]},
        ]),
        patch.object(openalex, "enrich_publication", return_value=True) as enrich,
        patch.object(openalex, "execute_query") as mark,
        patch.object(openalex.time, "sleep"),
    ):
        assert openalex.enrich_new_publications() == 1
    assert enrich.call_args.kwargs['paper_id'] == 2
    assert mark.call_args.args[1] == (2,)
