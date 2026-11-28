"""Apply a human review of paid-search (Searlo) discoveries through the miner's store.

The legacy admin actions (the old ``approve_discovery`` / ``bulk_approve_discoveries``)
wrote ``researcher_urls`` one statement at a time and skip every miner guard:
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

from backend.discovery.mining_policy import url_key
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


def _miner_candidates(store: MiningStore, rid: int, pages: list[dict]) -> list[dict]:
    """The miner's own candidates (not ours) for this researcher's home page.

    Read-only, and identical in dry run and apply, so both see the same
    queue.  A candidate matches when its URL is the bundle's HOME page under
    the miner's URL normalisation (trailing slash, ``www``, scheme, case).
    """
    if "site_mining_candidates" not in store._tables:
        return []
    homes = {url_key(p["url"]) for p in pages if p["page_type"] == "HOME"} or {url_key(pages[0]["url"])}
    rows = store._all("SELECT id,researcher_id,status,url,urls,source_kind FROM site_mining_candidates "
                      "WHERE researcher_id=%s AND source_kind<>%s", (rid, SOURCE_KIND))
    found = []
    for row in rows:
        if row["researcher_id"] != rid or row["source_kind"] == SOURCE_KIND:
            continue
        if url_key(row["url"]) in homes:
            row = dict(row)
            if isinstance(row.get("urls"), (str, bytes, bytearray)):
                row["urls"] = json.loads(row["urls"])
            found.append(row)
    return found


def _reuse_approved_forms(pages: list[dict], miner: list[dict]) -> tuple[list[dict], bool]:
    """Spell pages the miner already approved exactly as it stored them.

    The miner's approved bundle was inserted under its own URL spelling; the
    preflight refuses a differently spelled alias.  Pages that match by the
    miner's URL normalisation take its spelling (and become existing rows);
    only pages it did not approve remain to be inserted, still through the
    full ``approve_bundle`` preflight.
    """
    approved = {url_key(p["url"]): p for c in miner if c["status"] == "approved"
                for p in (c.get("urls") or []) if isinstance(p, dict) and p.get("url")}
    approved.update({url_key(c["url"]): {"url": c["url"], "page_type": "HOME"}
                     for c in miner if c["status"] == "approved"})
    out, reused = [], False
    for page in pages:
        known = approved.get(url_key(page["url"]))
        if known and known["url"] != page["url"]:
            page, reused = dict(page, url=known["url"]), True
        out.append(page)
    return out, reused


def apply_decisions(decisions: list[dict], *, apply: bool = False,
                    manifest_path: str | Path | None = None) -> dict:
    """Apply approve/reject decisions; dry run (default) writes nothing."""
    if apply and not manifest_path:
        raise ValueError("--apply requires a manifest path so the change can be undone")
    return _run(decisions, apply=apply, manifest_path=manifest_path)


def _run(decisions: list[dict], *, apply: bool, manifest_path) -> dict:
    stats = {"apply": apply, "approved": 0, "rejected": 0, "held": 0, "skipped": 0,
             "stale": 0, "urls_inserted": 0, "urls_existing": 0,
             "reused_miner_bundle": 0}
    held: list[dict] = []
    manifest = open(manifest_path, "a", encoding="utf-8") if apply and manifest_path else None
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
                miner = _miner_candidates(store, rid, pages)
                blocked = next((c for c in miner if c["status"] in {"review", "rejected"}), None)
                if blocked:
                    # The miner already looked at this researcher's site and its
                    # verdict stands; a reviewed bundle does not overrule it.
                    stats["held"] += 1
                    held.append({"discovery_id": did, "researcher_id": rid,
                                 "reason": f"Candidate already {blocked['status']}"})
                    continue
                pages, reused = _reuse_approved_forms(pages, miner)
                stats["reused_miner_bundle"] += int(reused)
                home = next((p["url"] for p in pages if p["page_type"] == "HOME"), pages[0]["url"])
                name = f"{d['first_name']} {d['last_name']}".strip()
                cand = {"researcher_id": rid, "name": name, "url": home,
                        "source_url": row["url"] or home, "source_kind": SOURCE_KIND,
                        "evidence": {"page_type": "HOME", "searlo_discovery_id": did,
                                     "review": d.get("evidence", "")[:2000]}}
                store.enqueue(cand)
                own = store._one("SELECT * FROM site_mining_candidates WHERE candidate_key = %s",
                                 (candidate_key(cand),)) if "site_mining_candidates" in store._tables else None
                if own and own["status"] in {"review", "rejected"}:
                    # Our own earlier attempt was held; report it the same way in
                    # a dry run as the apply will.
                    stats["held"] += 1
                    held.append({"discovery_id": did, "researcher_id": rid,
                                 "reason": f"Candidate already {own['status']}"})
                    continue
                if apply:
                    if not own:
                        stats["held"] += 1
                        held.append({"discovery_id": did, "reason": "candidate could not be queued"})
                        continue
                    cand = dict(cand, id=own["id"])
                else:
                    cand["id"] = own["id"] if own else -did
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


_SUBPAGE_TYPES = {"research": "RESEARCH", "publications": "PUBLICATIONS",
                  "working_papers": "WORKING_PAPERS"}


def approve_one(discovery_id: int) -> dict:
    """Approve one pending discovery through the miner's guarded path (admin button).

    Returns {"approved": bool, "reason": str | None}. A bundle the preflight
    refuses stays pending and the reason is returned, never forced.
    """
    from backend.database.connection import fetch_one
    row = fetch_one(
        """SELECT ud.id, ud.researcher_id, ud.url, ud.subpages, ud.status,
                  r.first_name, r.last_name
           FROM url_discoveries ud JOIN researchers r ON r.id = ud.researcher_id
           WHERE ud.id = %s""", (discovery_id,))
    if not row or row["status"] != "pending" or not row["url"]:
        return {"approved": False, "reason": "Discovery is not pending"}
    subpages = row["subpages"]
    if isinstance(subpages, (str, bytes)):
        try:
            subpages = json.loads(subpages)
        except ValueError:
            subpages = []
    bundle = [{"url": row["url"], "page_type": "HOME"}]
    for sp in subpages or []:
        page_type = _SUBPAGE_TYPES.get(str(sp.get("page_type", "")).lower())
        if page_type and sp.get("url"):
            bundle.append({"url": sp["url"], "page_type": page_type})
    result = _run([{"discovery_id": row["id"], "researcher_id": row["researcher_id"],
                    "first_name": row["first_name"], "last_name": row["last_name"],
                    "decision": "approve", "bundle": bundle, "evidence": "Admin approval"}],
                  apply=True, manifest_path=None)
    if result.get("skipped_reason"):
        return {"approved": False, "reason": "The site miner is running; try again shortly"}
    if result["approved"]:
        return {"approved": True, "reason": None}
    held = result.get("held_details") or [{}]
    return {"approved": False, "reason": held[0].get("reason", "Not approved")}


def undo(manifest_path: str | Path, *, apply: bool = False) -> dict:
    """Delete URL rows a manifest inserted and reopen its discoveries.

    Clean only until the next scrape/extraction picks the pages up: deleting a
    URL cascades to its stored HTML, so anything fetched or extracted from it
    since the apply is lost with it (papers and feed events are kept).

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
