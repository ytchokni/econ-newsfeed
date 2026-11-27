"""Researcher opt-out: tombstone checks, crawl/extraction exclusion, removal, crawler identity."""
import json
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.robotparser import RobotFileParser

import pytest

import backend.optout as optout
from backend.database import optouts
from backend.database.optouts import full_name_hash, name_hash, url_hash, url_key_hash


# ---------------------------------------------------------------------------
# Tombstone hashing
# ---------------------------------------------------------------------------

def test_name_hash_ignores_case_whitespace_and_unicode_form():
    assert name_hash("José", "García") == name_hash(" JOSÉ ", "garcía")
    assert name_hash("Jane", "Doe") == full_name_hash("jane   doe")
    assert name_hash("Jane", "Doe") != name_hash("Jane", "Dough")


def test_url_key_hash_matches_aliases_but_url_hash_is_exact():
    assert url_key_hash("https://www.janedoe.example/papers/") == url_key_hash("https://janedoe.example/papers")
    assert url_hash("https://janedoe.example/") != url_hash("https://janedoe.example")
    assert url_key_hash("not a url") is None


def test_url_optout_query_checks_exact_and_alias():
    sql, params = optouts.url_optout_query("https://www.janedoe.example/")
    assert "url_hash = %s OR url_key_hash = %s" in sql
    assert params == (url_hash("https://www.janedoe.example/"), url_key_hash("https://janedoe.example"))


# ---------------------------------------------------------------------------
# Crawl and extraction never touch opted-out URLs, whatever their is_active
# ---------------------------------------------------------------------------

def test_crawl_list_excludes_opted_out_urls_including_too_large_rechecks():
    from backend.researcher import Researcher
    with patch("backend.researcher.fetch_all", return_value=[]) as fetch:
        Researcher.get_all_researcher_urls()
    sql = " ".join(fetch.call_args.args[0].split())
    assert "researcher_optout_urls" in sql and "SHA2(ru.url, 256)" in sql
    # The opt-out guard applies to both ways a URL becomes eligible: active,
    # or a response_too_large URL due for its recheck (which reactivates it).
    where = sql.split(" WHERE ", 1)[1]
    assert where.startswith("(ru.is_active = TRUE OR (ru.deactivation_reason = 'response_too_large'")
    assert where.index("researcher_optout_urls") > where.index("INTERVAL %s DAY))")
    assert ") AND NOT EXISTS" in where


def test_extraction_queue_excludes_opted_out_urls():
    from backend.database.researchers import get_urls_needing_extraction
    with patch("backend.database.researchers.fetch_all", return_value=[]) as fetch:
        get_urls_needing_extraction((5,))
    sql = fetch.call_args.args[0]
    assert "researcher_optout_urls" in sql and "SHA2(ru.url, 256)" in sql
    assert sql.index("researcher_optout_urls") < sql.index("ORDER BY")


# ---------------------------------------------------------------------------
# Re-adding is refused
# ---------------------------------------------------------------------------

def _optout_hit_for(sql, params=None):
    return {"hit": 1} if "researcher_optouts" in sql or "researcher_optout_urls" in sql else None


def test_coauthor_extraction_does_not_recreate_opted_out_researcher():
    from backend.database.researchers import get_researcher_id
    with patch("backend.database.researchers.fetch_one", side_effect=_optout_hit_for), \
         patch("backend.database.researchers.fetch_all", return_value=[]), \
         patch("backend.database.researchers.execute_query") as execute:
        assert get_researcher_id("Jane", "Doe") is None
    assert not any("INSERT INTO researchers" in c.args[0] for c in execute.call_args_list)


def test_new_researcher_still_created_when_not_opted_out():
    from backend.database.researchers import get_researcher_id
    with patch("backend.database.researchers.fetch_one", return_value=None), \
         patch("backend.database.researchers.fetch_all", return_value=[]), \
         patch("backend.database.researchers.execute_query", return_value=41) as execute:
        assert get_researcher_id("Jane", "Doe") == 41
    assert "INSERT INTO researchers" in execute.call_args.args[0]


def test_add_researcher_url_refuses_opted_out_url():
    from backend.database.researchers import add_researcher_url
    with patch("backend.database.researchers.fetch_one", side_effect=_optout_hit_for), \
         patch("backend.database.researchers.execute_query") as execute:
        add_researcher_url(3, "HOME", "https://www.janedoe.example/")
    execute.assert_not_called()


def test_add_researcher_url_inserts_other_urls():
    from backend.database.researchers import add_researcher_url
    with patch("backend.database.researchers.fetch_one", return_value=None), \
         patch("backend.database.researchers.execute_query") as execute:
        add_researcher_url(3, "HOME", "https://bob.example/")
    assert "INSERT IGNORE INTO researcher_urls" in execute.call_args.args[0]


def test_searlo_approval_goes_through_the_guard():
    from backend.database import discoveries
    row = {"researcher_id": 3, "url": "https://janedoe.example/", "subpages": None}
    with patch("backend.database.discoveries.fetch_one", return_value=row), \
         patch("backend.database.discoveries.execute_query"), \
         patch("backend.database.researchers.fetch_one", side_effect=_optout_hit_for), \
         patch("backend.database.researchers.execute_query") as insert:
        discoveries.approve_discovery(9)
    insert.assert_not_called()


# ---------------------------------------------------------------------------
# opt_out_researcher
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []
        self.lastrowid = 0

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.db.calls.append((q, params))
        if q.startswith("SELECT id, first_name, last_name FROM researchers"):
            self.rows = [self.db.person] if self.db.person else []
        elif q.startswith("SELECT id, url, page_type FROM researcher_urls"):
            self.rows = self.db.urls
        elif q.startswith("SELECT a.publication_id"):
            self.rows = self.db.papers
        elif "information_schema.tables" in q:
            self.rows = [{"n": 1}]
        elif q.startswith("INSERT INTO researcher_optouts"):
            self.lastrowid = 77
        self.rows = list(self.rows)

    def executemany(self, sql, seq):
        self.db.calls.append((" ".join(sql.split()), list(seq)))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, person=True):
        self.calls = []
        self.person = {"id": 5, "first_name": "Jane", "last_name": "Doe"} if person else None
        self.urls = [
            {"id": 1, "url": "https://janedoe.example/", "page_type": "HOME"},
            {"id": 2, "url": "https://janedoe.example/papers", "page_type": "PUBLICATIONS"},
        ]
        self.papers = [{"id": 10, "shared": 0}, {"id": 11, "shared": 1}, {"id": 12, "shared": 0}]
        self.commits = self.rollbacks = 0
        self.closed = False

    def cursor(self, **kwargs):
        return FakeCursor(self)

    def start_transaction(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def sql(self):
        return [q for q, _ in self.calls]


WRITES = ("INSERT", "UPDATE", "DELETE", "CREATE")


def test_dry_run_reports_and_changes_nothing():
    db = FakeConn()
    with patch.object(optout, "get_connection", return_value=db):
        result = optout.opt_out_researcher(5)
    assert result == {
        "researcher_id": 5, "name": "Jane Doe",
        "urls": ["https://janedoe.example/", "https://janedoe.example/papers"],
        "papers_deleted": 2, "papers_kept_for_coauthors": 1,
        "miner_hold": True, "applied": False,
    }
    assert not [q for q in db.sql() if q.startswith(WRITES)]
    assert db.commits == 0 and db.closed


def test_apply_tombstones_deletes_and_holds_in_one_commit():
    db = FakeConn()
    with patch.object(optout, "get_connection", return_value=db):
        result = optout.opt_out_researcher(5, apply=True)
    assert result["applied"] and db.commits == 1
    sql = db.sql()

    tomb = next(p for q, p in db.calls if q.startswith("INSERT INTO researcher_optouts"))
    assert tomb == (name_hash("Jane", "Doe"), 5)
    url_rows = next(p for q, p in db.calls if q.startswith("INSERT IGNORE INTO researcher_optout_urls"))
    assert url_rows == [(url_hash(u), url_key_hash(u), 77)
                        for u in ("https://janedoe.example/", "https://janedoe.example/papers")]
    assert ("DELETE FROM papers WHERE id IN (%s,%s)", [10, 12]) in db.calls
    assert any(q.startswith("UPDATE papers SET source_url = NULL") for q in sql)
    assert any(q.startswith("UPDATE feed_events SET event_source_url = NULL") for q in sql)

    hold_sql, hold = next((q, p) for q, p in db.calls if q.startswith("INSERT INTO site_mining_candidates"))
    assert "'rejected'" in hold_sql
    assert json.loads(hold[4]) == {"identity_hold": True, "opt_out": True}
    assert "Jane" not in hold[1]
    assert [u["url"] for u in json.loads(hold[6])] == ["https://janedoe.example/", "https://janedoe.example/papers"]
    assert sql[-1] == "DELETE FROM researchers WHERE id = %s"
    assert any(q.startswith("CREATE TABLE IF NOT EXISTS site_mining_candidates") for q in sql)


def test_unknown_researcher_raises_and_rolls_back():
    db = FakeConn(person=False)
    with patch.object(optout, "get_connection", return_value=db), pytest.raises(LookupError):
        optout.opt_out_researcher(5, apply=True)
    assert db.commits == 0 and db.rollbacks >= 1 and db.closed


def test_researcher_without_urls_gets_no_miner_hold_but_is_tombstoned():
    db = FakeConn()
    db.urls = []
    with patch.object(optout, "get_connection", return_value=db):
        result = optout.opt_out_researcher(5, apply=True)
    assert result["miner_hold"] is False
    assert not any(q.startswith("INSERT INTO site_mining_candidates") for q in db.sql())
    assert any(q.startswith("INSERT INTO researcher_optouts") for q in db.sql())


def test_cli_opt_out_defaults_to_dry_run(capsys):
    from backend import main
    with patch("sys.argv", ["main", "opt-out", "--researcher-id", "5"]), \
         patch.object(main, "create_tables"), \
         patch("backend.optout.opt_out_researcher", return_value={"applied": False}) as run:
        main.main()
    run.assert_called_once_with(5, apply=False)
    assert "Dry run" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Crawler identity
# ---------------------------------------------------------------------------

def test_user_agent_names_bot_and_links_contact_page():
    from backend.crawler_identity import BOT_NAME, with_bot_token
    from backend.pipeline.html_fetcher import SCRAPER_USER_AGENT
    assert BOT_NAME in SCRAPER_USER_AGENT
    assert "/contact)" in SCRAPER_USER_AGENT
    assert with_bot_token(SCRAPER_USER_AGENT) == SCRAPER_USER_AGENT


def test_contact_url_uses_public_frontend(monkeypatch):
    from backend.crawler_identity import contact_url
    monkeypatch.setenv("FRONTEND_URL", "https://www.econnewsfeed.org/")
    assert contact_url() == "https://www.econnewsfeed.org/contact"
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    assert contact_url().startswith("https://") and contact_url().endswith("/contact")


def test_robots_rules_for_our_bot_are_honored():
    from backend.pipeline.html_fetcher import HTMLFetcher
    rp = RobotFileParser()
    rp.parse(["User-agent: EconNewsfeedBot", "Disallow: /"])
    with patch.object(HTMLFetcher, "_get_robots_parser", return_value=rp):
        assert HTMLFetcher.is_allowed_by_robots("https://janedoe.example/papers") is False
    rp = RobotFileParser()
    rp.parse(["User-agent: SomeOtherBot", "Disallow: /"])
    with patch.object(HTMLFetcher, "_get_robots_parser", return_value=rp):
        assert HTMLFetcher.is_allowed_by_robots("https://janedoe.example/papers") is True


def test_digest_footer_links_contact_and_privacy():
    import backend.digest as digest
    since, until = datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 8, tzinfo=timezone.utc)
    html = digest._render_digest_html([], None, "https://x.test/u", since, until)
    text = digest._render_digest_text([], None, "https://x.test/u", since, until)
    for page in ("/contact", "/privacy"):
        assert f'href="http://localhost:3000{page}"' in html
        assert f"http://localhost:3000{page}" in text
