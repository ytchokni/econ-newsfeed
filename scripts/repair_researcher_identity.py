"""Repair split researcher identities and misattributed page papers.

Dry run by default: prints what each step would change and its counts.
Pass --apply to write. Steps run in this order (later steps see earlier merges):

  strip_repeated_surnames         #249 "Peter Hull" + "Hull" -> "Peter" (merged into an
                                  existing "Peter Hull" record when there is one)
  merge_openalex_twins            #259 records sharing an OpenAlex author ID whose
                                  names agree ("Andy"/"Andrew" Garin) become one
  clear_conflicting_openalex_ids  #259 an ID still on several people stays with the
                                  one OpenAlex names ("Marcel", not "Quinn" Fafchamps)
  merge_shared_url_twins          #260 records tracking the same URL whose names
                                  agree (Gordon B. Dahl 24 / 17140) become one
  route_page_papers_to_owner      #260 byline authors on a page that name its owner
                                  move to the owner ("Dora Costa" stub -> Dora L. Costa)
  deactivate_shared_pages         #262 pages tracked for unrelated people (Bundesbank,
                                  ESRI, stanford.edu/research) stop being crawled; owner
                                  attachments that came only from them are removed
  flag_unowned_pages              #262 review list of pages that never name their owner;
                                  deletes nothing, sets researcher_urls.ownership_flag

Run one step at a time, each with its own backup file. In production the api
container has no volume, so mount /backups into a one-off container:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm -v /backups:/backups \
      api python scripts/repair_researcher_identity.py --steps merge_openalex_twins \
      --apply --backup /backups/identity-merge_openalex_twins.jsonl

    poetry run python scripts/repair_researcher_identity.py                  # dry run, all steps
    poetry run python scripts/repair_researcher_identity.py --report plan.json   # every action
    poetry run python scripts/repair_researcher_identity.py --steps X --apply --backup X.jsonl
    poetry run python scripts/repair_researcher_identity.py --undo X.jsonl              # dry run
    poetry run python scripts/repair_researcher_identity.py --undo X.jsonl --units 3-9 --apply

Each merge or move is its own short transaction, so the script can run while
the API and workers are up. Researcher merges never touch feed_events: events
belong to papers. Only deactivate_shared_pages deletes papers (and with them
their events): items with no remaining author that came only from shared pages.
Safe to re-run: every step selects the current violations. --apply requires
--backup: before each transaction commits, the rows it replaces or deletes
(including cascaded children of deleted papers and URLs) are appended to that
file; --undo restores them, newest change first (--steps / --units narrow it).
Undo restores a value only while it still holds what the repair wrote, so
later edits win, and it skips units that would re-create an opted-out (#256)
researcher or URL.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime
import decimal
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.database import fetch_all, get_connection  # noqa: E402
from backend.database.identity import (  # noqa: E402
    OWNER_NOT_NAMED, SHARED_PAGE, canonical_order, cluster_same_person,
    match_score, matches_display_name, merge_researcher_into, page_ownership_problem,
    same_person, split_name,
)
from backend.database.researchers import refresh_has_top5_for  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("repair_identity")

APPLY = False
SAMPLES = 15
REPORT: dict[str, list] = collections.defaultdict(list)
_RETRYABLE = {1205, 1213}  # lock wait timeout, deadlock


def _name(r: dict) -> str:
    return f"{r['first_name']} {r['last_name']}"


def _chunks(items: list, size: int = 2000):
    for i in range(0, len(items), size):
        yield items[i:i + size]



# ---------------------------------------------------------------- backup / undo
#
# With --apply every transaction first records the rows it is about to change
# (full rows for anything deleted or rewritten) as one JSON line in the
# --backup file, and a commit marker once it has committed. --undo replays the
# committed units newest first. Feed events and other children of deleted
# papers are captured through the foreign keys, so undo re-creates them.

class Backup:
    def __init__(self, path: str):
        self.path = path
        self.next_id = 1
        if Path(path).exists():
            for line in Path(path).read_text().splitlines():
                rec = json.loads(line)
                self.next_id = max(self.next_id, int(rec.get("unit") or rec.get("commit") or 0) + 1)

    def _append(self, rec: dict) -> None:
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec, default=_encode) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def record(self, step: str, label: str, groups: list) -> int:
        unit = self.next_id
        self.next_id += 1
        self._append({"unit": unit, "step": step, "label": label, "groups": groups})
        return unit

    def committed(self, unit: int) -> None:
        self._append({"commit": unit})


BACKUP: Backup | None = None
CURRENT_STEP = ""
_GROUPS: list[list] = []


def _encode(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return {"__dt__": value.isoformat()}
    if isinstance(value, (bytes, bytearray)):
        return {"__b64__": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, decimal.Decimal):
        return {"__dec__": str(value)}
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value))


def _decode(value):
    if isinstance(value, dict):
        if "__dt__" in value:
            return datetime.datetime.fromisoformat(value["__dt__"])
        if "__b64__" in value:
            return base64.b64decode(value["__b64__"])
        if "__dec__" in value:
            return decimal.Decimal(value["__dec__"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _select(cur, table: str, col: str, values, columns: str = "*") -> list[dict]:
    values = list(values)
    if not values:
        return []
    try:
        cur.execute(f"SELECT {columns} FROM `{table}` WHERE `{col}` IN ({','.join(['%s'] * len(values))})",
                    tuple(values))
    except Exception as exc:
        if getattr(exc, "errno", None) == 1146:  # optional table not created
            return []
        raise
    return [dict(r) for r in cur.fetchall()]


def _group() -> list:
    """Start a new group of restore operations (undone newest group first)."""
    _GROUPS.append([])
    return _GROUPS[-1]


def _merge_with_backup(cur, canonical_id: int, duplicate_id: int) -> None:
    """merge_researcher_into, recording what it changes and how it left the survivor."""
    researchers_op = _capture_merge(cur, canonical_id, duplicate_id)
    if not merge_researcher_into(cur, canonical_id, duplicate_id):
        # A record already gone (merged by another unit): roll this unit back
        # rather than count a merge that did not happen.
        raise RuntimeError(f"merge {duplicate_id} -> {canonical_id} found a missing record")
    researchers_op.append({str(r["id"]): r for r in _select(cur, "researchers", "id", [canonical_id])})


def _capture_merge(cur, canonical_id: int, duplicate_id: int) -> list:
    """Everything merge_researcher_into changes or deletes for this pair.
    Returns the researchers op, so the caller can attach the post-merge row."""
    ops = _group()
    ids = (canonical_id, duplicate_id)
    researchers_op = ["rows", "researchers", ["id"], _select(cur, "researchers", "id", ids)]
    ops.append(researchers_op)
    urls = _select(cur, "researcher_urls", "researcher_id", ids)
    ops.append(["rows", "researcher_urls", ["id"], urls])
    canonical_urls = {u["url"][:500] for u in urls if u["researcher_id"] == canonical_id}
    dup_url_ids = [u["id"] for u in urls if u["researcher_id"] == duplicate_id]
    doomed = [u["id"] for u in urls
              if u["researcher_id"] == duplicate_id and u["url"][:500] in canonical_urls]
    ops.append(["rows", "html_content", ["id"], _select(cur, "html_content", "url_id", doomed)])
    ops.append(["rows", "html_snapshots", ["id"], _select(cur, "html_snapshots", "url_id", doomed)])
    refs = _select(cur, "html_content", "researcher_id", ids, "id, url_id, researcher_id")
    refs += [r for r in _select(cur, "html_content", "url_id", dup_url_ids, "id, url_id, researcher_id")
             if r not in refs]
    # Rows of doomed URL rows are deleted and restored whole (above).
    ops.append(["set", "html_content", "researcher_id",
                [[r["id"], r["researcher_id"], canonical_id] for r in refs
                 if r["researcher_id"] != canonical_id and r["url_id"] not in doomed]])
    for table in ("authorship", "researcher_snapshots", "url_discoveries"):
        ops.append(["rows", table, ["id"], _select(cur, table, "researcher_id", ids)])
    for table, key in (("user_follows", "user_id"), ("researcher_jel_codes", "jel_code"),
                       ("researcher_fields", "field_id")):
        ops.append(["pairs", table, key, list(ids), _select(cur, table, "researcher_id", ids)])
    ops.append(["rows", "researcher_jel_state", ["researcher_id"],
                _select(cur, "researcher_jel_state", "researcher_id", ids)])
    mining = _select(cur, "site_mining_candidates", "researcher_id", [duplicate_id], "id, researcher_id")
    ops.append(["set", "site_mining_candidates", "researcher_id",
                [[r["id"], r["researcher_id"], canonical_id] for r in mining]])
    return researchers_op


_FK_CHILDREN: dict[str, list[tuple[str, str, str]]] = {}


def _fk_children(cur, table: str) -> list[tuple[str, str, str]]:
    if table not in _FK_CHILDREN:
        cur.execute(
            """SELECT TABLE_NAME AS t, COLUMN_NAME AS c, REFERENCED_COLUMN_NAME AS p
               FROM information_schema.KEY_COLUMN_USAGE
               WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME = %s""", (table,))
        _FK_CHILDREN[table] = [(r["t"], r["c"], r["p"]) for r in cur.fetchall()]
    return _FK_CHILDREN[table]


def _capture_tree(cur, table: str, col: str, values, ops: list, depth: int = 0) -> None:
    """Rows about to be deleted with every row that cascades from them, parents first."""
    rows = _select(cur, table, col, values)
    if not rows:
        return
    ops.append(["insert", table, rows])
    if depth < 4:
        for child, child_col, parent_col in _fk_children(cur, table):
            _capture_tree(cur, child, child_col, {r[parent_col] for r in rows}, ops, depth + 1)


_GENERATED: dict[str, set[str]] = {}


def _writable(cur, table: str, row: dict) -> dict:
    """Row without generated columns (html_content.has_stored_content)."""
    if table not in _GENERATED:
        cur.execute("""SELECT COLUMN_NAME AS c FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                         AND (EXTRA LIKE '%%VIRTUAL GENERATED%%' OR EXTRA LIKE '%%STORED GENERATED%%')""",
                    (table,))
        _GENERATED[table] = {r["c"] for r in cur.fetchall()}
    return {k: v for k, v in row.items() if k not in _GENERATED[table]}


def _insert(cur, table: str, row: dict, ignore: bool = False) -> None:
    row = _writable(cur, table, row)
    cols = list(row)
    cur.execute(f"INSERT {'IGNORE ' if ignore else ''}INTO `{table}` ({', '.join(f'`{c}`' for c in cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})", [row[c] for c in cols])


_UNDO_STATS: collections.Counter = collections.Counter()


def _restore_rows(cur, table: str, pk: list[str], rows: list[dict], after: dict | None = None) -> None:
    """Put rows back by primary key. With `after` ({id: row as the repair
    left it}), a column is restored only if it still holds that value."""
    for row in rows:
        row = _writable(cur, table, row)
        where = " AND ".join(f"`{c}` = %s" for c in pk)
        key = [row[c] for c in pk]
        cur.execute(f"SELECT * FROM `{table}` WHERE {where}", key)
        current = cur.fetchone()
        if current:
            cols = [c for c in row if c not in pk]
            left = (after or {}).get(str(key[0])) if len(pk) == 1 else None
            if left is not None:
                kept = [c for c in cols if c in left and current.get(c) != left[c]]
                _UNDO_STATS["kept_later_values"] += len(kept)
                if kept:
                    log.warning("  %s %s changed since the repair; keeping current %s", table, key, kept)
                cols = [c for c in cols if c not in kept]
            if cols:
                cur.execute(f"UPDATE `{table}` SET {', '.join(f'`{c}` = %s' for c in cols)} WHERE {where}",
                            [row[c] for c in cols] + key)
        else:
            _insert(cur, table, row)


def _undo_op(cur, op: list) -> None:
    kind = op[0]
    if kind == "rows":
        _restore_rows(cur, op[1], op[2], op[3], op[4] if len(op) > 4 else None)
    elif kind == "set":
        _, table, col, pairs = op
        for pair in pairs:
            if len(pair) == 3:  # [id, old, value the repair wrote]: restore only if unchanged since
                row_id, value, written = pair
                cur.execute(f"SELECT `{col}` AS v FROM `{table}` WHERE id = %s", (row_id,))
                current = cur.fetchone()
                if current is None:
                    continue
                if current["v"] != written:
                    _UNDO_STATS["kept_later_values"] += 1
                    log.warning("  %s %s.%s changed since the repair; keeping current value", table, row_id, col)
                    continue
                cur.execute(f"UPDATE `{table}` SET `{col}` = %s WHERE id = %s", (value, row_id))
            else:
                row_id, value = pair
                cur.execute(f"UPDATE `{table}` SET `{col}` = %s WHERE id = %s", (value, row_id))
    elif kind == "pairs":
        # (researcher_id, key) tables: drop what the merge moved onto the
        # surviving record, then put the original rows back.
        _, table, key, ids, rows = op
        canonical = ids[0]
        own = {r[key] for r in rows if r["researcher_id"] == canonical}
        moved = {r[key] for r in rows if r["researcher_id"] != canonical} - own
        for k in moved:
            cur.execute(f"DELETE FROM `{table}` WHERE researcher_id = %s AND `{key}` = %s", (canonical, k))
        for r in rows:
            _insert(cur, table, r, ignore=True)
    elif kind == "insert":
        _, table, rows = op
        for r in rows:
            try:
                _insert(cur, table, r, ignore=True)
            except Exception as exc:
                # The feed-event snapshot guard refuses a new_paper event whose
                # page lost its snapshots; restore everything else.
                if getattr(exc, "errno", None) != 1644:
                    raise
                log.warning("  could not restore %s row %s: %s", table, r.get("id"), exc)
    else:
        raise ValueError(f"unknown backup operation {kind}")


def _parse_units(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return out


def _opted_out(unit: dict) -> list[str]:
    """Researchers or URLs this unit would re-create that have opted out (#256)."""
    try:
        from backend.database.optouts import is_name_opted_out, is_url_opted_out
    except ImportError:  # opt-out tombstones not deployed: nothing to protect
        return []
    hits = []
    for group in unit["groups"]:
        for op in group:
            rows = op[3] if op[0] == "rows" else op[2] if op[0] == "insert" else []
            table = op[1]
            for r in rows if isinstance(rows, list) else []:
                if table == "researchers" and is_name_opted_out(r.get("first_name", ""), r.get("last_name", "")):
                    hits.append(f"researcher {r.get('id')}")
                elif table == "researcher_urls" and is_url_opted_out(r.get("url", "")):
                    hits.append(f"url {r.get('id')}")
    return hits


def undo(path: str, steps: set[str] | None = None, unit_ids: set[int] | None = None) -> dict:
    """Restore committed units of a backup file, newest first."""
    units, committed = {}, set()
    for line in Path(path).read_text().splitlines():
        rec = json.loads(line)
        if "unit" in rec:
            units[rec["unit"]] = _decode(rec)
        elif "commit" in rec:
            committed.add(rec["commit"])
    todo = [units[u] for u in sorted(committed, reverse=True) if u in units
            and (steps is None or units[u]["step"] in steps)
            and (unit_ids is None or u in unit_ids)]
    skipped_optout = 0
    for unit in list(todo):
        hits = _opted_out(unit)
        if hits:
            log.warning("  skip unit %s (%s): would re-create opted-out %s", unit["unit"], unit["label"], hits[:3])
            todo.remove(unit)
            skipped_optout += 1
    by_step = collections.Counter(u["step"] for u in todo)
    log.info("  %d committed units to undo (%d recorded): %s", len(todo), len(units), dict(by_step))
    restored = 0
    for unit in todo:
        def writes(cur, unit=unit):
            for group in reversed(unit["groups"]):
                for op in group:
                    _undo_op(cur, op)
        if APPLY:
            restored += _write(f"undo {unit['unit']} {unit['label']}", writes, record=False)
    if APPLY and restored < len(todo):
        log.error("  %d units could not be restored (see FAILED above)", len(todo) - restored)
    return {"units": len(todo), "restored": restored, "skipped_opted_out": skipped_optout,
            "kept_later_values": _UNDO_STATS["kept_later_values"], **{f"units_{k}": v for k, v in by_step.items()}}


def _write(label: str, fn, record: bool = True) -> bool:
    """Run fn(cursor) in its own transaction (retrying deadlocks) when --apply."""
    if not APPLY:
        return True
    for attempt in range(3):
        with get_connection() as conn:
            cursor = conn.cursor(dictionary=True, buffered=True)
            _GROUPS.clear()
            try:
                fn(cursor)
                unit = BACKUP.record(CURRENT_STEP, label, list(_GROUPS)) if record and BACKUP else None
                conn.commit()
                if unit is not None:
                    BACKUP.committed(unit)
                return True
            except Exception as exc:
                conn.rollback()
                if getattr(exc, "errno", None) in _RETRYABLE and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                log.exception("FAILED %s (rolled back)", label)
                return False
            finally:
                cursor.close()
    return False


def _weights(ids: list[int]) -> dict[int, dict]:
    out = {}
    for chunk in _chunks(sorted(set(ids))):
        ph = ",".join(["%s"] * len(chunk))
        for r in fetch_all(
            f"""SELECT r.id, r.first_name, r.last_name, r.openalex_author_id,
                  (SELECT COUNT(*) FROM researcher_urls u WHERE u.researcher_id = r.id) AS n_urls,
                  (SELECT COUNT(*) FROM user_follows f WHERE f.researcher_id = r.id) AS n_follows,
                  (SELECT COUNT(*) FROM authorship a WHERE a.researcher_id = r.id) AS n_pubs
                FROM researchers r WHERE r.id IN ({ph})""", tuple(chunk)):
            out[r["id"]] = r
    return out


def _merge_plan(clusters: list[list[dict]]) -> list[tuple[dict, list[dict]]]:
    plan = []
    for cluster in clusters:
        ranked = sorted(cluster, key=canonical_order)
        plan.append((ranked[0], ranked[1:]))
    return plan


def _apply_merges(step: str, plan: list[tuple[dict, list[dict]]]) -> int:
    done = 0
    for canonical, dups in plan:
        REPORT[step].append({"keep": canonical["id"], "name": _name(canonical),
                             "merge": [{"id": d["id"], "name": _name(d)} for d in dups]})

        def writes(cur, canonical=canonical, dups=dups):
            for d in dups:
                _merge_with_backup(cur, canonical["id"], d["id"])
        if _write(f"{step} {canonical['id']}", writes):
            done += 1
    return done


def _log_plan(plan: list[tuple[dict, list[dict]]]) -> None:
    for canonical, dups in plan[:SAMPLES]:
        log.info("  keep %s %s (urls=%s pubs=%s) <- %s", canonical["id"], _name(canonical),
                 canonical.get("n_urls"), canonical.get("n_pubs"),
                 "; ".join(f"{d['id']} {_name(d)} (urls={d.get('n_urls')} pubs={d.get('n_pubs')})"
                           for d in dups))


def _plan_counts(plan) -> dict:
    dups = [d for _, ds in plan for d in ds]
    return {"clusters": len(plan), "records_merged": len(dups),
            "tracked_records_merged": sum(1 for d in dups if d.get("n_urls")),
            "authorships_moved": sum(int(d.get("n_pubs") or 0) for d in dups),
            "follows_moved": sum(int(d.get("n_follows") or 0) for d in dups)}


# ---------------------------------------------------------------- #249

def strip_repeated_surnames() -> dict:
    """PR #249 data fix: first names ending in the surname ("Peter Hull" +
    "Hull" shows as "Peter Hull Hull"). Renamed in place, or merged into an
    existing record that already has the corrected name. Runs first, so the
    later steps compare corrected names."""
    from backend.database.identity import strip_repeated_surname
    rows = fetch_all(
        """SELECT id, first_name, last_name FROM researchers
           WHERE first_name LIKE CONCAT('% ', last_name)
              OR first_name LIKE CONCAT('% ', REPLACE(last_name, '-', ' '))""")
    renames, merges, conflicts = [], [], []
    # Union rows that share an id or a corrected name into one cluster:
    # "Ana Cozzi" Cozzi x2 + "Ana" Cozzi is one merge, not two overlapping ones.
    parent: dict[int, int] = {}

    def find(i: int) -> int:
        while parent.setdefault(i, i) != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    doubled: dict[int, dict] = {}
    by_name: dict[tuple[str, str], int] = {}
    for r in rows:
        fixed = strip_repeated_surname(r["first_name"], r["last_name"])
        if fixed == r["first_name"]:
            continue
        doubled[r["id"]] = r
        find(r["id"])
        key = (fixed.casefold(), r["last_name"].casefold())
        if key in by_name:
            parent[find(r["id"])] = find(by_name[key])
        else:
            by_name[key] = r["id"]
        for t in fetch_all("SELECT id FROM researchers WHERE first_name = %s AND last_name = %s AND id != %s",
                           (fixed, r["last_name"], r["id"])):
            parent[find(t["id"])] = find(r["id"])
    clusters: dict[int, list[int]] = collections.defaultdict(list)
    for i in list(parent):
        clusters[find(i)].append(i)
    for members in clusters.values():
        if len(members) == 1:
            r = doubled[members[0]]
            renames.append((r, strip_repeated_surname(r["first_name"], r["last_name"])))
            continue
        weights = _weights(members)
        # Different OpenAlex authors behind one name (two Markus Goldsteins):
        # neither merge nor rename; list the group for review.
        ids = {w["openalex_author_id"] for w in weights.values() if w["openalex_author_id"]}
        if len(ids) > 1:
            conflicts.append(sorted(weights.values(), key=lambda w: w["id"]))
            continue
        ranked = sorted(weights.values(), key=canonical_order)
        keep = ranked[0]
        merges.append((keep, ranked[1:], strip_repeated_surname(keep["first_name"], keep["last_name"])))
    renames.sort(key=lambda rf: rf[0]["id"])
    merges.sort(key=lambda m: m[0]["id"])
    for group in conflicts:
        log.info("  review (different OpenAlex IDs, left unchanged): %s",
                 "; ".join(f"{w['id']} {_name(w)} {w['openalex_author_id']}" for w in group))
        REPORT["strip_repeated_surnames_review"].append(
            [{"id": w["id"], "name": _name(w), "openalex_author_id": w["openalex_author_id"]} for w in group])
    for r, fixed in renames[:SAMPLES]:
        log.info("  rename %s: %r -> %r (%s)", r["id"], r["first_name"], fixed, r["last_name"])
    _log_plan([(c, d) for c, d, _ in merges])
    for c, _, fixed in merges[:SAMPLES]:
        if c["first_name"] != fixed:
            log.info("  survivor %s renamed: %r -> %r (%s)", c["id"], c["first_name"], fixed, c["last_name"])
    applied = 0
    for r, fixed in renames:
        REPORT["strip_repeated_surnames"].append({"id": r["id"], "from": r["first_name"], "to": fixed})
        if not APPLY:
            continue

        def writes(cur, r=r, fixed=fixed):
            _group().append(["set", "researchers", "first_name", [[r["id"], r["first_name"], fixed]]])
            cur.execute("UPDATE researchers SET first_name = %s WHERE id = %s AND first_name = %s",
                        (fixed, r["id"], r["first_name"]))
        applied += _write(f"rename {r['id']}", writes)
    merged = _apply_strip_merges(merges)
    return {"renamed": len(renames), "merged_into_existing": sum(len(d) for _, d, _ in merges),
            "survivors_renamed": sum(1 for c, _, fixed in merges if c["first_name"] != fixed),
            "left_for_review_conflicting_openalex_ids": len(conflicts),
            "applied_renames": applied, "applied_merges": merged if APPLY else 0}


def _apply_strip_merges(merges: list[tuple[dict, list[dict], str]]) -> int:
    """Merge each group into its survivor, which ends with the corrected first
    name even when the survivor is the record stored with the repeated surname
    ("Michèle Belot" + "Belot" keeps its URLs but becomes "Michèle")."""
    done = 0
    for canonical, dups, fixed in merges:
        entry = {"keep": canonical["id"], "name": _name(canonical),
                 "merge": [{"id": d["id"], "name": _name(d)} for d in dups]}
        if canonical["first_name"] != fixed:
            entry["rename_to"] = fixed
        REPORT["strip_repeated_surnames"].append(entry)

        def writes(cur, canonical=canonical, dups=dups, fixed=fixed):
            # Rename first (undone last), so the merge compares corrected names.
            cur.execute("SELECT first_name FROM researchers WHERE id = %s", (canonical["id"],))
            row = cur.fetchone()
            if not row or row["first_name"] != canonical["first_name"]:
                raise RuntimeError(f"researcher {canonical['id']} changed since the plan "
                                   f"({canonical['first_name']!r} -> {row and row['first_name']!r})")
            if canonical["first_name"] != fixed:
                _group().append(["set", "researchers", "first_name",
                                 [[canonical["id"], canonical["first_name"], fixed]]])
                cur.execute("UPDATE researchers SET first_name = %s WHERE id = %s AND first_name = %s",
                            (fixed, canonical["id"], canonical["first_name"]))
            for d in dups:
                _merge_with_backup(cur, canonical["id"], d["id"])
        if _write(f"strip_repeated_surnames {canonical['id']}", writes):
            done += 1
    return done


# ---------------------------------------------------------------- #259

def _openalex_groups() -> dict[str, list[dict]]:
    rows = fetch_all(
        """SELECT r.id, r.first_name, r.last_name, r.openalex_author_id,
             (SELECT COUNT(*) FROM researcher_urls u WHERE u.researcher_id = r.id) AS n_urls,
             (SELECT COUNT(*) FROM user_follows f WHERE f.researcher_id = r.id) AS n_follows,
             (SELECT COUNT(*) FROM authorship a WHERE a.researcher_id = r.id) AS n_pubs
           FROM researchers r
           JOIN (SELECT openalex_author_id FROM researchers WHERE openalex_author_id IS NOT NULL
                 GROUP BY openalex_author_id HAVING COUNT(*) > 1) g
             ON g.openalex_author_id = r.openalex_author_id""")
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        groups[r["openalex_author_id"]].append(r)
    return groups


def merge_openalex_twins() -> dict:
    groups = _openalex_groups()
    clusters = [c for recs in groups.values() for c in cluster_same_person(recs, strong=True)]
    plan = _merge_plan(clusters)
    log.info("  %d shared OpenAlex IDs on %d records", len(groups), sum(map(len, groups.values())))
    _log_plan(plan)
    counts = {"shared_ids": len(groups), **_plan_counts(plan)}
    applied = _apply_merges("merge_openalex_twins", plan)
    counts["applied"] = applied if APPLY else 0
    return counts


def _unit_matches(unit: list[dict], display_names: set[str]) -> tuple[bool, bool]:
    """(any record matches an OpenAlex display name, one with a full first name does)."""
    any_match = full_match = False
    for r in unit:
        for d in display_names:
            if matches_display_name(r["first_name"], r["last_name"], d):
                any_match = True
                given = split_name(r["first_name"], r["last_name"])[0]
                if given and len(given[0]) > 1:
                    full_match = True
    return any_match, full_match


def clear_conflicting_openalex_ids() -> dict:
    groups = _openalex_groups()
    names: dict[str, set[str]] = collections.defaultdict(set)
    for chunk in _chunks(list(groups)):
        ph = ",".join(["%s"] * len(chunk))
        for r in fetch_all(f"SELECT DISTINCT openalex_author_id, display_name FROM openalex_coauthors "
                           f"WHERE openalex_author_id IN ({ph})", tuple(chunk)):
            names[r["openalex_author_id"]].add(r["display_name"])
    clears: list[tuple[str, list[dict]]] = []
    ambiguous = 0
    for oa, recs in groups.items():
        clusters = cluster_same_person(recs, strong=True)
        in_cluster = {r["id"] for c in clusters for r in c}
        units = clusters + [[r] for r in recs if r["id"] not in in_cluster]
        if len(units) < 2:
            continue  # one person after merge_openalex_twins
        verdicts = [_unit_matches(u, names[oa]) for u in units]
        keep = [i for i, (m, _) in enumerate(verdicts) if m]
        if len(keep) > 1:
            keep = [i for i, (_, full) in enumerate(verdicts) if full]
        if len(keep) != 1:
            ambiguous += 1
            continue
        losers = [r for i, u in enumerate(units) if i != keep[0] for r in u]
        clears.append((oa, losers))
        REPORT["clear_conflicting_openalex_ids"].append(
            {"openalex_author_id": oa, "keep": [_name(r) for r in units[keep[0]]],
             "clear": [{"id": r["id"], "name": _name(r)} for r in losers],
             "openalex_names": sorted(names[oa])})
    for oa, losers in clears[:SAMPLES]:
        log.info("  %s %s: clear from %s", oa, sorted(names[oa])[:2],
                 "; ".join(f"{r['id']} {_name(r)}" for r in losers))
    applied = 0
    for oa, losers in clears if APPLY else []:
        ids = [r["id"] for r in losers]

        def writes(cur, oa=oa, ids=ids):
            _group().append(["set", "researchers", "openalex_author_id", [[i, oa, None] for i in ids]])
            cur.execute(f"UPDATE researchers SET openalex_author_id = NULL "
                        f"WHERE openalex_author_id = %s AND id IN ({','.join(['%s'] * len(ids))})",
                        (oa, *ids))
        applied += _write(f"clear {oa}", writes)
    cleared = [r for _, losers in clears for r in losers]
    return {"ids_resolved": len(clears), "records_cleared": len(cleared),
            "tracked_records_cleared": sum(1 for r in cleared if r["n_urls"]),
            "ids_left_ambiguous": ambiguous, "applied": applied}


# ---------------------------------------------------------------- #260

def merge_shared_url_twins() -> dict:
    rows = fetch_all(
        """SELECT DISTINCT ru.url, r.id, r.first_name, r.last_name
           FROM researcher_urls ru JOIN researchers r ON r.id = ru.researcher_id
           JOIN (SELECT url FROM researcher_urls GROUP BY url
                 HAVING COUNT(DISTINCT researcher_id) > 1) s ON s.url = ru.url""")
    by_url: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_url[r["url"]].append(r)
    parent: dict[int, int] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x
    for recs in by_url.values():
        for cluster in cluster_same_person(recs, strong=True):
            for r in cluster[1:]:
                parent[find(r["id"])] = find(cluster[0]["id"])
    members: dict[int, list[int]] = collections.defaultdict(list)
    for rid in list(parent):
        members[find(rid)].append(rid)
    weights = _weights([rid for ids in members.values() for rid in ids])
    clusters = []
    for ids in members.values():
        recs = [weights[i] for i in ids if i in weights]
        if len(recs) > 1 and all(same_person(a["first_name"], a["last_name"], b["first_name"],
                                             b["last_name"], strong=True)
                                 for a in recs for b in recs):
            clusters.append(recs)
    plan = _merge_plan(clusters)
    _log_plan(plan)
    counts = {"shared_urls": len(by_url), **_plan_counts(plan)}
    applied = _apply_merges("merge_shared_url_twins", plan)
    counts["applied"] = applied if APPLY else 0
    return counts


def route_page_papers_to_owner() -> dict:
    """Owner missing from a paper on their page, or only attached at position 0,
    while a byline author naming them is a different record."""
    pairs = fetch_all(
        """SELECT DISTINCT ru.researcher_id AS owner_id, pu.paper_id, ao.author_order AS owner_order
           FROM paper_urls pu
           JOIN researcher_urls ru ON ru.url = pu.url
           LEFT JOIN authorship ao ON ao.publication_id = pu.paper_id
                                  AND ao.researcher_id = ru.researcher_id
           WHERE ao.id IS NULL OR COALESCE(ao.author_order, 0) = 0""")
    paper_ids = sorted({p["paper_id"] for p in pairs})
    authors: dict[int, list[dict]] = collections.defaultdict(list)
    for chunk in _chunks(paper_ids):
        ph = ",".join(["%s"] * len(chunk))
        for a in fetch_all(
            f"""SELECT a.publication_id, a.researcher_id, a.author_order,
                       r.first_name, r.last_name, r.openalex_author_id,
                       EXISTS(SELECT 1 FROM researcher_urls u WHERE u.researcher_id = r.id) AS tracked
                FROM authorship a JOIN researchers r ON r.id = a.researcher_id
                WHERE a.publication_id IN ({ph})""", tuple(chunk)):
            authors[a["publication_id"]].append(a)
    owners = _weights([p["owner_id"] for p in pairs])

    moves: dict[int, list[tuple[int, dict, object]]] = collections.defaultdict(list)  # owner -> moves
    twin_pairs: set[tuple[int, int]] = set()
    skipped = collections.Counter()
    for p in pairs:
        owner = owners.get(p["owner_id"])
        if not owner:
            continue
        cands = []
        for a in authors.get(p["paper_id"], []):
            if a["researcher_id"] == owner["id"] or not a["author_order"]:
                continue
            if same_person(owner["first_name"], owner["last_name"], a["first_name"], a["last_name"],
                           strong=True):
                cands.append((match_score(owner["first_name"], owner["last_name"],
                                          a["first_name"], a["last_name"]), a))
        if not cands:
            continue
        cands.sort(key=lambda c: -c[0])
        if len(cands) > 1 and cands[0][0] == cands[1][0]:
            skipped["ambiguous_byline"] += 1
            continue
        score, a = cands[0]
        if (a["openalex_author_id"] and owner["openalex_author_id"]
                and a["openalex_author_id"] != owner["openalex_author_id"]):
            skipped["conflicting_openalex_id"] += 1
            continue
        if a["tracked"]:
            if score == 3:
                twin_pairs.add(tuple(sorted((owner["id"], a["researcher_id"]))))
            else:
                skipped["other_tracked_researcher"] += 1
            continue
        moves[owner["id"]].append((p["paper_id"], a, p["owner_order"]))

    # A stub whose every paper moves to one owner is that owner: merge it so
    # its follows come along and the next byline cannot reach it.
    stub_targets: dict[int, set[int]] = collections.defaultdict(set)
    stub_moves = collections.Counter()
    for owner_id, ms in moves.items():
        for _, a, _ in ms:
            stub_targets[a["researcher_id"]].add(owner_id)
            stub_moves[a["researcher_id"]] += 1
    stub_info = _weights(list(stub_targets))
    emptied = {sid for sid, targets in stub_targets.items()
               if len(targets) == 1 and sid in stub_info
               and stub_moves[sid] >= int(stub_info[sid]["n_pubs"])}

    twin_weights = _weights([i for pair in twin_pairs for i in pair])
    twin_plan = _merge_plan([[twin_weights[a], twin_weights[b]] for a, b in twin_pairs
                             if a in twin_weights and b in twin_weights])

    n_moves = sum(len(ms) for ms in moves.values())
    log.info("  %d (owner, paper) pairs checked; %d authorships move to %d owners; "
             "%d stubs merged; %d tracked twins merged; skipped %s",
             len(pairs), n_moves, len(moves), len(emptied), len(twin_plan), dict(skipped))
    for owner_id, ms in list(moves.items())[:SAMPLES]:
        stubs = collections.Counter((a["researcher_id"], _name(a)) for _, a, _ in ms)
        log.info("  owner %s %s <- %s", owner_id, _name(owners[owner_id]),
                 "; ".join(f"{sid} {nm} x{n}{' (merge)' if sid in emptied else ''}"
                           for (sid, nm), n in stubs.items()))
    _log_plan(twin_plan)

    applied = 0
    for owner_id, ms in moves.items():
        REPORT["route_page_papers_to_owner"].append(
            {"owner": owner_id, "moves": [{"paper": pid, "from": a["researcher_id"]} for pid, a, _ in ms]})

        def writes(cur, owner_id=owner_id, ms=ms):
            touched = {owner_id} | {a["researcher_id"] for _, a, _ in ms}
            pids = {pid for pid, _, _ in ms}
            _group().append(["rows", "authorship", ["id"],
                             [r for r in _select(cur, "authorship", "publication_id", pids)
                              if r["researcher_id"] in touched]])
            for pid, a, owner_order in ms:
                if owner_order is None:
                    cur.execute("UPDATE IGNORE authorship SET researcher_id = %s "
                                "WHERE researcher_id = %s AND publication_id = %s",
                                (owner_id, a["researcher_id"], pid))
                else:
                    cur.execute("UPDATE authorship SET author_order = %s "
                                "WHERE researcher_id = %s AND publication_id = %s",
                                (a["author_order"], owner_id, pid))
                cur.execute("DELETE FROM authorship WHERE researcher_id = %s AND publication_id = %s",
                            (a["researcher_id"], pid))
            for sid in {a["researcher_id"] for _, a, _ in ms} & emptied:
                _merge_with_backup(cur, owner_id, sid)
        if APPLY and _write(f"route to owner {owner_id}", writes):
            applied += 1
            refresh_has_top5_for([owner_id])
    twins_applied = _apply_merges("route_page_papers_to_owner_twins", twin_plan)
    twins_applied = twins_applied if APPLY else 0
    return {"pairs_checked": len(pairs), "authorships_moved": n_moves, "owners": len(moves),
            "papers": len({pid for ms in moves.values() for pid, _, _ in ms}),
            "stubs_merged": len(emptied), "tracked_twins_merged": len(twin_plan),
            **{f"skipped_{k}": v for k, v in skipped.items()},
            "applied_owners": applied, "applied_twins": twins_applied}


# ---------------------------------------------------------------- #262

def _shared_pages() -> dict[str, str]:
    """Active URLs tracked for researchers with unrelated names."""
    shared = fetch_all(
        """SELECT DISTINCT ru.url, r.id, r.first_name, r.last_name
           FROM researcher_urls ru JOIN researchers r ON r.id = ru.researcher_id
           JOIN (SELECT url FROM researcher_urls GROUP BY url
                 HAVING COUNT(DISTINCT researcher_id) > 1 AND SUM(is_active) > 0) s ON s.url = ru.url""")
    by_url: dict[str, list[dict]] = collections.defaultdict(list)
    for r in shared:
        by_url[r["url"]].append(r)
    return {url: SHARED_PAGE for url, owners in by_url.items()
            if page_ownership_problem(owners, None) == SHARED_PAGE}


def _unnamed_pages(exclude: set[str]) -> dict[str, dict]:
    """Active pages that never name their owner: {url: {owner, page_type}}."""
    flagged: dict[str, dict] = {}
    # SQL prefilter (accent/case-insensitive collation): pages whose text and
    # HTML never contain the stored surname; the Python check then decides.
    candidates = fetch_all(
        """SELECT ru.id AS url_id, ru.url, ru.page_type, r.id AS researcher_id, r.first_name, r.last_name
           FROM researcher_urls ru
           JOIN researchers r ON r.id = ru.researcher_id
           JOIN html_content hc ON hc.url_id = ru.id
           WHERE ru.is_active = TRUE
             AND (hc.content IS NOT NULL OR hc.raw_html IS NOT NULL)
             AND COALESCE(hc.content, '') NOT LIKE CONCAT('%', r.last_name, '%')
             AND COALESCE(hc.raw_html, '') NOT LIKE CONCAT('%', r.last_name, '%')""")
    for chunk in _chunks(candidates, 200):
        ph = ",".join(["%s"] * len(chunk))
        pages = {p["url_id"]: p for p in fetch_all(
            f"SELECT url_id, content, raw_html FROM html_content WHERE url_id IN ({ph})",
            tuple(c["url_id"] for c in chunk))}
        for c in chunk:
            page = pages.get(c["url_id"]) or {}
            owner = [{"first_name": c["first_name"], "last_name": c["last_name"]}]
            if c["url"] not in exclude and page_ownership_problem(
                    owner, page.get("content"), page.get("raw_html"), c["url"], c["page_type"]) == OWNER_NOT_NAMED:
                flagged[c["url"]] = {"researcher_id": c["researcher_id"], "name": _name(c),
                                     "page_type": c["page_type"]}
    return flagged


def flag_unowned_pages() -> dict:
    """#262 review list: pages that never name their owner. Nothing is deleted
    or deactivated; --apply sets researcher_urls.ownership_flag, which lists
    them in the admin Health tab and stops position-0 owner credit from them."""
    pages = _unnamed_pages(set(_shared_pages()))
    for url, info in list(pages.items())[:SAMPLES]:
        log.info("  review: %s (%s, %s)", url, info["name"], info["page_type"])
    REPORT["flag_unowned_pages"] = [{"url": u, **info} for u, info in sorted(pages.items())]
    applied = 0
    if APPLY:
        for chunk in _chunks(sorted(pages), 200):
            def writes(cur, chunk=chunk):
                now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
                rows = _select(cur, "researcher_urls", "url", chunk, "id, ownership_flag, ownership_flagged_at")
                todo = [r for r in rows if r["ownership_flag"] != OWNER_NOT_NAMED]
                _group().append(["set", "researcher_urls", "ownership_flag",
                                 [[r["id"], r["ownership_flag"], OWNER_NOT_NAMED] for r in todo]])
                _group().append(["set", "researcher_urls", "ownership_flagged_at",
                                 [[r["id"], r["ownership_flagged_at"], now] for r in todo]])
                for r in todo:
                    cur.execute("UPDATE researcher_urls SET ownership_flag = %s, ownership_flagged_at = %s "
                                "WHERE id = %s", (OWNER_NOT_NAMED, now, r["id"]))
            applied += _write("flag unowned pages", writes)
    return {"urls_flagged_for_review": len(pages), "applied_chunks": applied}


def deactivate_shared_pages() -> dict:
    """#262: pages tracked for unrelated people stop being crawled; owner
    attachments (position 0) that came only from them are removed, and items
    left with no author that came only from them are deleted. Those items are
    other people's papers or news scraped from institutional list pages; no
    tracked author remains on them."""
    flagged = _shared_pages()
    urls = sorted(flagged)
    detached: list[tuple[int, int]] = []   # (researcher, paper) owner attachments to remove
    doomed: set[int] = set()
    events = 0
    if urls:
        url_owner = collections.defaultdict(set)
        for chunk in _chunks(urls, 500):
            ph = ",".join(["%s"] * len(chunk))
            for r in fetch_all(f"SELECT url, researcher_id FROM researcher_urls WHERE url IN ({ph})",
                               tuple(chunk)):
                url_owner[r["url"]].add(r["researcher_id"])
        flagged_set = set(urls)
        papers: dict[int, set[str]] = collections.defaultdict(set)
        for chunk in _chunks(urls, 500):
            ph = ",".join(["%s"] * len(chunk))
            for r in fetch_all(f"SELECT paper_id, url FROM paper_urls WHERE url IN ({ph})", tuple(chunk)):
                papers[r["paper_id"]].add(r["url"])
        pids = sorted(papers)
        all_urls: dict[int, set[str]] = collections.defaultdict(set)
        auth: dict[int, list[dict]] = collections.defaultdict(list)
        for chunk in _chunks(pids):
            ph = ",".join(["%s"] * len(chunk))
            for r in fetch_all(f"SELECT paper_id, url FROM paper_urls WHERE paper_id IN ({ph})", tuple(chunk)):
                all_urls[r["paper_id"]].add(r["url"])
            for r in fetch_all(f"SELECT publication_id, researcher_id, author_order FROM authorship "
                               f"WHERE publication_id IN ({ph})", tuple(chunk)):
                auth[r["publication_id"]].append(r)
        other_urls = collections.defaultdict(set)  # researcher -> their unflagged URLs
        owner_ids = sorted({rid for ids in url_owner.values() for rid in ids})
        for chunk in _chunks(owner_ids):
            ph = ",".join(["%s"] * len(chunk))
            for r in fetch_all(f"SELECT researcher_id, url FROM researcher_urls WHERE researcher_id IN ({ph})",
                               tuple(chunk)):
                if r["url"] not in flagged_set:
                    other_urls[r["researcher_id"]].add(r["url"])
        for pid in pids:
            via = {rid for u in papers[pid] for rid in url_owner[u]}
            remaining = []
            for a in auth[pid]:
                if (a["researcher_id"] in via and not a["author_order"]
                        and not (all_urls[pid] & other_urls[a["researcher_id"]])):
                    detached.append((a["researcher_id"], pid))
                else:
                    remaining.append(a)
            if not remaining and all_urls[pid] <= flagged_set:
                doomed.add(pid)
        for chunk in _chunks(sorted(doomed)):
            ph = ",".join(["%s"] * len(chunk))
            events += fetch_all(f"SELECT COUNT(*) AS n FROM feed_events WHERE paper_id IN ({ph})",
                                tuple(chunk))[0]["n"]

    for url in urls[:SAMPLES]:
        log.info("  %s: %s", flagged[url], url)
    REPORT["deactivate_shared_pages"] = [{"url": u, "reason": flagged[u]} for u in urls]
    REPORT["deactivate_shared_pages_papers_deleted"] = sorted(doomed)

    applied = 0
    if APPLY:
        for url in urls:
            def writes(cur, url=url):
                now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
                before = _select(cur, "researcher_urls", "url", [url])
                _group().append(["set", "researcher_urls", "is_active",
                                 [[r["id"], r["is_active"], 0] for r in before if r["is_active"]]])
                _group().append(["set", "researcher_urls", "deactivation_reason",
                                 [[r["id"], r["deactivation_reason"], flagged[url]] for r in before if r["is_active"]]])
                _group().append(["set", "researcher_urls", "deactivated_at",
                                 [[r["id"], r["deactivated_at"], now] for r in before if r["is_active"]]])
                cur.execute("""UPDATE researcher_urls SET is_active = FALSE, deactivated_at = %s,
                                 deactivation_reason = %s WHERE url = %s AND is_active = TRUE""",
                            (now, flagged[url], url))
            applied += _write(f"deactivate {url}", writes)
        for chunk in _chunks(detached, 500):
            def writes(cur, chunk=chunk):
                pairs = set(chunk)
                _group().append(["insert", "authorship",
                                 [r for r in _select(cur, "authorship", "publication_id", {p for _, p in chunk})
                                  if (r["researcher_id"], r["publication_id"]) in pairs
                                  and not r["author_order"]]])
                for rid, pid in chunk:
                    cur.execute("DELETE FROM authorship WHERE researcher_id = %s AND publication_id = %s "
                                "AND COALESCE(author_order, 0) = 0", (rid, pid))
            _write("detach owners", writes)
        for chunk in _chunks(sorted(doomed), 200):
            def writes(cur, chunk=chunk):
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(f"SELECT id FROM papers WHERE id IN ({ph}) AND NOT EXISTS "
                            f"(SELECT 1 FROM authorship a WHERE a.publication_id = papers.id)", tuple(chunk))
                ids = [r["id"] for r in cur.fetchall()]
                if not ids:
                    return
                _capture_tree(cur, "papers", "id", ids, _group())
                cur.execute(f"DELETE FROM papers WHERE id IN ({','.join(['%s'] * len(ids))})", tuple(ids))
            _write("delete unowned items", writes)
        for rid in {rid for rid, _ in detached}:
            refresh_has_top5_for([rid])
    return {"urls_deactivated": len(urls),
            "owner_attachments_removed": len(detached),
            "papers_deleted": len(doomed), "feed_events_deleted": events, "applied_urls": applied}


STEP_ORDER = [
    strip_repeated_surnames,
    merge_openalex_twins,
    clear_conflicting_openalex_ids,
    merge_shared_url_twins,
    route_page_papers_to_owner,
    deactivate_shared_pages,
    flag_unowned_pages,
]
STEPS = {fn.__name__: fn for fn in STEP_ORDER}


def main(argv=None) -> dict:
    global APPLY
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--steps", help=f"comma-separated subset of: {', '.join(STEPS)}")
    parser.add_argument("--report", help="write every planned action to this JSON file")
    parser.add_argument("--backup", help="with --apply: append the rows each change replaces to this "
                                         "JSON-lines file (required)")
    parser.add_argument("--undo", metavar="BACKUP", help="restore a --backup file, newest change first "
                                                         "(dry run unless --apply; narrow with --steps/--units)")
    parser.add_argument("--units", help="with --undo: unit numbers to restore, e.g. 3,5-9")
    args = parser.parse_args(argv)
    APPLY = args.apply
    global BACKUP, CURRENT_STEP
    if args.undo:
        log.info("=== repair_researcher_identity --undo %s (%s) ===", args.undo, "APPLY" if APPLY else "DRY RUN")
        steps = {x.strip() for x in args.steps.split(",") if x.strip()} if args.steps else None
        counts = undo(args.undo, steps, _parse_units(args.units))
        log.info("  %s", counts)
        return {"undo": counts}
    if APPLY and not args.backup:
        parser.error("--apply requires --backup PATH (restore later with --undo PATH --apply)")
    BACKUP = Backup(args.backup) if APPLY else None

    selected = STEP_ORDER
    if args.steps:
        requested = [s.strip() for s in args.steps.split(",") if s.strip()]
        unknown = [s for s in requested if s not in STEPS]
        if unknown:
            parser.error(f"unknown steps: {unknown}")
        selected = [fn for fn in STEP_ORDER if fn.__name__ in requested]

    mode = "APPLY" if APPLY else "DRY RUN"
    log.info("=== repair_researcher_identity (%s) ===", mode)
    summary = {}
    for fn in selected:
        log.info("\n--- %s ---", fn.__name__)
        started = time.time()
        CURRENT_STEP = fn.__name__
        summary[fn.__name__] = fn()
        log.info("  %s  (%.0fs)", summary[fn.__name__], time.time() - started)
    log.info("\n=== summary (%s) ===", mode)
    for name, counts in summary.items():
        log.info("  %-32s %s", name, counts)
    if args.report:
        Path(args.report).write_text(json.dumps({"mode": mode, "summary": summary, "actions": REPORT},
                                                indent=1, default=str))
    return summary


if __name__ == "__main__":
    main()
