"""Clean up what the first Searlo apply (#299) wrote to ``site_mining_candidates``.

That run keyed reviewed bundles like the miner's own candidates, which had two
side effects this module repairs (#303 stops them from happening again):

``restore``  17 miner candidates (``cached_link``) that were still pending/retry
             had their status, reason, urls, evidence and attempts overwritten by
             the reviewed bundle.  Each is put back from the nightly backup taken
             before the apply.  A row the backup cannot restore (created after
             it, or not pending/retry in it) is reset to ``pending`` with
             evidence rebuilt from its own columns, so the miner verifies it
             again.  Its URL rows stay as they are.

``rekey``    The ``searlo_review`` rows sat under the miner's candidate_key, so
             the miner could never queue its own candidate for those sites.
             Approved rows move to the new suffixed key (history and undo keep
             working).  Rows left in ``review`` are deleted: they record a hold
             that wrote nothing, a held own row blocks a re-run for good, and
             their discoveries are still pending, so the next run re-evaluates
             them against today's data.

Dry run by default.  Every change appends the replaced row to a JSONL backup so
``undo`` can put it back.
"""
from __future__ import annotations

import gzip
import json
import re
import time
from pathlib import Path

from backend.discovery.mining_store import MiningStore, candidate_key
from backend.discovery.searlo_review import SOURCE_KIND

MARK = "searlo_discovery_id"
_TABLE = "site_mining_candidates"
_RESTORE_FIELDS = ("status", "reason", "urls", "evidence", "attempts", "next_attempt_at")
_STEPS = ("restore", "rekey")
_ESCAPES = {"0": "\0", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "b": "\b"}
_QUOTE_OR_BACKSLASH = re.compile(r"[\\']")


# --- reading the backup ------------------------------------------------------

def parse_insert(line: str):
    """Yield the value lists of one mysqldump ``INSERT ... VALUES (...),(...);`` line."""
    i, n = line.index(" VALUES ") + 8, len(line)
    while i < n:
        if line[i] != "(":
            i += 1
            continue
        i += 1
        row = []
        while True:
            if line.startswith("_binary ", i):
                i += 8
            c = line[i]
            if c == "'":
                i += 1
                parts = []
                while True:
                    m = _QUOTE_OR_BACKSLASH.search(line, i)
                    parts.append(line[i:m.start()])
                    if m.group() == "'":
                        if line.startswith("''", m.start()):       # doubled quote
                            parts.append("'")
                            i = m.start() + 2
                            continue
                        i = m.start() + 1
                        break
                    esc = line[m.start() + 1]
                    parts.append(_ESCAPES.get(esc, esc))
                    i = m.start() + 2
                row.append("".join(parts))
            else:
                j = i
                while line[j] not in ",)":
                    j += 1
                token = line[i:j]
                row.append(None if token == "NULL" else int(token) if re.fullmatch(r"-?\d+", token) else float(token))
                i = j
            if line[i] == ",":
                i += 1
            else:                                                   # ")"
                i += 1
                break
        yield row


def read_candidates_from_dump(path: str | Path, wanted: set[int]) -> dict[int, dict]:
    """Rows of ``site_mining_candidates`` with the given ids from a mysqldump file (.sql or .sql.gz)."""
    path = str(path)
    opener = gzip.open if path.endswith(".gz") else open
    columns, found = [], {}
    in_table = in_create = False
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not in_table:
                in_table = line.startswith(f"CREATE TABLE `{_TABLE}`")
                in_create = in_table
                continue
            if in_create:
                m = re.match(r"\s+`(\w+)` ", line)
                if m:
                    columns.append(m.group(1))
                elif line.startswith(")"):
                    in_create = False
                continue
            if line.startswith(f"INSERT INTO `{_TABLE}`"):
                m = re.match(rf"INSERT INTO `{_TABLE}` \(([^)]*)\) VALUES ", line)
                cols = [c.strip("` ") for c in m.group(1).split(",")] if m else columns
                for values in parse_insert(line):
                    row = dict(zip(cols, values))
                    if row.get("id") in wanted:
                        found[row["id"]] = row
            elif line.startswith("UNLOCK TABLES"):
                break
    return found


# --- plan --------------------------------------------------------------------

def _json_text(value):
    return value if value is None or isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _minimal_evidence(store: MiningStore, row: dict) -> dict:
    """Rebuild what the miner needs from the row's own columns (the original is lost)."""
    evidence = {"restored_after_searlo_overwrite": True, "anchor": row["name"]}
    source = [u for u in store._all("SELECT id,researcher_id,url FROM researcher_urls WHERE url=%s",
                                    (row["source_url"],)) if u["url"] == row["source_url"]]
    if source:
        evidence.update(source_researcher_id=source[0]["researcher_id"], source_url_id=source[0]["id"])
    return evidence


def plan_restore(store: MiningStore, dump_path) -> list[dict]:
    rows = store._all(f"SELECT * FROM {_TABLE} WHERE source_kind<>%s AND evidence LIKE %s",
                      (SOURCE_KIND, f"%{MARK}%"))
    rows = [r for r in rows if MARK in (_json_text(r["evidence"]) or "")]
    originals = read_candidates_from_dump(dump_path, {r["id"] for r in rows}) if rows else {}
    actions = []
    for row in rows:
        orig = originals.get(row["id"])
        if orig and orig["candidate_key"] == row["candidate_key"] and orig["status"] in {"pending", "retry"}:
            after = {f: orig[f] for f in _RESTORE_FIELDS}
            how = "backup"
        else:
            after = {"status": "pending", "reason": None, "urls": None, "attempts": 0,
                     "evidence": json.dumps(_minimal_evidence(store, row), ensure_ascii=False),
                     "next_attempt_at": None}
            how = "reset_no_backup" if not orig else "reset_backup_not_pending"
        actions.append({"op": "restore", "id": row["id"], "how": how, "researcher_id": row["researcher_id"],
                        "from_status": row["status"], "to_status": after["status"],
                        "before": {f: row[f] for f in (*_RESTORE_FIELDS, "updated_at")}, "after": after})
    return actions


def plan_rekey(store: MiningStore) -> list[dict]:
    actions = []
    for row in store._all(f"SELECT * FROM {_TABLE} WHERE source_kind=%s", (SOURCE_KIND,)):
        new_key = candidate_key(row)
        if row["candidate_key"] == new_key:
            continue
        base = {"id": row["id"], "researcher_id": row["researcher_id"], "status": row["status"]}
        if store._one(f"SELECT id FROM {_TABLE} WHERE candidate_key = %s", (new_key,)):
            actions.append(dict(base, op="skip", why="new key already taken"))
        elif row["status"] == "approved":
            actions.append(dict(base, op="rekey", old_key=row["candidate_key"], new_key=new_key))
        elif row["status"] == "review":
            actions.append(dict(base, op="delete", row=dict(row), updated_at=row["updated_at"]))
        else:
            actions.append(dict(base, op="skip", why=f"status {row['status']} is left alone"))
    return actions


def summarize(actions: list[dict]) -> dict:
    out: dict[str, int] = {}
    for a in actions:
        key = a["op"] + (f":{a['how']}" if a["op"] == "restore" else f":{a['why']}" if a["op"] == "skip" else "")
        out[key] = out.get(key, 0) + 1
    return out


# --- apply / undo ---------------------------------------------------------------

def _dump(record) -> str:
    return json.dumps(record, ensure_ascii=False, default=str) + "\n"


def _apply_one(store: MiningStore, a: dict) -> bool:
    if a["op"] == "restore":
        after = a["after"]
        _, count = store._execute(
            f"""UPDATE {_TABLE} SET status=%s,reason=%s,urls=%s,evidence=%s,
                  attempts=%s,next_attempt_at=COALESCE(%s,UTC_TIMESTAMP()),updated_at=UTC_TIMESTAMP()
                WHERE id=%s AND updated_at=%s AND source_kind<>%s""",
            (after["status"], after["reason"], _json_text(after["urls"]), _json_text(after["evidence"]),
             after["attempts"], after["next_attempt_at"], a["id"], a["before"]["updated_at"], SOURCE_KIND))
        return count == 1
    if a["op"] == "rekey":
        _, count = store._execute(
            f"UPDATE {_TABLE} SET candidate_key=%s WHERE id=%s AND candidate_key=%s AND source_kind=%s",
            (a["new_key"], a["id"], a["old_key"], SOURCE_KIND))
        return count == 1
    if a["op"] == "delete":
        _, count = store._execute(
            f"DELETE FROM {_TABLE} WHERE id=%s AND source_kind=%s AND status='review' AND updated_at=%s",
            (a["id"], SOURCE_KIND, a["updated_at"]))
        return count == 1
    return False


def run(*, steps=_STEPS, dump_path=None, apply=False, backup_path=None) -> dict:
    steps = tuple(steps)
    if unknown := set(steps) - set(_STEPS):
        raise ValueError(f"unknown steps: {sorted(unknown)}")
    if apply and not backup_path:
        raise ValueError("--apply requires --backup so the change can be undone")
    if "restore" in steps and not dump_path:
        raise ValueError("the restore step needs --dump (the backup taken before the apply)")
    report = {"apply": apply, "steps": {}}
    with MiningStore(apply=False) as probe:                     # plan without DDL, whatever --apply says
        if not probe.acquired:
            return dict(report, skipped_reason="another_mining_job_is_running")
        actions = (plan_restore(probe, dump_path) if "restore" in steps else []) + \
                  (plan_rekey(probe) if "rekey" in steps else [])
    report["actions"] = summarize(actions)
    report["details"] = [{k: v for k, v in a.items() if k not in {"before", "after", "row"}} for a in actions]
    if not apply:
        return report
    applied = skipped = 0
    with MiningStore(apply=True) as store, open(backup_path, "a", encoding="utf-8") as backup:
        if not store.acquired:
            return dict(report, skipped_reason="another_mining_job_is_running")
        for a in actions:
            if a["op"] == "skip":
                continue
            store.conn.start_transaction()
            try:
                if _apply_one(store, a):
                    backup.write(_dump(dict(a, at=time.time())))
                    backup.flush()
                    store.conn.commit()
                    applied += 1
                else:
                    store.conn.rollback()
                    skipped += 1                                  # row changed since the plan
            except BaseException:
                store.conn.rollback()
                raise
    return dict(report, applied=applied, changed_since_plan=skipped)


def undo(backup_path, *, apply=False) -> dict:
    entries = [json.loads(line) for line in Path(backup_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    stats = {"apply": apply, "restored": 0, "rekeyed": 0, "reinserted": 0, "left_alone": []}
    with MiningStore(apply=apply) as store:
        if not store.acquired:
            return dict(stats, skipped_reason="another_mining_job_is_running")
        for e in reversed(entries):
            if apply:
                store.conn.start_transaction()
            try:
                if e["op"] == "restore":
                    b = e["before"]
                    if apply:
                        _, count = store._execute(
                            f"""UPDATE {_TABLE} SET status=%s,reason=%s,urls=%s,evidence=%s,attempts=%s,
                                  next_attempt_at=%s,updated_at=%s WHERE id=%s AND evidence=CAST(%s AS JSON)""",
                            (b["status"], b["reason"], _json_text(b["urls"]), _json_text(b["evidence"]),
                             b["attempts"], b["next_attempt_at"], b["updated_at"], e["id"],
                             _json_text(e["after"]["evidence"])))
                        if count != 1:
                            stats["left_alone"].append(e["id"])
                            store.conn.rollback()
                            continue
                    stats["restored"] += 1
                elif e["op"] == "rekey":
                    if apply:
                        _, count = store._execute(
                            f"UPDATE {_TABLE} SET candidate_key=%s WHERE id=%s AND candidate_key=%s",
                            (e["old_key"], e["id"], e["new_key"]))
                        if count != 1:
                            stats["left_alone"].append(e["id"])
                            store.conn.rollback()
                            continue
                    stats["rekeyed"] += 1
                elif e["op"] == "delete":
                    r = e["row"]
                    if apply:
                        store._execute(
                            f"""INSERT INTO {_TABLE}
                                (id,candidate_key,researcher_id,name,url,source_url,source_kind,evidence,status,
                                 reason,urls,attempts,next_attempt_at,created_at,updated_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (r["id"], r["candidate_key"], r["researcher_id"], r["name"], r["url"], r["source_url"],
                             r["source_kind"], _json_text(r["evidence"]), r["status"], r["reason"],
                             _json_text(r["urls"]), r["attempts"], r["next_attempt_at"], r["created_at"],
                             r["updated_at"]))
                    stats["reinserted"] += 1
                if apply:
                    store.conn.commit()
            except BaseException:
                if apply:
                    store.conn.rollback()
                raise
    return stats
