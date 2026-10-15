"""Bounded chooser API and actual name matching against stored fixture rows."""
import sqlite3
from unittest.mock import patch

import pytest

from backend.database import researchers


def test_filter_options_never_downloads_researcher_directory(client):
    with patch("backend.api.fetch_all", side_effect=[[], [], []]) as fetch:
        response = client.get("/api/filter-options")
    assert response.status_code == 200
    assert response.json()["researchers"] == []
    assert fetch.call_count == 3
    assert all("first_name" not in call.args[0] for call in fetch.call_args_list)


@pytest.mark.parametrize("query", ["", "?search=Y", "?search=%20%20"])
def test_empty_or_short_queries_do_not_query_database(client, query):
    with patch("backend.api.search_researcher_options") as search:
        assert client.get("/api/researcher-options" + query).json() == {"items": []}
    search.assert_not_called()


@pytest.mark.parametrize("query", ["ids=-1", "ids=0", "ids=abc", "ids=1,", "ids=999999999999", "ids=1&search=Yun", "ids=" + ",".join(str(i) for i in range(1, 52))])
def test_invalid_selected_ids_are_rejected(client, query):
    with patch("backend.api.search_researcher_options") as search:
        assert client.get("/api/researcher-options?" + query).status_code == 400
    search.assert_not_called()


def test_selected_names_are_loaded_without_directory_search(client):
    with patch("backend.api.search_researcher_options", return_value=[{"id": 1, "name": "Youngjin Yun", "affiliation": None}]) as search:
        response = client.get("/api/researcher-options?ids=1,1")
    assert response.json()["items"][0]["name"] == "Youngjin Yun"
    search.assert_called_once_with(search="", researcher_ids=[1])


def test_names_match_all_terms_and_results_are_capped(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_function("CONCAT_WS", -1, lambda sep, *parts: sep.join(part for part in parts if part is not None))
    db.execute("CREATE TABLE researchers (id INTEGER, first_name TEXT, last_name TEXT, affiliation TEXT)")
    db.executemany("INSERT INTO researchers VALUES (?, ?, ?, NULL)", [(1, "Youngjin", "Yun"), (2, "Yun", "Liu"), (3, "Youngjin", "Kim")] + [(i, "Jane", f"Smith {i}") for i in range(4, 40)])
    monkeypatch.setattr(researchers, "fetch_all", lambda sql, params: [dict(row) for row in db.execute(sql.replace("%s", "?"), params)])
    assert [row["id"] for row in researchers.search_researcher_options(search="Youngjin Yun")] == [1]
    assert len(researchers.search_researcher_options(search="Jane")) == 20
    assert [row["id"] for row in researchers.search_researcher_options(researcher_ids=[2])] == [2]
    db.close()
