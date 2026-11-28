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


def _patch_fetch(monkeypatch, db):
    from backend.database import connection

    def fetch_one(sql, params=None):
        d = next((x for x in db.discoveries if x["id"] == params[0]), None)
        if d is None:
            return None
        return dict(d, subpages=json.dumps([{"page_type": "research", "url": "https://alice-economist.example/research"},
                                            {"page_type": "cv", "url": "https://alice-economist.example/cv.pdf"}]),
                    first_name="Alice", last_name="Economist")
    monkeypatch.setattr(connection, "fetch_one", fetch_one)
    return fetch_one


def test_admin_approve_one_uses_preflight_and_ignores_unknown_page_types(db, monkeypatch):
    _patch_fetch(monkeypatch, db)
    assert searlo_review.approve_one(11) == {"approved": True, "reason": None}
    assert [u["page_type"] for u in db.urls] == ["HOME", "RESEARCH"]
    assert db.discoveries[0]["status"] == "approved"


def test_admin_approve_one_returns_hold_reason(db, monkeypatch):
    _patch_fetch(monkeypatch, db)
    db.urls.append(url_row("https://alice-economist.example/research", rid=8))
    result = searlo_review.approve_one(11)
    assert result["approved"] is False and result["reason"]
    assert db.discoveries[0]["status"] == "pending" and len(db.urls) == 1


def test_admin_approve_one_rejects_non_pending(db, monkeypatch):
    _patch_fetch(monkeypatch, db)
    db.discoveries[0]["status"] = "rejected"
    assert searlo_review.approve_one(11)["approved"] is False
    assert not db.urls


def test_admin_approve_one_keeps_working_papers_subpage(db, monkeypatch):
    from backend.database import connection
    monkeypatch.setattr(connection, "fetch_one", lambda sql, params=None: dict(
        db.discoveries[0], first_name="Alice", last_name="Economist",
        subpages=json.dumps([{"page_type": "working_papers", "url": "https://alice-economist.example/research"}])))
    assert searlo_review.approve_one(11)["approved"] is True
    assert [u["page_type"] for u in db.urls] == ["HOME", "WORKING_PAPERS"]


# --- existing miner candidates -------------------------------------------------

HOME = "https://alice-economist.example/"
RESEARCH = "https://alice-economist.example/research"


def miner_candidate(db, cid=50, status="pending", url=HOME, urls=None, kind="cached_link",
                    reason=None, evidence=None):
    """A candidate the site miner queued earlier for researcher 7's home page."""
    key = module.candidate_key({"researcher_id": 7, "url": url, "name": "Alice Economist",
                                "source_kind": kind})
    db.candidates[cid] = dict(
        id=cid, candidate_key=key, researcher_id=7, name="Alice Economist", url=url,
        source_url="https://source.example/", source_kind=kind, status=status, reason=reason,
        evidence=json.dumps(evidence or {"miner": "original evidence"}),
        urls=json.dumps(urls) if urls is not None else None, attempts=1,
        updated_at=module._utcnow(), next_attempt_at=module._utcnow())
    return db.candidates[cid]


def snapshot(row):
    return {k: row[k] for k in ("status", "reason", "evidence", "urls", "attempts", "url", "source_kind")}


def own_candidates(db):
    return [c for c in db.candidates.values() if c["source_kind"] == "searlo_review"]


def run_both(db, decisions, tmp_path):
    """Dry run first, then apply: the dry run must predict the apply's counts."""
    dry = searlo_review.apply_decisions(decisions, apply=False)
    applied = searlo_review.apply_decisions(decisions, apply=True, manifest_path=tmp_path / "m.jsonl")
    keys = ("approved", "held", "urls_inserted", "urls_existing", "reused_miner_bundle")
    assert {k: dry[k] for k in keys} == {k: applied[k] for k in keys}
    assert [h["reason"] for h in dry["held_details"]] == [h["reason"] for h in applied["held_details"]]
    return applied


@pytest.mark.parametrize("status", ["pending", "retry"])
def test_miner_pending_candidate_is_not_overwritten(db, tmp_path, status):
    miner = miner_candidate(db, status=status)
    before = snapshot(miner)
    stats = run_both(db, [approve_decision()], tmp_path)
    assert stats["approved"] == 1 and stats["urls_inserted"] == 2
    assert snapshot(db.candidates[50]) == before            # reason, urls, evidence, attempts kept
    [ours] = own_candidates(db)
    assert ours["id"] != 50 and ours["status"] == "approved"
    assert [u["url"] for u in db.urls] == [HOME, RESEARCH]


def test_own_candidate_key_differs_from_miners():
    base = {"researcher_id": 7, "url": HOME, "name": "Alice Economist"}
    assert module.candidate_key(dict(base, source_kind="searlo_review")) != \
        module.candidate_key(dict(base, source_kind="cached_link"))
    assert module.candidate_key(dict(base, source_kind="cached_link")) == \
        module.candidate_key(dict(base, source_kind="known_site"))


def test_miner_approved_bundle_is_reused_and_only_missing_pages_added(db, tmp_path):
    # The miner approved HOME + /research under its own spelling (no trailing slash).
    miner = miner_candidate(db, status="approved", url="https://alice-economist.example",
                            urls=[{"url": "https://alice-economist.example", "page_type": "HOME"},
                                  {"url": RESEARCH, "page_type": "RESEARCH"}], reason="Verified identity")
    db.urls += [url_row("https://alice-economist.example", identifier=100),
                url_row(RESEARCH, identifier=101)]
    before = snapshot(miner)
    decision = approve_decision(bundle=[
        {"url": HOME, "page_type": "HOME"},                     # alias of the miner's HOME spelling
        {"url": RESEARCH, "page_type": "RESEARCH"},
        {"url": "https://alice-economist.example/papers", "page_type": "PUBLICATIONS"}])
    stats = run_both(db, [decision], tmp_path)
    assert stats["approved"] == 1 and stats["held"] == 0 and stats["reused_miner_bundle"] == 1
    assert stats["urls_inserted"] == 1 and stats["urls_existing"] == 2
    assert [u["url"] for u in db.urls] == ["https://alice-economist.example", RESEARCH,
                                           "https://alice-economist.example/papers"]
    assert snapshot(db.candidates[50]) == before
    assert db.discoveries[0]["status"] == "approved"


def test_reuse_still_goes_through_preflight(db, tmp_path):
    miner_candidate(db, status="approved", url="https://alice-economist.example",
                    urls=[{"url": "https://alice-economist.example", "page_type": "HOME"}])
    db.urls.append(url_row("https://alice-economist.example", identifier=100))
    db.urls.append(url_row("https://alice-economist.example/papers", rid=8, identifier=101))
    decision = approve_decision(bundle=[{"url": HOME, "page_type": "HOME"},
                                        {"url": "https://alice-economist.example/papers", "page_type": "PUBLICATIONS"}])
    stats = run_both(db, [decision], tmp_path)
    assert stats["approved"] == 0 and stats["held"] == 1
    assert "another researcher" in stats["held_details"][0]["reason"]
    assert db.discoveries[0]["status"] == "pending"
    assert len(db.urls) == 2


@pytest.mark.parametrize("status", ["review", "rejected"])
@pytest.mark.parametrize("spelling", [HOME, "https://alice-economist.example"])
def test_miner_review_verdict_is_kept(db, tmp_path, status, spelling):
    miner = miner_candidate(db, status=status, url=spelling, reason="No corroborating publication titles")
    before = snapshot(miner)
    stats = run_both(db, [approve_decision()], tmp_path)
    assert stats["approved"] == 0 and stats["held"] == 1
    assert stats["held_details"][0]["reason"] == f"Candidate already {status}"
    assert snapshot(db.candidates[50]) == before
    assert not db.urls and not own_candidates(db)
    assert db.discoveries[0]["status"] == "pending"


def test_other_researchers_candidates_are_ignored(db, tmp_path):
    miner = miner_candidate(db, status="review")
    miner["researcher_id"] = 8
    stats = run_both(db, [approve_decision()], tmp_path)
    assert stats["approved"] == 1


def test_own_earlier_hold_is_reported_by_dry_run_and_apply(db, tmp_path):
    db.urls.append(url_row(RESEARCH, rid=8))
    first = searlo_review.apply_decisions([approve_decision()], apply=True, manifest_path=tmp_path / "a.jsonl")
    assert first["held"] == 1 and own_candidates(db)[0]["status"] == "review"
    db.urls.clear()                                         # the blocker is gone, the hold is ours
    stats = run_both(db, [approve_decision()], tmp_path)
    assert stats["held"] == 1 and stats["held_details"][0]["reason"] == "Candidate already review"


def test_undo_leaves_miner_candidates_alone(db, tmp_path):
    miner = miner_candidate(db, status="pending")
    before = snapshot(miner)
    manifest = tmp_path / "m.jsonl"
    searlo_review.apply_decisions([approve_decision()], apply=True, manifest_path=manifest)
    searlo_review.undo(manifest, apply=True)
    assert snapshot(db.candidates[50]) == before
    assert not db.urls and db.discoveries[0]["status"] == "pending"
