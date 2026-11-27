"""Rename-aware merging of papers that share a strong link identifier (issue #265)."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.enrichment import rename_merge as rm
from backend.enrichment.paper_merge import merge_paper_group
from backend.enrichment.rename_merge import (
    classify_rename_pair, compatible_rename, identifier_key_from_doi, is_translation_pair,
    link_identifier_keys, titles_cooccur,
)
from backend.database.papers import normalize_title


class TestIdentifierKeys:
    @pytest.mark.parametrize("url,doi,expected", [
        ("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6082390", "10.2139/ssrn.6082390", {"ssrn:6082390"}),
        ("https://dx.doi.org/10.2139/ssrn.4480616", "10.2139/ssrn.4480616", {"ssrn:4480616"}),
        ("https://ssrn.com/abstract=5030329", None, {"ssrn:5030329"}),
        ("https://papers.ssrn.com/sol3/Delivery.cfm/x.pdf?abstractid=3517257&mirid=1", None, {"ssrn:3517257"}),
        ("https://www.nber.org/papers/w33054", None, {"nber:33054"}),
        ("https://www.nber.org/system/files/working_papers/w32538/w32538.pdf", None, {"nber:32538"}),
        ("https://doi.org/10.3386/w34595", "10.3386/w34595", {"nber:34595"}),
        ("https://arxiv.org/abs/2502.08296v2", None, {"arxiv:2502.08296"}),
        ("https://arxiv.org/pdf/2312.04827", None, {"arxiv:2312.04827"}),
        ("https://doi.org/10.48550/arXiv.2502.11243", None, {"arxiv:2502.11243"}),
        ("https://onlinelibrary.wiley.com/doi/abs/10.3982/ECTA13533", "10.3982/ECTA13533", {"doi:10.3982/ecta13533"}),
        ("https://www.aeaweb.org/articles?id=10.1257/pol.20230552&&from=f", "10.1257/pol.20230552&",
         {"doi:10.1257/pol.20230552"}),
        # A whole-book DOI is shared by every chapter: no evidence.
        ("https://link.springer.com/book/10.1007/978-3-319-13746-9", "10.1007/978-3-319-13746-9", set()),
        ("https://drive.google.com/file/d/abc/view", None, set()),
    ])
    def test_keys(self, url, doi, expected):
        assert link_identifier_keys(url, doi) == expected

    def test_chapter_doi_is_kept(self):
        assert identifier_key_from_doi("10.1007/978-3-319-13746-9_5") == "doi:10.1007/978-3-319-13746-9_5"


def paper(pid, title, authors, keys, openalex_id=None, paper_keys=()):
    return {"id": pid, "title": title, "author_ids": set(authors), "keys": set(keys),
            "paper_keys": set(paper_keys), "openalex_id": openalex_id}


FISCAL = paper(81297, "Fiscal Insurance", {1, 2}, {"ssrn:6082390"}, "W7204966283")
CROSS_POLICY = paper(125578, "Cross-Policy Risk Pricing", {1, 2}, {"ssrn:6082390"}, "W7204966283")


class TestCompatibleRename:
    def test_81297_125578_rename_sharing_ssrn_6082390(self):
        assert compatible_rename(FISCAL, CROSS_POLICY)

    def test_199604_434157_nested_author_sets(self):
        a = paper(199604, "Court Quality and Economic Resilience", {5}, {"ssrn:3517257"})
        b = paper(434157, "Court Quality and Propagation of Supply Chain Shocks", {5, 6}, {"ssrn:3517257"})
        assert compatible_rename(a, b)

    def test_disjoint_or_missing_authors_refused(self):
        assert not compatible_rename(FISCAL, {**CROSS_POLICY, "author_ids": {3}})
        assert not compatible_rename(FISCAL, {**CROSS_POLICY, "author_ids": {1, 3}})
        assert not compatible_rename(FISCAL, {**CROSS_POLICY, "author_ids": set()})

    def test_no_shared_key_refused(self):
        assert not compatible_rename(FISCAL, {**CROSS_POLICY, "keys": {"ssrn:1"}})

    def test_conflicting_identifier_of_same_kind_refused(self):
        a = {**FISCAL, "keys": {"ssrn:6082390", "nber:100"}}
        b = {**CROSS_POLICY, "keys": {"ssrn:6082390", "nber:200"}}
        assert not compatible_rename(a, b)
        assert not compatible_rename(FISCAL, {**CROSS_POLICY, "openalex_id": "W1"})
        assert not compatible_rename({**FISCAL, "paper_keys": {"doi:10.1/a"}},
                                     {**CROSS_POLICY, "paper_keys": {"doi:10.1/b"}})

    def test_translations_kept_apart(self):
        """35870/296934 and 49354/346379 share a link but are translations."""
        assert is_translation_pair("Effects of Fiscal Shocks on Brazilian Labor Market",
                                   "Efeitos dos Choques Fiscais sobre o Mercado de Trabalho Brasileiro")
        assert is_translation_pair("Les dynamiques individuelles de revenu salarial pendant la crise",
                                   "The Individual Dynamics of Wage Income in France During the Crisis")
        assert not is_translation_pair("Fiscal Insurance", "Cross-Policy Risk Pricing")
        a = paper(35870, "Effects of Fiscal Shocks on Brazilian Labor Market", {1}, {"doi:10.1/x"})
        b = paper(296934, "Efeitos dos Choques Fiscais sobre o Mercado de Trabalho Brasileiro", {1}, {"doi:10.1/x"})
        assert not compatible_rename(a, b)


def page(*entries):
    return normalize_title(" ... ".join(entries))


class TestCooccurrence:
    def test_neighbour_entries_on_one_page(self):
        html = page("Altruism and Risk Sharing in Networks, JEEA 2021",
                    "Altruism in Networks, Econometrica 2017")
        assert titles_cooccur("Altruism and Risk Sharing in Networks", "Altruism in Networks", html)

    def test_previously_titled_is_rename_evidence(self):
        html = page("A Separability Foundation for Random Coefficients Logit "
                    "(previously titled Independence of Irrelevant Decisions in Stochastic Choice)")
        assert not titles_cooccur("Independence of Irrelevant Decisions in Stochastic Choice",
                                  "A Separability Foundation for Random Coefficients Logit", html)
        html = page("Positioning Political Texts with Large Language Models by Asking and Averaging. "
                    "Working paper versions' earlier titles: Scaling Political Texts with ChatGPT")
        assert not titles_cooccur("Scaling Political Texts with ChatGPT",
                                  "Positioning Political Texts with Large Language Models by Asking and Averaging",
                                  html)

    def test_title_inside_the_other_is_not_a_second_entry(self):
        short = "Methods versus Substance: Measuring the Effects of Technology Shocks"
        long = short + " on Hours"
        assert not titles_cooccur(short, long, page(long))
        assert titles_cooccur(short, long, page(long, short))


class TestClassifyPair:
    BANKRUPTCY_OLD = paper(141290, "Wealth Protection in Bankruptcy and Serial Entrepreneurship", {7},
                           {"ssrn:5030329"}, "W4404644399")
    BANKRUPTCY_NEW = paper(176080, "Post-Bankruptcy Wealth and Serial Entrepreneurship", {7},
                           {"ssrn:5030329"}, "W4404644399")

    def test_ssrn_5030329_rename_across_page_versions(self):
        old_page = page("Wealth Protection in Bankruptcy and Serial Entrepreneurship")
        new_page = page("Post-Bankruptcy Wealth and Serial Entrepreneurship")
        assert classify_rename_pair(self.BANKRUPTCY_OLD, self.BANKRUPTCY_NEW, [old_page], [new_page]) == \
            (True, "rename")

    def test_both_titles_on_one_page_refused(self):
        a = paper(135479, "Altruism and Risk Sharing in Networks", {1, 2}, {"doi:10.3982/ecta13533"})
        b = paper(406249, "Altruism in Networks", {1, 2, 3}, {"doi:10.3982/ecta13533"})
        both = page("Altruism and Risk Sharing in Networks", "Altruism in Networks")
        assert classify_rename_pair(a, b, [both], [both]) == (False, "both_titles_on_one_page")

    def test_title_missing_from_its_pages_refused(self):
        assert classify_rename_pair(self.BANKRUPTCY_OLD, self.BANKRUPTCY_NEW, [], [page("x")]) == \
            (False, "title_not_on_source_page")


class TestFindRenameGroups:
    def test_4480616_three_papers_form_one_group(self):
        """SSRN 4480616: 402907 (Effect) is a rename of the 402944/402949 spelling variants."""
        papers = {
            402907: paper(402907, "The Effect of Mandatory Profit-Sharing on Workers and Firms: Evidence from France",
                          {9}, {"ssrn:4480616"}),
            402944: paper(402944, "The Effects of Mandatory Profit-Sharing on Workers and Firms: Evidence from France.",
                          {9}, {"ssrn:4480616"}, "W4381955671"),
            402949: paper(402949, "The Effects of Mandatory Profit Sharing on Workers and Firms: Evidence from France",
                          {9}, {"ssrn:4480616"}, "W4381955671"),
        }
        pairs = [(402907, 402944, "ssrn:4480616"), (402907, 402949, "ssrn:4480616")]
        pages = {402907: [page(papers[402907]["title"])], 402944: [page(papers[402944]["title"])],
                 402949: [page(papers[402949]["title"])]}
        with patch.object(rm, "load_rename_candidates", return_value=(pairs, papers)), \
             patch.object(rm, "_source_pages", return_value=pages):
            groups, refused = rm.find_rename_groups()
        assert groups == [[402907, 402944, 402949]] and refused == []

    def test_group_with_unverified_pair_is_refused(self):
        papers = {1: paper(1, "Alpha Paper Title", {1}, {"ssrn:1"}),
                  2: paper(2, "Beta Work Name", {1}, {"ssrn:1", "arxiv:2"}),
                  3: paper(3, "Gamma Study Here", {1}, {"arxiv:2"})}
        pairs = [(1, 2, "ssrn:1"), (2, 3, "arxiv:2")]
        pages = {pid: [page(p["title"])] for pid, p in papers.items()}
        with patch.object(rm, "load_rename_candidates", return_value=(pairs, papers)), \
             patch.object(rm, "_source_pages", return_value=pages):
            groups, refused = rm.find_rename_groups()
        assert groups == [] and refused[-1]["reason"] == "incomplete_group"


class TestLoadCandidates:
    def test_cleared_identifier_is_not_evidence(self):
        """A DOI #266 cleared for no author overlap no longer pairs the papers."""
        links = [
            {"paper_id": 135479, "url": "https://onlinelibrary.wiley.com/doi/abs/10.3982/ECTA13533", "doi": "10.3982/ECTA13533"},
            {"paper_id": 406249, "url": "https://onlinelibrary.wiley.com/doi/abs/10.3982/ECTA13533", "doi": "10.3982/ECTA13533"},
            {"paper_id": 81297, "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6082390", "doi": None},
            {"paper_id": 125578, "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6082390", "doi": None},
        ]
        cleared = [{"paper_id": 135479, "doi": "10.3982/ecta13533"}]
        rows = [{"id": 81297, "title": "Fiscal Insurance", "doi": None, "openalex_id": None, "status": None,
                 "discovered_at": None, "authors": "1,2"},
                {"id": 125578, "title": "Cross-Policy Risk Pricing", "doi": None, "openalex_id": None, "status": None,
                 "discovered_at": None, "authors": "1,2"}]
        with patch.object(rm, "fetch_all", side_effect=[links, cleared, rows]):
            pairs, papers = rm.load_rename_candidates()
        assert pairs == [(81297, 125578, "ssrn:6082390")]


class TestMergePaperGroupRename:
    @pytest.fixture
    def connection(self):
        with patch("backend.enrichment.paper_merge.get_connection") as get_conn:
            conn = MagicMock()
            conn.__enter__.return_value = conn
            get_conn.return_value = conn
            yield conn, conn.cursor.return_value

    @staticmethod
    def rows():
        return [
            {"id": 81297, "title": "Fiscal Insurance", "title_hash": "h1", "doi": "10.29412/res.wp.2026.13",
             "openalex_id": "W7204966283", "discovered_at": datetime(2026, 4, 10), "status": "working_paper",
             "venue": None, "abstract": None, "year": None},
            {"id": 125578, "title": "Cross-Policy Risk Pricing", "title_hash": "h2", "doi": "10.29412/res.wp.2026.13",
             "openalex_id": "W7204966283", "discovered_at": datetime(2026, 6, 11), "status": "working_paper",
             "venue": None, "abstract": None, "year": None},
        ]

    LINKS = [{"paper_id": pid, "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6082390", "doi": None}
             for pid in (81297, 125578)]
    AUTHORS = [{"publication_id": pid, "researcher_id": r} for pid in (81297, 125578) for r in (1, 2)]

    def test_rename_keeps_oldest_id_and_newest_title(self, connection):
        conn, cursor = connection
        cursor.fetchall.side_effect = [self.rows(), self.AUTHORS, self.LINKS, [], []]
        assert merge_paper_group([81297, 125578], rename=True)
        sql = [(c.args[0], c.args[1] if len(c.args) > 1 else None) for c in cursor.execute.call_args_list]
        assert ("DELETE FROM papers WHERE id = %s", (125578,)) in sql
        title = [args for q, args in sql if q.startswith("UPDATE papers SET title")]
        assert title == [("Cross-Policy Risk Pricing", "h2", 81297)]
        delete_at = next(i for i, (q, _) in enumerate(sql) if q.startswith("DELETE FROM papers"))
        title_at = next(i for i, (q, _) in enumerate(sql) if q.startswith("UPDATE papers SET title"))
        assert delete_at < title_at  # title_hash is unique
        conn.commit.assert_called_once()

    def test_without_rename_flag_a_rename_is_refused(self, connection):
        conn, cursor = connection
        cursor.fetchall.side_effect = [self.rows(), self.AUTHORS]
        assert merge_paper_group([81297, 125578]) is False
        conn.rollback.assert_called_once()

    def test_cleared_link_identifier_blocks_rename(self, connection):
        conn, cursor = connection
        cleared = [{"paper_id": 81297, "doi": "10.2139/ssrn.6082390"}]
        cursor.fetchall.side_effect = [self.rows(), self.AUTHORS, self.LINKS, cleared]
        assert merge_paper_group([81297, 125578], rename=True) is False
        conn.rollback.assert_called_once()
