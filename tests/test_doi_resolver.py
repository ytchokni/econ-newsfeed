"""Tests for DOI resolution from publisher URLs."""
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import patch, MagicMock

from doi_resolver import extract_doi_from_url, extract_pii_from_url, resolve_pii_via_crossref, resolve_doi


class TestExtractDoiFromUrl:
    """Pure regex extraction — no API calls."""

    def test_doi_in_springer_path(self):
        assert extract_doi_from_url(
            "https://link.springer.com/article/10.1007/s40641-016-0032-z"
        ) == "10.1007/s40641-016-0032-z"

    def test_doi_in_aea_query(self):
        assert extract_doi_from_url(
            "https://www.aeaweb.org/articles?id=10.1257/aer.20250278"
        ) == "10.1257/aer.20250278"

    def test_doi_in_uchicago_path(self):
        assert extract_doi_from_url(
            "https://www.journals.uchicago.edu/doi/10.1086/713733"
        ) == "10.1086/713733"

    def test_doi_org_direct(self):
        assert extract_doi_from_url(
            "https://doi.org/10.1093/qje/qjac020"
        ) == "10.1093/qje/qjac020"

    def test_doi_in_wiley_path(self):
        assert extract_doi_from_url(
            "https://onlinelibrary.wiley.com/doi/10.1111/ecpo.12149"
        ) == "10.1111/ecpo.12149"

    def test_pii_not_extracted_as_doi(self):
        assert extract_doi_from_url(
            "https://www.sciencedirect.com/science/article/pii/S0959378013002410"
        ) is None

    def test_no_doi_in_oup_path(self):
        assert extract_doi_from_url(
            "https://academic.oup.com/restud/article/83/1/87/2461318"
        ) is None

    def test_no_doi_in_jstor(self):
        assert extract_doi_from_url(
            "https://www.jstor.org/stable/41969212"
        ) is None

    def test_strips_query_params(self):
        assert extract_doi_from_url(
            "https://doi.org/10.1016/j.jebo.2024.106753?via=ihub"
        ) == "10.1016/j.jebo.2024.106753"

    def test_strips_trailing_slash(self):
        assert extract_doi_from_url(
            "https://link.springer.com/article/10.1007/s40641-016-0032-z/"
        ) == "10.1007/s40641-016-0032-z"

    def test_ssrn_abstract_url_synthesizes_doi(self):
        assert extract_doi_from_url(
            "https://ssrn.com/abstract=12345"
        ) is None  # no abstract_id param format

    def test_ssrn_papers_url_synthesizes_doi(self):
        assert extract_doi_from_url(
            "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5353691"
        ) == "10.2139/ssrn.5353691"

    def test_ssrn_with_extra_params(self):
        assert extract_doi_from_url(
            "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4238957&download=yes"
        ) == "10.2139/ssrn.4238957"

    def test_none_for_empty_string(self):
        assert extract_doi_from_url("") is None

    def test_strips_doi_from_wiley_asset_url(self):
        result = extract_doi_from_url(
            "https://onlinelibrary.wiley.com/store/10.1111/jeea.12174/asset/supinfo/jeea12174-sup-0001-SuppMat.zip"
        )
        assert result is None


class TestExtractPiiFromUrl:
    def test_sciencedirect_pii(self):
        assert extract_pii_from_url(
            "https://www.sciencedirect.com/science/article/pii/S0959378013002410"
        ) == "S0959378013002410"

    def test_sciencedirect_with_query_params(self):
        assert extract_pii_from_url(
            "https://www.sciencedirect.com/science/article/pii/S0927537125000715?via=ihub"
        ) == "S0927537125000715"

    def test_no_pii_in_non_sciencedirect(self):
        assert extract_pii_from_url(
            "https://link.springer.com/article/10.1007/s40641-016-0032-z"
        ) is None

    def test_no_pii_in_empty(self):
        assert extract_pii_from_url("") is None


class TestResolvePiiViaCrossref:
    def test_resolves_pii_to_doi(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "items": [{"DOI": "10.1016/j.gloenvcha.2013.12.011", "title": ["Smallholder farmer"]}]
            }
        }
        with patch("doi_resolver._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            result = resolve_pii_via_crossref("S0959378013002410")

        assert result == "10.1016/j.gloenvcha.2013.12.011"
        mock_session.return_value.get.assert_called_once()
        assert "alternative-id:S0959378013002410" in str(mock_session.return_value.get.call_args)

    def test_returns_none_on_no_results(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"message": {"items": []}}
        with patch("doi_resolver._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            assert resolve_pii_via_crossref("S0000000000000000") is None

    def test_returns_none_on_network_error(self):
        import requests as req
        with patch("doi_resolver._get_session") as mock_session:
            mock_session.return_value.get.side_effect = req.RequestException("timeout")
            assert resolve_pii_via_crossref("S0959378013002410") is None


class TestResolveDoi:
    def test_returns_doi_from_regex(self):
        with patch("doi_resolver.resolve_pii_via_crossref") as mock_cr:
            result = resolve_doi("https://link.springer.com/article/10.1007/s40641-016-0032-z")
        assert result == "10.1007/s40641-016-0032-z"
        mock_cr.assert_not_called()

    def test_resolves_pii_via_crossref(self):
        with patch("doi_resolver.resolve_pii_via_crossref", return_value="10.1016/j.gloenvcha.2013.12.011"):
            result = resolve_doi("https://www.sciencedirect.com/science/article/pii/S0959378013002410")
        assert result == "10.1016/j.gloenvcha.2013.12.011"

    def test_returns_none_for_unresolvable(self):
        result = resolve_doi("https://academic.oup.com/restud/article/83/1/87/2461318")
        assert result is None

    def test_returns_none_for_empty(self):
        assert resolve_doi("") is None
