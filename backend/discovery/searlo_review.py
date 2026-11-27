"""Apply a human review of paid-search (Searlo) discoveries through the miner's store.

The legacy admin actions (``approve_discovery`` / ``bulk_approve_discoveries``)
write ``researcher_urls`` one statement at a time and skip every miner guard:
identity holds, URL ownership, scope, inactive rows, prior rejections.  This
module applies reviewed decisions the same way the site miner applies a verified
bundle: one transaction per researcher (``MiningStore.approve_bundle``) that
preflights every URL, inserts the whole bundle and marks the ``url_discoveries``
row approved together.  A bundle the preflight refuses stays pending and is
reported; nothing is forced.

Decisions come from a JSON file (a list of objects)::

    {"discovery_id": 12, "researcher_id": 5788, "first_name": "Maisy",
     "last_name": "Wong", "decision": "approve",
     "bundle": [{"url": "https://...", "page_type": "HOME"},
                {"url": "https://.../research", "page_type": "RESEARCH"}],
     "evidence": "full name in h1; 2 known titles on /research"}

``decision`` is ``approve`` or ``reject`` (anything else, e.g. ``unsure``, is
skipped).  Every applied change is appended to a JSONL manifest, which
``undo`` uses to delete the inserted URL rows and reopen the discoveries.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from backend.discovery.mining_store import MiningStore, candidate_key

logger = logging.getLogger(__name__)

SOURCE_KIND = "searlo_review"
_PAGE_TYPES = {"HOME", "RESEARCH", "PUBLICATIONS", "WORKING_PAPERS"}


def load_decisions(path: str | Path) -> list[dict]:
    decisions = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(decisions, list):
        raise ValueError("decisions file must contain a JSON list")
    seen = set()
    for d in decisions:
        for key in ("discovery_id", "researcher_id", "first_name", "last_name", "decision"):
            if key not in d:
                raise ValueError(f"decision missing {key!r}: {d}")
        if d["discovery_id"] in seen:
            raise ValueError(f"duplicate discovery_id {d['discovery_id']}")
        seen.add(d["discovery_id"])
        if d["decision"] == "approve":
            bundle = d.get("bundle") or []
            if not bundle or any(p.get("page_type") not in _PAGE_TYPES or not p.get("url") for p in bundle):
                raise ValueError(f"approve decision needs a bundle with page types {sorted(_PAGE_TYPES)}: {d}")
    return decisions


def _pending(store: MiningStore, discovery_id: int, researcher_id: int):
    return store._one(
        "SELECT id,researcher_id,url,status FROM url_discoveries WHERE id=%s",
        (discovery_id,),
    )


def _manifest(manifest, record: dict) -> None:
    if manifest is not None:
        manifest.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        manifest.flush()


def apply_decisions(decisions: list[dict], *, apply: bool = False,
                    manifest_path: str | Path | None = None) -> dict:
    """Apply approve/reject decisions; dry run (default) writes nothing."""
    if apply and not manifest_path:
        raise ValueError("--apply requires a manifest path so the change can be undone")
    stats = {"apply": apply, "approved": 0, "rejected": 0, "held": 0, "skipped": 0,
             "stale": 0, "urls_inserted": 0, "urls_existing": 0}
    held: list[dict] = []
    manifest = open(manifest_path, "a", encoding="utf-8") if apply else None
    try:
        with MiningStore(apply=apply) as store:
            if not store.acquired:
                return dict(stats, skipped_reason="another_mining_job_is_running")
            for d in decisions:
                did, rid = d["discovery_id"], d["researcher_id"]
                if d["decision"] not in {"approve", "reject"}:
                    stats["skipped"] += 1
                    continue
                row = _pending(store, did, rid)
                if not row or row["status"] != "pending" or row["researcher_id"] != rid:
                    stats["stale"] += 1
                    held.append({"discovery_id": did, "reason": "discovery is no longer pending for this researcher"})
                    continue
                if d["decision"] == "reject":
                    if apply:
                        store.resolve_discovery(did, "rejected")
                        _manifest(manifest, {"op": "reject", "discovery_id": did, "at": time.time()})
                    stats["rejected"] += 1
                    continue
                pages = [{"url": p["url"], "page_type": p["page_type"]} for p in d["bundle"]]
                home = next((p["url"] for p in pages if p["page_type"] == "HOME"), pages[0]["url"])
                name = f"{d['first_name']} {d['last_name']}".strip()
                cand = {"researcher_id": rid, "name": name, "url": home,
                        "source_url": row["url"] or home, "source_kind": SOURCE_KIND,
                        "evidence": {"page_type": "HOME", "searlo_discovery_id": did,
                                     "review": d.get("evidence", "")[:2000]}}
                store.enqueue(cand)
                if apply:
                    current = store._one("SELECT * FROM site_mining_candidates WHERE candidate_key = %s",
                                         (candidate_key(cand),))
                    if not current:
                        stats["held"] += 1
                        held.append({"discovery_id": did, "reason": "candidate could not be queued"})
                        continue
                    cand = dict(cand, id=current["id"])
                else:
                    cand["id"] = -did
                    cand["candidate_key"] = candidate_key(cand)
                    cand["status"] = "pending"
                    cand["attempts"] = 0
                outcome = store.approve_bundle(
                    cand, pages, (d["first_name"], d["last_name"]),
                    in_transaction=(lambda s, o, did=did: s.resolve_discovery(did, "approved")) if apply else None,
                )
                if outcome["held"]:
                    stats["held"] += 1
                    held.append({"discovery_id": did, "researcher_id": rid, "reason": outcome["reason"]})
                    continue
                stats["approved"] += 1
                stats["urls_inserted"] += len(outcome["inserted"])
                stats["urls_existing"] += len(outcome["existing"])
                _manifest(manifest, {"op": "approve", "discovery_id": did, "researcher_id": rid,
                                     "candidate_id": cand["id"], "at": time.time(),
                                     "inserted": [{"id": r["id"], "url": r["url"], "page_type": r["page_type"]}
                                                  for r in outcome["inserted"]],
                                     "existing": [{"id": r["id"], "url": r["url"]} for r in outcome["existing"]]})
    finally:
        if manifest:
            manifest.close()
    stats["held_details"] = held
    return stats


def undo(manifest_path: str | Path, *, apply: bool = False) -> dict:
    """Delete URL rows a manifest inserted and reopen its discoveries.

    A row is deleted only if id, researcher and URL still match what was
    inserted, so rows that changed since are left alone and reported.
    """
    entries = [json.loads(line) for line in Path(manifest_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    stats = {"apply": apply, "urls_deleted": 0, "discoveries_reopened": 0, "left_alone": []}
    with MiningStore(apply=apply) as store:
        if not store.acquired:
            return dict(stats, skipped_reason="another_mining_job_is_running")
        for e in entries:
            if apply:
                store.conn.start_transaction()
            try:
                for u in e.get("inserted", []):
                    row = store._one("SELECT id FROM researcher_urls WHERE id=%s AND researcher_id=%s AND url=%s",
                                     (u["id"], e["researcher_id"], u["url"]))
                    if not row:
                        stats["left_alone"].append(u)
                        continue
                    if apply:
                        store._execute("DELETE FROM researcher_urls WHERE id=%s", (u["id"],))
                    stats["urls_deleted"] += 1
                if apply:
                    if e["op"] == "approve":
                        store._execute("UPDATE site_mining_candidates SET status='review',reason=%s WHERE id=%s AND status='approved'",
                                       ("Searlo review undone", e["candidate_id"]))
                    store._execute("UPDATE url_discoveries SET status='pending',reviewed_at=NULL WHERE id=%s",
                                   (e["discovery_id"],))
                    store.conn.commit()
                stats["discoveries_reopened"] += 1
            except BaseException:
                if apply:
                    store.conn.rollback()
                raise
    return stats
