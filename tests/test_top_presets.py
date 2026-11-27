"""Exact "Top-5" journal and "Top-20" department presets (audit bugs 11 and 12)."""
import random
import re
import sqlite3
from unittest.mock import patch

import pytest

from backend.database import top_presets as tp


# ---------------------------------------------------------------------------
# Top-5 venues: only the five core journals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("venue", [
    "American Economic Review",
    "The American Economic Review",
    "AMERICAN ECONOMIC REVIEW",
    "AER",
    "Am. Econ. Rev.",
    "American Economic Review (Lead Article)",
    "American Economic Review, 114(10): 3007-3040.",
    "American Economic Review Vol. 108, No. 11 (Nov), pp. 3303-3800",
    "Econometrica",
    "ECTA",
    "Econometrica: Journal of the Econometric Society",
    "Econometrica, Econometric Society, vol. 90(2), pages 507-550, March",
    "Econometrica 80, 1505–1531",
    "Journal of Political Economy",
    "the Journal of Political Economy",
    "Journal of Political Economy (JPE)",
    "Journal of Polit ical Economy",
    "J. Polit. Econ.",
    "JPE",
    "Journal of Political Economy 127, no. 2",
    "Forthcoming in the Journal of Political Economy",
    "Quarterly Journal of Economics",
    "The Quarterly Journal of Economics 140(4)",
    "QJE",
    "Q. J. Econ.",
    "Federal Reserve Bank of Chicago Working Paper; NBER Working Paper; Quarterly Journal of Economics",
    "Review of Economic Studies",
    "The Review of Economic Studies",
    "Review of Economic Stud ies",
    "Rev. Econ. Stud.",
    "REStud",
    "RESTUD",
    "Review of Economic Studies, 90-3, pp. 1394–1440",
    "CEPR Discussion Paper 15539; The Review of Economic Studies",
])
def test_five_core_journals_match(venue):
    assert tp.is_top5_venue(venue)


@pytest.mark.parametrize("venue", [
    # Sister journals
    "American Economic Review: Insights",
    "AER: Insights",
    "AER Insights",
    "American Economic Review: Insight",
    "American Economic Review: Papers and Proceedings",
    "American Economic Review Papers & Proceedings",
    "American Economic Review, P&P",
    "AER P&P",
    "AER PP",
    "AEA Papers and Proceedings",
    "American Economic Review (AER) Papers and Proceedings",
    "American Economic Review Papers and Proceedings 99",
    "Journal of Political Economy Microeconomics",
    "Journal of Political Economy: Macroeconomics",
    "Journal of Political Economy - Macroeconomics",
    "JPE Micro",
    "JPE: Macro",
    "JPE-Macro",
    "American Economic Journal: Applied Economics",
    "AEJ: Macro",
    # Look-alikes
    "Scottish Journal of Political Economy",
    "Scottish Journal of Political Economy (Special Issue on Gender Norms)",
    "International Journal of Political Economy",
    "European Journal of Political Economy",
    "Nordic Journal of Political Economy",
    "Brazilian Journal of Political Economy",
    "The Independent Review. Journal of Political Economy",
    "Latin American Economic Review",
    "AERA Open",
    "AERA meetings",
    "AERC",
    "AERC Working Paper",
    "NCAER Working Paper",
    "FAERE Working Paper",
    "Equilibrium. Quarterly Journal of Economics and Economic Policy",
    "Quarterly Journal of Austrian Economics",
    "Review of Economic Studies Working Paper",
    "Chinese Journal of Aeronautics",
    # Not a publication at the journal
    "R&R at American Economic Review",
    "American Economic Review (R&R)",
    "Revise and resubmit, Econometrica",
    "Submitted to Econometrica",
    "jpe nber",
    "",
    None,
])
def test_sister_journals_lookalikes_and_rr_venues_do_not_match(venue):
    assert not tp.is_top5_venue(venue)


@pytest.mark.parametrize(("status", "counts"), [
    ("published", True),
    ("accepted", True),  # forthcoming papers are stored as accepted
    ("revise_and_resubmit", False),
    ("reject_and_resubmit", False),
    ("working_paper", False),
    ("work_in_progress", False),
    (None, False),
])
def test_only_published_or_accepted_papers_count(status, counts):
    assert tp.is_top5_publication("American Economic Review", status) is counts


def test_patterns_are_plain_sql_parameters():
    # No backslashes: MySQL string escaping cannot change the pattern.
    for pattern in (tp.TOP5_VENUE_PATTERN, tp.TOP20_DEPARTMENT_PATTERN, tp.TOP20_LOOKALIKE_PATTERN):
        assert "\\" not in pattern and "%" not in pattern
    clause, params = tp.top5_venue_clause("p.venue")
    assert params == [tp.TOP5_VENUE_PATTERN]
    assert clause.count("%s") == 1 and " LIKE " not in clause
    clause, params = tp.top5_publication_clause("p.venue", "p.status")
    assert "p.status IN ('published', 'accepted')" in clause
    clause, params = tp.top20_affiliation_clause("r.affiliation")
    assert clause.count("%s") == len(params) == 2 and " LIKE " not in clause


# ---------------------------------------------------------------------------
# Top-20 departments: whole words, satellite campuses excluded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("affiliation", [
    "MIT",
    "MIT Sloan School of Management",
    "Massachusetts Institute of Technology",
    "Harvard Business School",
    "Department of Economics, Columbia University",
    "Columbia Business School",
    "University of British Columbia and Columbia University",
    "University of California-Berkeley",
    "University of California, San Diego (UCSD)",
    "UC San Diego",
    "University of Wisconsin-Madison",
    "NYU Stern",
    "New York University",
    "Yale University",
    "Northwestern University, Kellogg",
    "University of Chicago Booth School of Business",
    "Wharton, University of Pennsylvania",
    "Boston University",
    "Carnegie Mellon University",
    "UCLA Anderson",
])
def test_top20_departments_match(affiliation):
    assert tp.is_top20_affiliation(affiliation)


@pytest.mark.parametrize("affiliation", [
    "University of British Columbia",
    "University of British Columbia Okanagan",
    "Government of the District of Columbia",
    "University of Missouri-Columbia",
    "Smith College",
    "Hobart and William Smith Colleges",
    "Johnson C. Smith University",
    "Adam Smith Institute",
    "Shadow Open Market Committee",
    "International Rescue Committee",
    "RMIT University",
    "Technische Hochschule Mittelhessen",
    "Institut für Mittelstandsforschung (IfM) Bonn",
    "Mittuniversitetet",
    "Amity University",
    "New York University Abu Dhabi",
    "NYU Abu Dhabi",
    "New York University Shanghai",
    "Yale-NUS College",
    "Duke Kunshan University",
    "Northwestern Polytechnical University",
    "University of Michigan-Dearborn",
    "University of Minnesota-Duluth",
    "University of Wisconsin-Whitewater",
    "University of Wisconsin–La Crosse",
    "Indiana University of Pennsylvania",
    "West Chester University of Pennsylvania",
    "State University of New York",
    "",
    None,
])
def test_top20_lookalikes_do_not_match(affiliation):
    assert not tp.is_top20_affiliation(affiliation)


# ---------------------------------------------------------------------------
# SQL execution (SQLite with MySQL's REGEXP functions supplied by Python)
# ---------------------------------------------------------------------------

def _sqlite():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.create_function("REGEXP_LIKE", 2, lambda s, p: s is not None and re.search(p, s) is not None)
    conn.create_function("REGEXP_REPLACE", 3, lambda s, p, r: None if s is None else re.sub(p, r, s))
    return conn


def test_has_top5_rule_ignores_rr_and_sister_journals():
    from backend.database import researchers

    conn = _sqlite()
    conn.executescript("""
        CREATE TABLE researchers (id INTEGER PRIMARY KEY, has_top5_pub INTEGER);
        CREATE TABLE researcher_urls (researcher_id INTEGER);
        CREATE TABLE papers (id INTEGER PRIMARY KEY, venue TEXT, status TEXT);
        CREATE TABLE authorship (researcher_id INTEGER, publication_id INTEGER);
    """)
    # researcher -> (stored flag, [(venue, status)])
    fixtures = {
        1: (1, [("American Economic Review", "published")]),            # keeps
        2: (1, [("Journal of Polit ical Economy", "accepted")]),        # keeps
        3: (1, [("American Economic Review: Insights", "published")]),  # sister
        4: (1, [("Scottish Journal of Political Economy", "published")]),
        5: (1, [("Econometrica", "revise_and_resubmit")]),              # R&R
        6: (1, [("Quarterly Journal of Economics", "working_paper")]),
        7: (0, [("Rev. Econ. Stud.", "published")]),                    # newly set
    }
    pid = 0
    for rid, (flag, pubs) in fixtures.items():
        conn.execute("INSERT INTO researchers VALUES (?, ?)", (rid, flag))
        conn.execute("INSERT INTO researcher_urls VALUES (?)", (rid,))
        for venue, status in pubs:
            pid += 1
            conn.execute("INSERT INTO papers VALUES (?, ?, ?)", (pid, venue, status))
            conn.execute("INSERT INTO authorship VALUES (?, ?)", (rid, pid))

    def fetch_one(sql, params):
        return dict(conn.execute(sql.replace("%s", "?"), params).fetchone())

    with patch.object(researchers, "fetch_one", fetch_one):
        changes = researchers.has_top5_flag_changes()

    assert changes == {
        "flagged_before": 6, "flagged_after": 3, "cleared": 4, "newly_set": 1,
        "tracked_before": 6, "tracked_after": 3,
    }


def test_refresh_has_top5_for_uses_the_publication_rule():
    from backend.database import researchers

    with patch.object(researchers, "execute_query") as execute:
        researchers.refresh_has_top5_for([5, 3, 5])
        researchers.refresh_has_top5_for([])
    assert execute.call_count == 1
    sql, params = execute.call_args[0]
    assert "p.status IN ('published', 'accepted')" in sql
    assert " LIKE " not in sql
    assert list(params) == [tp.TOP5_VENUE_PATTERN, 3, 5]


# ---------------------------------------------------------------------------
# Feed presets
# ---------------------------------------------------------------------------

def _feed_capture(**kwargs):
    from backend.database import papers

    with (
        patch.object(papers, "fetch_one", return_value={"cnt": 0}) as one,
        patch.object(papers, "fetch_all", return_value=[]) as all_,
    ):
        result = papers.search_feed_events(**kwargs)
    return result, one, all_


def test_feed_has_top5_uses_the_researcher_flag():
    result, one, all_ = _feed_capture(preset="has_top5")
    assert result == ([], 0)
    assert one.call_count == 1  # no separate COUNT(DISTINCT researcher_id) query
    sql, params = all_.call_args[0]
    assert "r2.has_top5_pub = TRUE" in sql
    assert "a3" not in sql and " LIKE " not in sql and "REGEXP" not in sql
    assert sql.count("%s") == len(params)


def test_feed_top20_uses_whole_word_matching():
    _, _, all_ = _feed_capture(preset="top20")
    sql, params = all_.call_args[0]
    assert "r.affiliation LIKE" not in sql
    assert tp.TOP20_DEPARTMENT_PATTERN in params
    assert sql.count("%s") == len(params)


def test_feed_has_top5_lists_papers_with_a_flagged_author(monkeypatch):
    """Executes the preset SQL. Query speed is guarded on MySQL in
    tests_data_quality/test_feed_event_quality.py (SQLite plans differ)."""
    from backend.database import papers

    rng = random.Random(7)
    n_papers, n_researchers, n_events = 3_000, 1_000, 1_000
    conn = _sqlite()
    conn.executescript("""
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY, title TEXT, year TEXT, venue TEXT, source_url TEXT,
            discovered_at TEXT, status TEXT, draft_url TEXT, abstract TEXT,
            draft_url_status TEXT, doi TEXT
        );
        CREATE TABLE researchers (id INTEGER PRIMARY KEY, has_top5_pub INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE authorship (researcher_id INTEGER, publication_id INTEGER);
        CREATE TABLE feed_events (
            id INTEGER PRIMARY KEY, paper_id INTEGER, event_type TEXT, old_status TEXT,
            new_status TEXT, old_title TEXT, new_title TEXT, created_at TEXT,
            event_metadata_captured INTEGER DEFAULT 0, event_venue TEXT, event_source_url TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO papers (id, title, venue, status) VALUES (?, ?, ?, 'published')",
        ((i, f"Paper {i}", rng.choice(["American Economic Review", "AER: Insights", None]))
         for i in range(1, n_papers + 1)),
    )
    flagged = {i for i in range(1, n_researchers + 1) if rng.random() < 0.08}
    conn.executemany(
        "INSERT INTO researchers VALUES (?, ?)",
        ((i, int(i in flagged)) for i in range(1, n_researchers + 1)),
    )
    authors = {pid: {rng.randint(1, n_researchers) for _ in range(rng.randint(1, 4))}
               for pid in range(1, n_papers + 1)}
    conn.executemany("INSERT INTO authorship VALUES (?, ?)",
                     [(rid, pid) for pid, rids in authors.items() for rid in rids])
    conn.executemany(
        "INSERT INTO feed_events (id, paper_id, event_type, new_status, created_at) "
        "VALUES (?, ?, 'new_paper', 'published', ?)",
        ((i, n_papers - i, f"2026-01-01 {i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}")
         for i in range(1, n_events + 1)),
    )

    def execute(sql, params):
        return conn.execute(sql.replace("%s", "?"), params).fetchall()

    monkeypatch.setattr(papers, "fetch_one", lambda sql, params: dict(execute(sql, params)[0]))
    monkeypatch.setattr(papers, "fetch_all", lambda sql, params: [dict(r) for r in execute(sql, params)])

    expected = [i for i in range(n_events, 0, -1) if authors[n_papers - i] & flagged]
    rows, total = papers.search_feed_events(preset="has_top5", limit=50)

    assert total == len(expected) > 50
    assert [r["event_id"] for r in rows] == expected[:50]
