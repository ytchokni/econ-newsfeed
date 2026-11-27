"""Re-verification of pre-#244 OpenAlex identifiers (issue #266)."""
from unittest.mock import MagicMock, patch

import pytest

from backend.enrichment import identifier_verification as iv
from backend.enrichment.identifier_verification import Verdict, classify


def work(wid, title, authors, doi=None, type_="article", abstract=None):
    return {
        "id": f"https://openalex.org/{wid}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title,
        "type": type_,
        "authorships": [{"author": {"display_name": a, "id": f"https://openalex.org/A{i}"}}
                        for i, a in enumerate(authors)],
        "abstract_inverted_index": ({w: [i] for i, w in enumerate(abstract.split())} if abstract else None),
        "publication_year": 2017,
    }


ALTRUISM_WORK = work("W1547689330", "Altruism in Networks",
                     ["Renaud Bourlès", "Yann Bramoullé", "Eduardo Perez-Richet"], doi="10.3982/ecta13533")
ALTRUISM_AUTHORS = [{"first_name": "Renaud", "last_name": "Bourlès"},
                    {"first_name": "Eduardo", "last_name": "Perez-Richet"}]
PHACK_WORK = work("W3025280704", "A Proposed Specification Check for p-Hacking",
                  ["Abel Brodeur", "Scott Cook", "Anthony Heyes"], doi="10.1257/pandp.20201078")


class TestClassify:
    def test_135479_jeea_paper_with_econometrica_id_is_cleared(self):
        """135479 carries 406249's Econometrica DOI; same authors, so only the
        exact title match to another paper is clear evidence."""
        paper = {"id": 135479, "title": "Altruism and Risk Sharing in Networks"}
        verdict = classify(paper, ALTRUISM_WORK, ALTRUISM_AUTHORS, title_owner_id=406249)
        assert verdict == Verdict("cleared", "belongs_to_other_paper", 406249, False)

    def test_406249_owner_is_verified(self):
        paper = {"id": 406249, "title": "Altruism in Networks"}
        assert classify(paper, ALTRUISM_WORK, ALTRUISM_AUTHORS, 406249).verdict == "verified"

    def test_56037_carries_56036_pandp_doi(self):
        paper = {"id": 56037, "title": "Econometrics as evidence? Examining the 'causal' connections "
                                       "between financial speculation and commodities prices"}
        authors = [{"first_name": "Robert", "last_name": "Ackrill"}]
        verdict = classify(paper, PHACK_WORK, authors, title_owner_id=56036)
        assert (verdict.verdict, verdict.matched_paper_id) == ("cleared", 56036)

    def test_no_author_overlap_clears_even_without_owner(self):
        paper = {"id": 1, "title": "Econometrics as evidence?"}
        verdict = classify(paper, PHACK_WORK, [{"first_name": "Robert", "last_name": "Ackrill"}])
        assert verdict.verdict == "cleared" and verdict.reason == "no_author_overlap"

    def test_87182_chapter_with_whole_book_doi(self):
        book = work("W2153688434", "Energy Technology and Valuation Issues", ["Andre Dorsman"],
                    doi="10.1007/978-3-319-13746-9", type_="book")
        paper = {"id": 87182, "title": "The impact of environmental strengths and concerns on the "
                                       "accounting performance of firms in the energy sector"}
        verdict = classify(paper, book, [{"first_name": "Andre", "last_name": "Dorsman"}])
        assert verdict.reason == "book_mismatch" and verdict.verdict == "cleared"

    def test_working_paper_title_with_shared_authors_goes_to_review(self):
        """OpenAlex often keeps the working-paper title: never clear on title alone."""
        wp = work("W1", "Altruism, Insurance and Networks", ["Renaud Bourlès", "Yann Bramoullé"])
        paper = {"id": 2, "title": "Altruism and Risk Sharing in Networks"}
        assert classify(paper, wp, ALTRUISM_AUTHORS) == Verdict("review", "title_differs")

    def test_same_title_without_author_overlap_is_review_not_clear(self):
        namesake = work("W2", "Altruism in Networks", ["Wei Zhang"])
        paper = {"id": 3, "title": "Altruism in Networks"}
        assert classify(paper, namesake, ALTRUISM_AUTHORS).reason == "title_match_no_author_overlap"

    def test_typo_of_owner_title_is_review(self):
        """40670 "...aversioin..." vs 387697: a duplicate, not a wrong identifier."""
        w = work("W3", "Stated and revealed inequality aversion in three subject pools", ["Simon Gächter"])
        paper = {"id": 40670, "title": "Stated and revealed inequality aversioin three subject pools"}
        verdict = classify(paper, w, [{"first_name": "Simon", "last_name": "Gächter"}], title_owner_id=387697)
        assert verdict.reason == "near_duplicate_of_other_paper"

    def test_missing_work_and_missing_authors_are_review(self):
        paper = {"id": 4, "title": "Some paper"}
        assert classify(paper, None, ALTRUISM_AUTHORS).reason == "work_not_found"
        assert classify(paper, PHACK_WORK, []).reason == "paper_has_no_authors"
        assert classify(paper, work("W5", "Other", []), ALTRUISM_AUTHORS).reason == "work_has_no_authors"

    def test_accent_and_initial_surname_overlap(self):
        """Bramoulle without the accent still overlaps; given-name mismatch keeps a title match verified."""
        w = work("W6", "Altruism in Networks", ["Y. Bramoulle"])
        paper = {"id": 5, "title": "Altruism in Networks"}
        authors = [{"first_name": "Yann", "last_name": "Bramoullé"}]
        assert classify(paper, w, authors).verdict == "verified"

    def test_abstract_copied_from_work_is_cleared_with_it(self):
        w = work("W7", "Unrelated Title", ["Wei Zhang"], abstract="We study networks")
        paper = {"id": 6, "title": "Altruism", "abstract": "We study  networks"}
        assert classify(paper, w, ALTRUISM_AUTHORS).clear_abstract is True
        paper["abstract"] = "Our own abstract"
        assert classify(paper, w, ALTRUISM_AUTHORS).clear_abstract is False


class TestReviewFixes:
    def test_own_book_with_subtitle_is_review(self):
        book = work("W8", "Capital in the Twenty-First Century: An Analysis", ["Thomas Piketty"], type_="book")
        paper = {"id": 10, "title": "Capital in the Twenty-First Century"}
        assert classify(paper, book, [{"first_name": "Thomas", "last_name": "Piketty"}]).reason == "book_title_variant"

    def test_edited_volume_is_review(self):
        book = work("W9", "NBER Macroeconomics Annual, Volume 35", ["Martin Eichenbaum"], type_="edited-book")
        paper = {"id": 11, "title": "NBER Macroeconomics Annual 2020"}
        authors = [{"first_name": "Martin", "last_name": "Eichenbaum"}]
        assert classify(paper, book, authors).verdict == "review"

    def test_umlaut_transliteration_overlaps(self):
        w = work("W10", "Published Title", ["Hans Müller"])
        paper = {"id": 12, "title": "Working Paper Title"}
        assert classify(paper, w, [{"first_name": "Hans", "last_name": "Mueller"}]).reason == "title_differs"

    def test_romanisation_variant_is_review(self):
        w = work("W11", "Published Title", ["Wei Zhou"])
        paper = {"id": 13, "title": "Working Paper Title"}
        assert classify(paper, w, [{"first_name": "Wei", "last_name": "Chou"}]).reason == "possible_name_variant"


class TestFetchWorks:
    def test_batches_filter_and_falls_back_to_singleton(self):
        ids = [f"W{i}" for i in range(60)]
        first = MagicMock(status_code=200)
        first.json.return_value = {"results": [work(f"W{i}", f"T{i}", ["A B"]) for i in range(49)]}
        second = MagicMock(status_code=200)
        second.json.return_value = {"results": [work(f"W{i}", f"T{i}", ["A B"]) for i in range(50, 60)]}
        missing = MagicMock(status_code=404)
        session = MagicMock()
        session.get.return_value = missing
        with patch.object(iv.openalex, "_get_session", return_value=session), \
             patch.object(iv.openalex, "_get_with_retry", side_effect=[first, second]) as get, \
             patch.object(iv.time, "sleep"):
            cache = {}
            works = iv.fetch_works(ids, cache)
        assert get.call_count == 2
        assert get.call_args_list[0].args[2]["filter"].count("|") == 49
        assert works["W49"] is None  # merged/deleted: singleton said 404
        assert works["W0"]["title"] == "T0"
        # A cached rerun spends nothing.
        with patch.object(iv.openalex, "_get_with_retry") as get_again:
            assert iv.fetch_works(ids, cache) == works
        get_again.assert_not_called()

    def test_budget_exhaustion_propagates(self):
        with patch.object(iv.openalex, "_get_session"), \
             patch.object(iv.openalex, "_get_with_retry", side_effect=iv.openalex._BudgetExhausted):
            with pytest.raises(iv.BudgetExhausted):
                iv.fetch_works(["W1"], {})

    def test_transient_singleton_error_is_not_cached(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"results": []}
        session = MagicMock()
        session.get.side_effect = iv.requests.ConnectionError("boom")
        with patch.object(iv.openalex, "_get_session", return_value=session), \
             patch.object(iv.openalex, "_get_with_retry", return_value=resp), \
             patch.object(iv.time, "sleep"):
            cache = {}
            assert iv.fetch_works(["W1"], cache) == {}
        assert cache == {}


class TestApplyVerdict:
    @pytest.fixture
    def cursor(self):
        with patch.object(iv, "get_connection") as get_conn:
            conn = MagicMock()
            conn.__enter__.return_value = conn
            get_conn.return_value = conn
            yield conn, conn.cursor.return_value

    PAPER = {"id": 56037, "openalex_id": "W3025280704", "doi": "10.1257/pandp.20201078", "title": "x"}

    def test_clear_removes_identifier_coauthors_topics_and_link_doi(self, cursor):
        conn, cur = cursor
        cur.fetchone.return_value = {"openalex_id": "W3025280704", "doi": "10.1257/pandp.20201078", "abstract": None}
        cur.fetchall.return_value = [{"id": 7, "doi": "10.1257/PANDP.20201078"}, {"id": 8, "doi": "10.1093/other"}]
        assert iv.apply_verdict(self.PAPER, PHACK_WORK, Verdict("cleared", "belongs_to_other_paper", 56036))
        sql = [c.args[0] for c in cur.execute.call_args_list]
        update = next(c for c in cur.execute.call_args_list if c.args[0].lstrip().startswith("UPDATE papers"))
        assert "openalex_id = NULL" in update.args[0] and "enrichment_attempted_at = NULL" in update.args[0]
        assert update.args[1] == (True, False, 56037)
        assert any("DELETE FROM openalex_coauthors" in s for s in sql)
        assert any("DELETE FROM paper_topics" in s for s in sql)
        link_updates = [c.args[1] for c in cur.execute.call_args_list if c.args[0].startswith("UPDATE paper_links")]
        assert link_updates == [(7,)]
        insert = next(c for c in cur.execute.call_args_list if "INSERT INTO openalex_identifier_checks" in c.args[0])
        assert insert.args[1][:5] == (56037, "W3025280704", "10.1257/pandp.20201078", "cleared", "belongs_to_other_paper")
        conn.commit.assert_called_once()

    def test_verified_only_records(self, cursor):
        conn, cur = cursor
        cur.fetchone.return_value = {"openalex_id": "W3025280704", "doi": "10.1257/pandp.20201078", "abstract": None}
        assert iv.apply_verdict(self.PAPER, PHACK_WORK, Verdict("verified", "title_and_author_match"))
        sql = [c.args[0] for c in cur.execute.call_args_list]
        assert not any(s.lstrip().startswith(("UPDATE", "DELETE")) for s in sql)
        assert any("INSERT INTO openalex_identifier_checks" in s for s in sql)

    def test_keeps_doi_that_is_not_the_works(self, cursor):
        _, cur = cursor
        cur.fetchone.return_value = {"openalex_id": "W3025280704", "doi": "10.9999/merged", "abstract": None}
        cur.fetchall.return_value = [{"id": 5, "doi": "10.9999/merged"}]
        paper = {**self.PAPER, "doi": "10.9999/merged"}
        iv.apply_verdict(paper, PHACK_WORK, Verdict("cleared", "no_author_overlap"))
        update = next(c for c in cur.execute.call_args_list if c.args[0].lstrip().startswith("UPDATE papers"))
        assert update.args[1][0] is False
        # The kept DOI's link is not nulled.
        assert not [c for c in cur.execute.call_args_list if c.args[0].startswith("UPDATE paper_links")]

    def test_skips_when_identifier_changed_since_planning(self, cursor):
        conn, cur = cursor
        cur.fetchone.return_value = {"openalex_id": "W999", "doi": None, "abstract": None}
        assert iv.apply_verdict(self.PAPER, PHACK_WORK, Verdict("cleared", "no_author_overlap")) is False
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


def test_plan_batch_uses_title_owner_lookup():
    papers = [{"id": 135479, "title": "Altruism and Risk Sharing in Networks", "openalex_id": "W1547689330",
               "doi": "10.3982/ecta13533"},
              {"id": 9, "title": "Pending", "openalex_id": "W404", "doi": None}]
    with patch.object(iv, "_authors_for", return_value={135479: ALTRUISM_AUTHORS}), \
         patch.object(iv, "find_papers_by_titles",
                      return_value={"Altruism in Networks": {"id": 406249, "title": "Altruism in Networks"}}):
        plan = iv.plan_batch(papers, {"W1547689330": ALTRUISM_WORK})
    assert [(p["id"], v.reason) for p, _, v in plan] == [(135479, "belongs_to_other_paper")]
