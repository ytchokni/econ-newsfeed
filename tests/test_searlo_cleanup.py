"""Cleanup of the candidates the first Searlo apply (#299) overwrote or keyed like the miner's."""
import gzip
import json
from datetime import timedelta

import pytest

from backend.discovery import searlo_cleanup
from backend.discovery.mining_store import candidate_key
from tests.test_mining_store import FakeConnection, module, url_row  # noqa: F401

HOME = "https://alice-economist.example/"


def old_key(url=HOME, rid=7):
    """The key the first apply used: the miner's own formula."""
    return candidate_key({"researcher_id": rid, "url": url, "name": "Alice Economist", "source_kind": "cached_link"})


def cand(db, cid, status, kind="cached_link", key=None, url=HOME, evidence=None, urls=None, reason=None,
         attempts=1, rid=7, source_url="https://alice-economist.example/links"):
    db.candidates[cid] = dict(
        id=cid, candidate_key=key or old_key(url, rid), researcher_id=rid, name="Alice Economist", url=url,
        source_url=source_url, source_kind=kind, status=status, reason=reason,
        evidence=json.dumps(evidence if evidence is not None else {"miner": "x"}),
        urls=json.dumps(urls) if urls is not None else None, attempts=attempts,
        created_at=module._utcnow() - timedelta(days=30), updated_at=module._utcnow() - timedelta(hours=1),
        next_attempt_at=module._utcnow() - timedelta(days=1))
    return db.candidates[cid]


def overwritten(db, cid, status="approved", **kw):
    return cand(db, cid, status, evidence={"page_type": "HOME", "searlo_discovery_id": 11, "review": "ours"},
                urls=[{"url": HOME, "page_type": "HOME"}], reason="Verified identity and crawl bundle",
                attempts=2, **kw)


def dump_row(cid, status="pending", key=None, url=HOME):
    original = {"source_researcher_id": 3, "source_url_id": 9, "anchor": "Alice's \"co-author\"", "context": "it's here"}
    ev = json.dumps(original).replace("\\", "\\\\").replace("'", "\\'")
    return (f"({cid},'{key or old_key(url)}',7,'Alice Economist','{url}','https://alice-economist.example/links',"
            f"'cached_link','{ev}','{status}',NULL,NULL,1,'2026-11-01 00:00:00','2026-10-12 00:00:00','2026-11-26 00:00:00')")


def write_dump(path, rows, gz=False):
    text = (
        "-- MySQL dump\nCREATE TABLE `researchers` (\n  `id` int NOT NULL,\n  PRIMARY KEY (`id`)\n);\n"
        "LOCK TABLES `researchers` WRITE;\nINSERT INTO `researchers` VALUES (7,'x');\nUNLOCK TABLES;\n"
        "CREATE TABLE `site_mining_candidates` (\n  `id` bigint NOT NULL AUTO_INCREMENT,\n"
        "  `candidate_key` char(64) NOT NULL,\n  `researcher_id` int DEFAULT NULL,\n  `name` varchar(511) NOT NULL,\n"
        "  `url` varchar(2048) NOT NULL,\n  `source_url` varchar(2048) NOT NULL,\n"
        "  `source_kind` varchar(32) NOT NULL,\n  `evidence` json NOT NULL,\n  `status` enum('pending') NOT NULL,\n"
        "  `reason` text,\n  `urls` json DEFAULT NULL,\n  `attempts` int NOT NULL DEFAULT '0',\n"
        "  `next_attempt_at` datetime NOT NULL,\n  `created_at` datetime NOT NULL,\n  `updated_at` datetime NOT NULL,\n"
        "  PRIMARY KEY (`id`),\n  UNIQUE KEY `uq_mining_candidate` (`candidate_key`)\n) ENGINE=InnoDB;\n"
        "LOCK TABLES `site_mining_candidates` WRITE;\n"
        f"INSERT INTO `site_mining_candidates` VALUES {','.join(rows)};\nUNLOCK TABLES;\n"
        "INSERT INTO `site_mining_candidates` VALUES (999,'after');\n")
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def db(monkeypatch):
    db = FakeConnection()
    db.urls.append(url_row("https://alice-economist.example/links", identifier=9, rid=3))
    monkeypatch.setattr(module, "get_connection", lambda: db)
    return db


def test_parse_insert_handles_escapes_nulls_numbers_and_quotes():
    line = r"INSERT INTO `t` VALUES (1,'a\'b',NULL,-2.5,'x\\y\nz','it''s'),(2,_binary 'q',NULL,3,'(,)','');"
    assert list(searlo_cleanup.parse_insert(line)) == [
        [1, "a'b", None, -2.5, "x\\y\nz", "it's"], [2, "q", None, 3, "(,)", ""]]


@pytest.mark.parametrize("gz", [False, True])
def test_read_candidates_from_dump_stops_at_the_table_end(tmp_path, gz):
    path = write_dump(tmp_path / ("d.sql.gz" if gz else "d.sql"), [dump_row(50), dump_row(51), dump_row(52)], gz=gz)
    rows = searlo_cleanup.read_candidates_from_dump(path, {50, 52, 999})
    assert sorted(rows) == [50, 52]
    original = json.loads(rows[50]["evidence"])
    assert original["anchor"] == 'Alice\'s "co-author"' and rows[50]["status"] == "pending"
    assert rows[50]["attempts"] == 1 and rows[50]["reason"] is None


def test_dry_run_writes_nothing_and_counts_each_step(db, tmp_path):
    overwritten(db, 50)
    overwritten(db, 51, status="review", url="https://b.example/", rid=8, key=old_key("https://b.example/", 8))
    cand(db, 60, "approved", kind="searlo_review", evidence={"searlo_discovery_id": 1})
    cand(db, 61, "review", kind="searlo_review", url="https://c.example/", key=old_key("https://c.example/"))
    dump = write_dump(tmp_path / "d.sql", [dump_row(50), dump_row(51, url="https://b.example/", key=old_key("https://b.example/", 8))])
    before = json.loads(json.dumps(db.candidates, default=str))
    report = searlo_cleanup.run(dump_path=dump)
    assert report["actions"] == {"restore:backup": 2, "rekey": 1, "delete": 1}
    assert json.loads(json.dumps(db.candidates, default=str)) == before
    assert all(q.startswith(("select ", "set session autocommit")) for q, _ in db.calls)


def test_apply_requires_backup_and_dump(db, tmp_path):
    with pytest.raises(ValueError, match="backup"):
        searlo_cleanup.run(dump_path=tmp_path / "d.sql", apply=True)
    with pytest.raises(ValueError, match="dump"):
        searlo_cleanup.run(steps=["restore"])


def test_restore_puts_back_the_miners_row_and_undo_reverts(db, tmp_path):
    row = overwritten(db, 50)
    ours = dict(row)
    dump = write_dump(tmp_path / "d.sql", [dump_row(50)])
    backup = tmp_path / "b.jsonl"
    report = searlo_cleanup.run(steps=["restore"], dump_path=dump, apply=True, backup_path=backup)
    assert report["applied"] == 1 and report["changed_since_plan"] == 0
    row = db.candidates[50]
    assert row["status"] == "pending" and row["reason"] is None and row["urls"] is None and row["attempts"] == 1
    assert json.loads(row["evidence"])["source_url_id"] == 9 and "searlo_discovery_id" not in row["evidence"]
    assert len(backup.read_text().splitlines()) == 1
    assert searlo_cleanup.undo(backup, apply=False)["restored"] == 1 and db.candidates[50]["status"] == "pending"
    assert searlo_cleanup.undo(backup, apply=True)["restored"] == 1
    back = db.candidates[50]
    assert (back["status"], back["reason"], back["attempts"]) == (ours["status"], ours["reason"], 2)
    assert json.loads(back["evidence"]) == json.loads(ours["evidence"])


def test_restore_resets_rows_the_backup_cannot_restore(db, tmp_path):
    overwritten(db, 70, url="https://z.example/", key=old_key("https://z.example/"))        # created after the backup
    overwritten(db, 71, url="https://y.example/", key=old_key("https://y.example/"))        # not pending in the backup
    dump = write_dump(tmp_path / "d.sql", [dump_row(71, status="review", url="https://y.example/", key=old_key("https://y.example/"))])
    report = searlo_cleanup.run(steps=["restore"], dump_path=dump, apply=True, backup_path=tmp_path / "b.jsonl")
    assert report["actions"] == {"restore:reset_no_backup": 1, "restore:reset_backup_not_pending": 1}
    for cid in (70, 71):
        row = db.candidates[cid]
        assert (row["status"], row["reason"], row["urls"], row["attempts"]) == ("pending", None, None, 0)
        evidence = json.loads(row["evidence"])
        assert evidence["restored_after_searlo_overwrite"] and evidence["source_url_id"] == 9
        assert evidence["source_researcher_id"] == 3 and "searlo_discovery_id" not in evidence
        assert row["next_attempt_at"] <= module._utcnow()


def test_restore_leaves_rows_without_our_marker_and_our_own_rows_alone(db, tmp_path):
    cand(db, 80, "approved", evidence={"miner": "reused an identical approved candidate"})
    cand(db, 81, "approved", kind="searlo_review", evidence={"searlo_discovery_id": 5})
    dump = write_dump(tmp_path / "d.sql", [dump_row(80)])
    assert searlo_cleanup.run(steps=["restore"], dump_path=dump)["actions"] == {}


def test_a_row_changed_since_the_plan_is_not_overwritten(db, tmp_path):
    overwritten(db, 50)
    dump = write_dump(tmp_path / "d.sql", [dump_row(50)])
    [action] = searlo_cleanup.plan_restore(module.MiningStore(apply=False).__enter__(), dump)
    db.candidates[50]["updated_at"] += timedelta(minutes=5)         # the miner touched it meanwhile
    with module.MiningStore(apply=True) as store:
        assert searlo_cleanup._apply_one(store, action) is False
    assert db.candidates[50]["status"] == "approved"


def test_rekey_moves_approved_rows_deletes_review_rows_and_undo_restores_both(db, tmp_path):
    cand(db, 60, "approved", kind="searlo_review", evidence={"searlo_discovery_id": 1})
    cand(db, 61, "review", kind="searlo_review", url="https://c.example/", key=old_key("https://c.example/"),
         reason="URL is inactive and requires review", evidence={"searlo_discovery_id": 2})
    cand(db, 62, "pending", kind="searlo_review", url="https://d.example/", key=old_key("https://d.example/"))
    ours = {i: dict(db.candidates[i]) for i in (60, 61)}
    backup = tmp_path / "b.jsonl"
    report = searlo_cleanup.run(steps=["rekey"], apply=True, backup_path=backup)
    assert report["actions"] == {"rekey": 1, "delete": 1, "skip:status pending is left alone": 1}
    assert db.candidates[60]["candidate_key"] == candidate_key(
        {"researcher_id": 7, "url": HOME, "name": "Alice Economist", "source_kind": "searlo_review"})
    assert db.candidates[60]["candidate_key"] != ours[60]["candidate_key"]
    assert 61 not in db.candidates and db.candidates[62]["candidate_key"] == old_key("https://d.example/")
    # The miner can now queue its own candidate for the same site.
    miner_key = old_key()
    assert all(c["candidate_key"] != miner_key for c in db.candidates.values())
    # Idempotent: nothing left to do.
    assert searlo_cleanup.run(steps=["rekey"])["actions"] == {"skip:status pending is left alone": 1}
    assert searlo_cleanup.undo(backup, apply=True)["reinserted"] == 1
    assert db.candidates[60]["candidate_key"] == ours[60]["candidate_key"]
    assert db.candidates[61]["candidate_key"] == ours[61]["candidate_key"] and db.candidates[61]["reason"] == ours[61]["reason"]


def test_rekey_skips_when_the_new_key_is_taken(db):
    cand(db, 60, "approved", kind="searlo_review", evidence={"searlo_discovery_id": 1})
    new = candidate_key({"researcher_id": 7, "url": HOME, "name": "Alice Economist", "source_kind": "searlo_review"})
    cand(db, 90, "approved", kind="searlo_review", key=new)
    assert searlo_cleanup.run(steps=["rekey"])["actions"] == {"skip:new key already taken": 1}
