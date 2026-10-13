"""The curated feed must match journal identities rather than substrings."""
import sqlite3

import pytest

from backend.database.search_helpers import (
    TOP5_AND_FIELD_JOURNALS,
    top5_and_field_venue_clause,
)


def _matches(venue):
    # Execute the actual predicate: LOWER/TRIM/REPLACE/IN have the same
    # semantics for these ASCII names in SQLite and MySQL. No live DB needed.
    clause, params = top5_and_field_venue_clause()
    with sqlite3.connect(":memory:") as connection:
        return connection.execute(
            f"SELECT 1 FROM (SELECT ? AS venue) p WHERE {clause.replace('%s', '?')}",
            [venue, *params],
        ).fetchone() is not None


@pytest.mark.parametrize("journal", TOP5_AND_FIELD_JOURNALS)
def test_curated_journal_names_and_aliases_match(journal):
    for alias in journal:
        assert _matches(alias)
        assert _matches(f"  THE {alias.upper()}  ")


@pytest.mark.parametrize("venue", [
    "American Economic Review Insights",
    "AEJ Applied Economics",
    "AEJ: Policy",
    "Q.J.E.",
    "J.F.E.",
    "The Economic Journal",
])
def test_common_punctuation_and_article_variants_match(venue):
    assert _matches(venue)


@pytest.mark.parametrize("venue", [
    "European Journal of Political Economy",
    "Journal of Financial Econometrics",
    "Journal of Finance and Data Science",
    "American Economic Review: Papers and Proceedings",
    "American Economic Journal: Unknown Series",
    "Journal of Political Economy Microeconomics",
    "Chinese Journal of International Economics",
    "NBER Working Papers",
    "CEPR Discussion Papers",
    "SSRN",
    "Economics Letters",
    "Management Science",
    "",
    None,
])
def test_other_journals_and_working_paper_series_do_not_match(venue):
    assert not _matches(venue)


def test_curated_feed_filters_the_venue_displayed_on_each_event(monkeypatch):
    from backend.database import papers

    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY, title TEXT, year TEXT, venue TEXT,
                source_url TEXT, discovered_at TEXT, status TEXT, draft_url TEXT,
                abstract TEXT, draft_url_status TEXT, doi TEXT
            );
            CREATE TABLE feed_events (
                id INTEGER PRIMARY KEY, paper_id INTEGER, event_type TEXT,
                old_status TEXT, new_status TEXT, old_title TEXT, new_title TEXT,
                created_at TEXT, event_metadata_captured INTEGER, event_venue TEXT,
                event_source_url TEXT
            );
            CREATE TABLE authorship (publication_id INTEGER, researcher_id INTEGER);
        """)
        selected = "American Economic Review"
        excluded = "Economics Letters"
        fixtures = [
            (1, "status_change", selected, excluded, 1),
            (2, "status_change", excluded, selected, 1),
            (3, "status_change", selected, None, 0),
            (4, "status_change", selected, None, 1),
            (5, "new_paper", selected, excluded, 1),
            (6, "new_paper", excluded, selected, 1),
        ]
        for paper_id, event_type, venue, event_venue, captured in fixtures:
            connection.execute("INSERT INTO papers (id, venue) VALUES (?, ?)", (paper_id, venue))
            connection.execute(
                "INSERT INTO feed_events "
                "(id, paper_id, event_type, old_status, new_status, event_metadata_captured, event_venue) "
                "VALUES (?, ?, ?, 'working_paper', 'accepted', ?, ?)",
                (paper_id, paper_id, event_type, captured, event_venue),
            )
            connection.execute("INSERT INTO authorship VALUES (?, 1)", (paper_id,))

        def execute(sql, params):
            return connection.execute(sql.replace("%s", "?"), params)

        monkeypatch.setattr(papers, "fetch_one", lambda sql, params: dict(execute(sql, params).fetchone()))
        monkeypatch.setattr(papers, "fetch_all", lambda sql, params: [dict(row) for row in execute(sql, params)])

        rows, total, researcher_count = papers.search_feed_events(preset="top5_and_field")

        assert {row["paper_id"] for row in rows} == {2, 3, 5}
        assert total == 3
        assert researcher_count == 1
