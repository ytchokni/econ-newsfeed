"""Exercise storage boundaries without a live database or network access."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import unicodedata

import pytest

from backend.discovery import mining_store as module
from backend.discovery.mining_store import MiningStore, candidate_key


def mysql_prefix(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value[:500]).casefold()
                   if not unicodedata.combining(c))


class FakeConnection:
    def __init__(self, *, tables=True, lock=1):
        self.tables = set(module._TABLES) if tables else set()
        self.lock = lock
        self.calls = []
        self.people = {7: {"id": 7, "first_name": "Alice", "last_name": "Economist", "affiliation": None}}
        self.urls = []
        self.candidates = {}
        self.discoveries = []
        self.cached = []
        self.snapshot = None
        self.commits = 0
        self.rollbacks = 0
        self.released = False
        self.closed = False
        self.fail_insert = None
        self.url_inserts = 0
        self.raw_autocommit = False
        self.implicit_transaction = False

    def __getattr__(self, name):
        # Match PooledMySQLConnection: reads delegate, assignments shadow the
        # wrapper rather than changing the underlying connection/session.
        if name == "autocommit":
            return self.raw_autocommit
        raise AttributeError(name)

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def start_transaction(self):
        if self.implicit_transaction:
            raise RuntimeError("Transaction already in progress")
        assert self.snapshot is None
        self.snapshot = deepcopy((self.urls, self.candidates))

    def commit(self):
        self.commits += 1
        self.snapshot = None
        self.implicit_transaction = False

    def rollback(self):
        self.rollbacks += 1
        if self.snapshot is not None:
            self.urls, self.candidates = self.snapshot
            self.snapshot = None
        self.implicit_transaction = False

    def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, connection):
        self.db = connection
        self.rows = []
        self.lastrowid = 0
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def fetchall(self):
        return deepcopy(self.rows)

    def fetchone(self):
        return deepcopy(self.rows[0]) if self.rows else None

    def execute(self, sql, params=None):
        q = " ".join(sql.lower().split())
        p = params or ()
        self.db.calls.append((q, p))
        if not self.db.raw_autocommit and (q.startswith(("insert ", "update ")) or
                                          (q.startswith("select ") and " from " in q)):
            self.db.implicit_transaction = True
            if self.db.snapshot is None:
                self.db.snapshot = deepcopy((self.db.urls, self.db.candidates))
        if q.startswith("set session autocommit"):
            self.db.raw_autocommit = bool(p[0]) if p else q.endswith("= 1")
            if self.db.raw_autocommit:
                self.db.snapshot = None
                self.db.implicit_transaction = False
        elif "get_lock(" in q:
            self.rows = [{"acquired": self.db.lock}]
        elif "release_lock(" in q:
            self.db.released = True
            self.rows = [{"released": 1}]
        elif q.startswith("create table"):
            self.db.tables.add(q.split()[5])
        elif "information_schema.tables" in q:
            # MySQL exposes information_schema's native uppercase column name
            # unless the SELECT explicitly assigns a lower-case alias.
            field = "table_name" if "table_name as table_name" in q else "TABLE_NAME"
            self.rows = [{field: t} for t in self.db.tables]
        elif q.startswith("select") and "from researchers" in q:
            self.rows = [self.db.people[p[0]]] if p and p[0] in self.db.people else list(self.db.people.values()) if not p else []
        elif "from researcher_urls ru join html_content" in q:
            if "where ru.id = %s" in q:
                rows = [r for r in self.db.cached if r["url_id"] == p[0]]
            else:
                skipped = set(p[:-1])
                rows = [r for r in self.db.cached if r["url_id"] not in skipped][:p[-1]]
            self.rows = [dict(r, content_hash=hashlib.sha256(r["raw_html"].encode()).hexdigest()) for r in rows]
            if "ru.url,h.raw_html" not in q:
                self.rows = [{k: v for k, v in r.items() if k != "raw_html"} for r in self.rows]
        elif "from researcher_urls" in q:
            self.rows = self.db.urls
            if "left(url,500)" in q:
                self.rows = [r for r in self.rows if mysql_prefix(r["url"]) == mysql_prefix(p[0])]
        elif "as equal_prefix" in q:
            self.rows = [{"equal_prefix": mysql_prefix(p[0]) == mysql_prefix(p[1])}]
        elif "from url_discoveries" in q:
            self.rows = self.db.discoveries
        elif "from site_mining_candidates" in q:
            rows = list(self.db.candidates.values())
            if "candidate_key = %s" in q:
                rows = [r for r in rows if r["candidate_key"] == p[0]]
            elif "where id=%s" in q:
                rows = [r for r in rows if r["id"] == p[0]]
            elif "status = 'rejected'" in q:
                rows = [dict(r, subpages=r.get("urls")) for r in rows if r["status"] == "rejected"]
            elif "$.identity_hold" in q:
                rows = [r for r in rows if r["status"] in {"review", "rejected"}
                        and json.loads(r["evidence"]).get("identity_hold")]
            elif "status in ('pending','retry')" in q:
                rows = [r for r in rows if r["status"] in {"pending", "retry"}
                        and r["next_attempt_at"] <= module._utcnow()
                        and r["id"] not in p[:-1]][:p[-1]]
            self.rows = rows
        elif q.startswith("insert into site_mining_candidates"):
            self.lastrowid = max(self.db.candidates, default=0) + 1
            self.db.candidates[self.lastrowid] = dict(
                id=self.lastrowid, candidate_key=p[0], researcher_id=p[1], name=p[2],
                url=p[3], source_url=p[4], source_kind=p[5], evidence=p[6],
                status="pending", attempts=0, updated_at=module._utcnow(),
                next_attempt_at=module._utcnow(), urls=None,
            )
            self.rowcount = 1
        elif q.startswith("update site_mining_candidates"):
            if "set status='pending'" in q:
                row = self.db.candidates[p[2]]
                if row["status"] in {"approved", "review"}:
                    row.update(status="pending", evidence=p[0], source_url=p[1], attempts=0,
                               updated_at=module._utcnow(), next_attempt_at=module._utcnow(), urls=None)
                    self.rowcount = 1
            else:
                row = self.db.candidates[p[-1]]
                if row["status"] in {"pending", "retry"}:
                    row.update(status=p[0], reason=p[1], urls=p[2], evidence=p[3],
                               next_attempt_at=p[5], attempts=row["attempts"] + p[4])
                    self.rowcount = 1
        elif q.startswith("insert into researcher_urls"):
            self.db.url_inserts += 1
            if self.db.fail_insert == self.db.url_inserts:
                raise RuntimeError("simulated insert failure")
            self.lastrowid = max((r["id"] for r in self.db.urls), default=0) + 1
            self.db.urls.append(dict(id=self.lastrowid, researcher_id=p[0], url=p[1], page_type=p[2], is_active=1))
            self.rowcount = 1
        elif "from site_mining_sources" in q:
            self.rows = []
        else:
            raise AssertionError(f"Unhandled SQL: {q}")


@pytest.fixture
def db(monkeypatch):
    db = FakeConnection()
    monkeypatch.setattr(module, "get_connection", lambda: db)
    return db


def candidate(kind="cached_link"):
    return dict(researcher_id=7, name="Alice Economist", url="https://alice-economist.example/",
                source_url="https://coauthor.example/", source_kind=kind,
                evidence={"paper_match": "A verified research paper"})


def bundle():
    return [{"url": "https://alice-economist.example/", "page_type": "HOME"},
            {"url": "https://alice-economist.example/research", "page_type": "RESEARCH"}]


def queued(store, kind="cached_link"):
    assert store.enqueue(candidate(kind))
    return store.due_candidates(1)[0]


def approve(store, row, pages=None):
    return store.approve_bundle(row, pages or bundle(), ("Alice", "Economist"))


def url_row(url, rid=7, active=1, identifier=100):
    return dict(id=identifier, researcher_id=rid, url=url, page_type="HOME", is_active=active)


def test_dry_run_with_absent_tables_has_no_ddl_or_dml(db):
    db.tables.clear()
    with MiningStore(apply=False) as store:
        row = queued(store)
        result = approve(store, row)
        store.checkpoint("cached", row["source_url"], "abc")
        assert not store.source_due("cached", row["source_url"])
        assert store.start_run() is None
        store.finish_run(None, {})
        assert len(result["inserted"]) == 2
        assert store.due_candidates(5) == []
        assert store.status() == {"counts": {}, "recent_runs": [], "review": []}
    assert not db.urls and not db.candidates
    assert all(q.startswith(("select ", "set session autocommit")) for q, _ in db.calls)
    assert db.released and db.closed


def test_dry_run_handles_mysql_information_schema_column_casing(db):
    with MiningStore(apply=False) as store:
        assert store._tables == set(module._TABLES)
    assert any("select table_name as table_name from information_schema.tables" in q
               for q, _ in db.calls)


def test_pool_wrapper_session_autocommit_persists_queue_before_bundle(db):
    with MiningStore(apply=True) as store:
        assert db.raw_autocommit
        assert "autocommit" not in db.__dict__
        row = queued(store)
        # Standalone queue writes survive a rollback, as they must before a
        # bundle's separate explicit transaction or a process interruption.
        db.rollback()
        assert row["id"] in db.candidates
        assert not db.implicit_transaction
        assert len(approve(store, row)["inserted"]) == 2
    assert not db.raw_autocommit
    assert db.candidates[row["id"]]["status"] == "approved"
    assert len(db.urls) == 2


def test_lock_unavailable_causes_no_schema_changes(db):
    db.lock = 0
    with MiningStore(apply=True) as store:
        assert not store.acquired
        assert store.status() == {"skipped": "another_mining_job_is_running"}
        with pytest.raises(RuntimeError, match="no run lock"):
            store.enqueue(candidate())
    assert not db.released and db.closed
    assert not any(q.startswith("create") for q, _ in db.calls)


def test_apply_creates_exactly_three_tables_and_releases_lock_on_error(db):
    with pytest.raises(ValueError, match="caller failed"):
        with MiningStore(apply=True):
            raise ValueError("caller failed")
    assert len([q for q, _ in db.calls if q.startswith("create")]) == 3
    assert db.released and db.closed


def test_duplicate_enqueue_never_resets_rejected_candidate(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        store.save_result(row, "rejected", "Wrong person")
        assert not store.enqueue(candidate())
        assert db.candidates[row["id"]]["status"] == "rejected"


def test_approval_is_atomic_idempotent_and_saves_verification_evidence(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        row["evidence"]["checks"] = [{"identity": True, "paper_match": "matched"}]
        first = approve(store, row)
        second = approve(store, row)
        assert len(first["inserted"]) == 2
        assert len(second["existing"]) == 2 and not second["inserted"]
        assert db.candidates[row["id"]]["status"] == "approved"
        assert json.loads(db.candidates[row["id"]]["evidence"])["checks"]
        assert db.commits == 2
    assert len(db.urls) == 2


@pytest.mark.parametrize("existing,reason", [
    (url_row("https://alice-economist.example/research", rid=8), "another researcher"),
    (url_row("https://alice-economist.example/research", active=0), "inactive"),
    (url_row("http://www.alice-economist.example/research"), "alias"),
    (url_row("https://alice-economist.example/older-work", rid=8), "Site scope"),
    (url_row("https://alice-economist.example/Research"), "collation"),
])
def test_ownership_and_inactive_conflicts_hold_entire_bundle(db, existing, reason):
    db.urls.append(existing)
    with MiningStore(apply=True) as store:
        result = approve(store, queued(store))
        assert result["held"] and reason in result["reason"]
        assert db.url_inserts == 0
        assert db.candidates[1]["status"] == "review"
    assert db.urls == [existing]


def test_rejected_legacy_discovery_subpage_holds_bundle(db):
    db.discoveries = [{"url": "https://other.example/", "subpages": json.dumps([bundle()[1]])}]
    with MiningStore(apply=True) as store:
        result = approve(store, queued(store))
        assert result["held"] and "previously rejected" in result["reason"]
    assert db.url_inserts == 0


def test_unknown_researcher_and_name_drift_cannot_insert(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        db.people[7]["last_name"] = "Different"
        result = approve(store, row)
        assert result["held"] and "identity changed" in result["reason"]
    assert db.url_inserts == 0


def test_bundle_requires_home_and_same_owner_scope(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        result = approve(store, row, [bundle()[1]])
        assert result["held"] and "HOME" in result["reason"]
    assert db.url_inserts == 0


def test_unrelated_research_site_cannot_join_home_bundle(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        result = approve(store, row, [bundle()[0], {"url": "https://someone-else.example/research", "page_type": "RESEARCH"}])
        assert result["held"] and "outside" in result["reason"]
    assert db.url_inserts == 0


def test_case_collision_within_bundle_is_checked_before_first_url_write(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        result = approve(store, row, bundle() + [{"url": "https://alice-economist.example/Research", "page_type": "PUBLICATIONS"}])
        assert result["held"] and "within bundle" in result["reason"]
    assert db.url_inserts == 0


def test_mid_bundle_failure_rolls_back_urls_and_candidate_approval(db):
    db.fail_insert = 2
    with pytest.raises(RuntimeError, match="simulated insert failure"):
        with MiningStore(apply=True) as store:
            approve(store, queued(store))
    assert db.urls == []
    assert db.candidates[1]["status"] == "pending"
    assert db.commits == 0 and db.released and db.closed


def test_different_people_on_same_campus_host_do_not_collide(db):
    db.urls = [url_row("https://economics.example.edu/people/bob", rid=8)]
    pages = [{"url": "https://economics.example.edu/people/alice", "page_type": "HOME"},
             {"url": "https://economics.example.edu/people/alice/research", "page_type": "RESEARCH"}]
    with MiningStore(apply=True) as store:
        result = approve(store, queued(store), pages)
        assert not result["held"] and len(result["inserted"]) == 2


def test_raw_html_hash_is_used_and_dry_batches_do_not_starve(db):
    db.cached = [dict(url_id=i, researcher_id=7, url=f"https://alice-economist.example/{i}",
                      raw_html=f'<a href="{i}">Unchanged visible text</a>') for i in range(1, 4)]
    with MiningStore(apply=False) as store:
        first = list(store.cached_sources(2))
        second = list(store.cached_sources(2))
    assert [r["url_id"] for r in first + second] == [1, 2, 3]
    query = next(q for q, _ in db.calls if "join html_content" in q)
    assert "sha2(h.raw_html,256)" in query
    assert "h.content_hash" not in query and "s.next_check_at" in query
    assert first[0]["content_hash"] != first[1]["content_hash"]
    assert "ru.url,h.raw_html" not in query
    assert len([q for q, _ in db.calls if "where ru.id = %s" in q]) == 3


def test_dry_persisted_queue_progresses_past_overlay_decisions(db):
    with MiningStore(apply=True) as store:
        for i in range(3):
            assert store.enqueue(dict(candidate(), url=f"https://alice-economist.example/{i}"))
    with MiningStore(apply=False) as store:
        ids = []
        for _ in range(3):
            row = store.due_candidates(1)[0]
            ids.append(row["id"])
            store.save_result(row, "review", "needs checking")
    assert ids == [1, 2, 3]
    assert all(r["status"] == "pending" for r in db.candidates.values())


def test_directory_profile_weekly_refresh_preserves_rejections(db):
    with MiningStore(apply=True) as store:
        row = queued(store, "directory_profile")
        store.save_result(row, "review", "profile inspected")
        assert not store.enqueue(candidate("directory_profile"))
        db.candidates[row["id"]]["updated_at"] -= timedelta(days=8)
        assert store.enqueue(candidate("directory_profile"))
        assert db.candidates[row["id"]]["status"] == "pending"
        store.save_result(row, "rejected", "wrong person")
        db.candidates[row["id"]]["updated_at"] -= timedelta(days=8)
        assert not store.enqueue(candidate("directory_profile"))
    assert candidate_key(candidate()) != candidate_key(candidate("directory_profile"))


def test_approved_candidate_cannot_be_repurposed_for_more_urls(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        approve(store, row)
        result = approve(store, row, bundle() + [{"url": "https://alice-economist.example/publications", "page_type": "PUBLICATIONS"}])
        assert result["held"] and "different bundle" in result["reason"]
        assert len(db.urls) == 2


def test_identity_hold_blocks_other_sites_for_conflated_researcher(db):
    with MiningStore(apply=True) as store:
        hold = dict(candidate(), url="https://a-homonym.example/", evidence={"identity_hold": True})
        assert store.enqueue(hold)
        hold = store.due_candidates(1)[0]
        store.save_result(hold, "review", "Researcher record combines multiple people")
        result = approve(store, queued(store))
        assert result["held"] and "identity audit hold" in result["reason"]
        assert db.url_inserts == 0


def test_weekly_profile_refresh_does_not_reset_identity_hold(db):
    with MiningStore(apply=True) as store:
        held = dict(candidate("directory_profile"), evidence={"identity_hold": True})
        assert store.enqueue(held)
        row = store.due_candidates(1)[0]
        store.save_result(row, "review", "Unresolved identity")
        db.candidates[row["id"]]["updated_at"] -= timedelta(days=8)
        assert not store.enqueue(candidate("directory_profile"))
        assert db.candidates[row["id"]]["status"] == "review"


def test_budget_deferral_does_not_spend_retry_attempt(db):
    with MiningStore(apply=True) as store:
        row = queued(store)
        store.save_result(row, "retry", "Network batch budget exhausted", retry_hours=1, count_attempt=False)
        assert db.candidates[row["id"]]["attempts"] == 0
        assert db.candidates[row["id"]]["status"] == "retry"
        assert store.due_candidates(1) == []


def test_directory_generation_refreshes_late_verified_profile_once_per_visit(db):
    old = dict(candidate("directory_profile"), evidence={"directory_generation": "2026-10-01T08:00:00Z"})
    new = dict(candidate("directory_profile"), evidence={"directory_generation": "2026-10-08T08:00:00Z"})
    with MiningStore(apply=True) as store:
        assert store.enqueue(old)
        row = store.due_candidates(1)[0]
        # Last week's queued profile was verified just before the new seed visit.
        store.save_result(row, "review", "Profile checked late")
        db.candidates[row["id"]]["updated_at"] = module._utcnow()
        assert not store.enqueue(old)
        assert store.enqueue(new)
        refreshed = store.due_candidates(1)[0]
        assert refreshed["evidence"]["directory_generation"] == "2026-10-08T08:00:00Z"
        store.save_result(refreshed, "review", "This visit finished")
        # Age alone cannot turn the same seed generation into a new visit.
        db.candidates[row["id"]]["updated_at"] -= timedelta(days=9)
        assert not store.enqueue(new)
        assert not store.enqueue(candidate("directory_profile"))
