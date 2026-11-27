"""Applying reviewed paid-search discoveries goes through the miner's transactional store."""
import json

import pytest

from backend.discovery import searlo_review
from backend.discovery.mining_store import MiningStore
from tests.test_mining_store import FakeConnection, module, url_row  # noqa: F401


@pytest.fixture
def db(monkeypatch):
    db = FakeConnection()
    db.discoveries = [
        {"id": 11, "researcher_id": 7, "url": "https://alice-economist.example/", "status": "pending", "subpages": None},
        {"id": 12, "researcher_id": 7, "url": "https://elsewhere.example/", "status": "pending", "subpages": None},
    ]
    monkeypatch.setattr(module, "get_connection", lambda: db)
    return db


def approve_decision(**over):
    d = {"discovery_id": 11, "researcher_id": 7, "first_name": "Alice", "last_name": "Economist",
         "decision": "approve", "evidence": "name in h1",
         "bundle": [{"url": "https://alice-economist.example/", "page_type": "HOME"},
                    {"url": "https://alice-economist.example/research", "page_type": "RESEARCH"}]}
    d.update(over)
    return d


def test_dry_run_reports_but_writes_nothing(db):
    stats = searlo_review.apply_decisions([approve_decision()], apply=False)
    assert stats["approved"] == 1 and stats["urls_inserted"] == 2
    assert not db.urls and not db.candidates
    assert db.discoveries[0]["status"] == "pending"
    assert all(q.startswith(("select ", "set session autocommit")) for q, _ in db.calls)


def test_apply_requires_manifest(db):
    with pytest.raises(ValueError, match="manifest"):
        searlo_review.apply_decisions([approve_decision()], apply=True)


def test_apply_inserts_bundle_and_marks_discovery_together(db, tmp_path):
    manifest = tmp_path / "m.jsonl"
    stats = searlo_review.apply_decisions([approve_decision()], apply=True, manifest_path=manifest)
    assert stats["approved"] == 1 and stats["urls_inserted"] == 2
    assert [u["page_type"] for u in db.urls] == ["HOME", "RESEARCH"]
    assert db.discoveries[0]["status"] == "approved"
    assert list(db.candidates.values())[0]["status"] == "approved"
    entry = json.loads(manifest.read_text().splitlines()[0])
    assert entry["op"] == "approve" and len(entry["inserted"]) == 2


def test_held_bundle_leaves_discovery_pending(db, tmp_path):
    db.urls.append(url_row("https://alice-economist.example/research", rid=8))
    stats = searlo_review.apply_decisions([approve_decision()], apply=True, manifest_path=tmp_path / "m.jsonl")
    assert stats["held"] == 1 and stats["approved"] == 0
    assert db.discoveries[0]["status"] == "pending"
    assert len(db.urls) == 1


def test_identity_hold_blocks_researcher(db, tmp_path):
    db.candidates[50] = dict(id=50, candidate_key="x", researcher_id=7, name="Alice Economist",
                             url="https://other.example/", source_url="s", source_kind="directory",
                             evidence=json.dumps({"identity_hold": True}), status="review",
                             attempts=0, updated_at=module._utcnow(), next_attempt_at=module._utcnow(), urls=None)
    stats = searlo_review.apply_decisions([approve_decision()], apply=True, manifest_path=tmp_path / "m.jsonl")
    assert stats["held"] == 1 and "identity audit hold" in stats["held_details"][0]["reason"]
    assert not db.urls


def test_name_drift_is_held(db, tmp_path):
    stats = searlo_review.apply_decisions([approve_decision(last_name="Other")], apply=True,
                                          manifest_path=tmp_path / "m.jsonl")
    assert stats["held"] == 1 and not db.urls


def test_reject_and_unsure(db, tmp_path):
    decisions = [approve_decision(decision="reject", discovery_id=11),
                 approve_decision(decision="unsure", discovery_id=12)]
    stats = searlo_review.apply_decisions(decisions, apply=True, manifest_path=tmp_path / "m.jsonl")
    assert stats["rejected"] == 1 and stats["skipped"] == 1
    assert [d["status"] for d in db.discoveries] == ["rejected", "pending"]


def test_stale_discovery_is_not_applied(db, tmp_path):
    db.discoveries[0]["status"] = "approved"
    stats = searlo_review.apply_decisions([approve_decision()], apply=True, manifest_path=tmp_path / "m.jsonl")
    assert stats["stale"] == 1 and not db.urls


def test_undo_removes_inserted_urls_and_reopens(db, tmp_path):
    manifest = tmp_path / "m.jsonl"
    searlo_review.apply_decisions([approve_decision()], apply=True, manifest_path=manifest)
    assert len(db.urls) == 2
    assert searlo_review.undo(manifest, apply=False)["urls_deleted"] == 2
    assert len(db.urls) == 2
    stats = searlo_review.undo(manifest, apply=True)
    assert stats["urls_deleted"] == 2 and stats["discoveries_reopened"] == 1
    assert not db.urls and db.discoveries[0]["status"] == "pending"


def test_load_decisions_validates(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps([approve_decision(bundle=[{"url": "https://x.example/", "page_type": "personal"}])]))
    with pytest.raises(ValueError, match="bundle"):
        searlo_review.load_decisions(path)
    path.write_text(json.dumps([approve_decision(), approve_decision()]))
    with pytest.raises(ValueError, match="duplicate"):
        searlo_review.load_decisions(path)
    path.write_text(json.dumps([approve_decision()]))
    assert searlo_review.load_decisions(path)[0]["discovery_id"] == 11
