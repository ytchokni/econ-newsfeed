"""Repair split researcher identities and misattributed page papers.

Dry run by default: prints what each step would change and its counts.
Pass --apply to write. Steps run in this order (later steps see earlier merges):

  merge_openalex_twins            #259 records sharing an OpenAlex author ID whose
                                  names agree ("Andy"/"Andrew" Garin) become one
  clear_conflicting_openalex_ids  #259 an ID still on several people stays with the
                                  one OpenAlex names ("Marcel", not "Quinn" Fafchamps)
  merge_shared_url_twins          #260 records tracking the same URL whose names
                                  agree (Gordon B. Dahl 24 / 17140) become one
  route_page_papers_to_owner      #260 byline authors on a page that name its owner
                                  move to the owner ("Dora Costa" stub -> Dora L. Costa)
  deactivate_unowned_pages        #262 pages tracked for unrelated people, or that
                                  never name their owner, stop being crawled; owner
                                  attachments that came only from them are removed

    poetry run python scripts/repair_researcher_identity.py                  # dry run
    poetry run python scripts/repair_researcher_identity.py --apply
    poetry run python scripts/repair_researcher_identity.py --steps merge_openalex_twins --apply
    poetry run python scripts/repair_researcher_identity.py --report plan.json   # every action

Each merge or move is its own short transaction, so the script can run while
the API and workers are up. Researcher merges never touch feed_events: events
belong to papers. Only deactivate_unowned_pages deletes papers (and with them
their events): items with no remaining author that came only from unowned pages.
Safe to re-run: every step selects the current violations.
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
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
from backend.database.researchers import refresh_has_top5  # noqa: E402

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


def _write(label: str, fn) -> bool:
    """Run fn(cursor) in its own transaction (retrying deadlocks) when --apply."""
    if not APPLY:
        return True
    for attempt in range(3):
        with get_connection() as conn:
            cursor = conn.cursor(dictionary=True, buffered=True)
            try:
                fn(cursor)
                conn.commit()
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
                merge_researcher_into(cur, canonical["id"], d["id"])
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
                merge_researcher_into(cur, owner_id, sid)
        if APPLY and _write(f"route to owner {owner_id}", writes):
            applied += 1
            refresh_has_top5(owner_id)
    twins_applied = _apply_merges("route_page_papers_to_owner_twins", twin_plan)
    twins_applied = twins_applied if APPLY else 0
    return {"pairs_checked": len(pairs), "authorships_moved": n_moves, "owners": len(moves),
            "papers": len({pid for ms in moves.values() for pid, _, _ in ms}),
            "stubs_merged": len(emptied), "tracked_twins_merged": len(twin_plan),
            **{f"skipped_{k}": v for k, v in skipped.items()},
            "applied_owners": applied, "applied_twins": twins_applied}


# ---------------------------------------------------------------- #262

def deactivate_unowned_pages() -> dict:
    flagged: dict[str, str] = {}

    shared = fetch_all(
        """SELECT DISTINCT ru.url, r.id, r.first_name, r.last_name
           FROM researcher_urls ru JOIN researchers r ON r.id = ru.researcher_id
           JOIN (SELECT url FROM researcher_urls GROUP BY url
                 HAVING COUNT(DISTINCT researcher_id) > 1 AND SUM(is_active) > 0) s ON s.url = ru.url""")
    by_url: dict[str, list[dict]] = collections.defaultdict(list)
    for r in shared:
        by_url[r["url"]].append(r)
    for url, owners in by_url.items():
        if any(not same_person(a["first_name"], a["last_name"], b["first_name"], b["last_name"], strong=True)
               for i, a in enumerate(owners) for b in owners[i + 1:]):
            flagged[url] = SHARED_PAGE

    # SQL prefilter (accent/case-insensitive collation): pages whose text and
    # HTML never contain the stored surname; the Python check then decides.
    candidates = fetch_all(
        """SELECT ru.id AS url_id, ru.url, r.first_name, r.last_name
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
            if c["url"] not in flagged and page_ownership_problem(
                    owner, page.get("content"), page.get("raw_html"), c["url"]) == OWNER_NOT_NAMED:
                flagged[c["url"]] = OWNER_NOT_NAMED

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

    reasons = collections.Counter(flagged.values())
    for url in urls[:SAMPLES]:
        log.info("  %s: %s", flagged[url], url)
    REPORT["deactivate_unowned_pages"] = [{"url": u, "reason": flagged[u]} for u in urls]
    REPORT["deactivate_unowned_pages_papers_deleted"] = sorted(doomed)

    applied = 0
    if APPLY:
        for url in urls:
            def writes(cur, url=url):
                cur.execute("""UPDATE researcher_urls SET is_active = FALSE, deactivated_at = NOW(),
                                 deactivation_reason = %s WHERE url = %s AND is_active = TRUE""",
                            (flagged[url], url))
            applied += _write(f"deactivate {url}", writes)
        for chunk in _chunks(detached, 500):
            def writes(cur, chunk=chunk):
                for rid, pid in chunk:
                    cur.execute("DELETE FROM authorship WHERE researcher_id = %s AND publication_id = %s "
                                "AND COALESCE(author_order, 0) = 0", (rid, pid))
            _write("detach owners", writes)
        for chunk in _chunks(sorted(doomed), 200):
            def writes(cur, chunk=chunk):
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(f"DELETE FROM papers WHERE id IN ({ph}) AND NOT EXISTS "
                            f"(SELECT 1 FROM authorship a WHERE a.publication_id = papers.id)", tuple(chunk))
            _write("delete unowned items", writes)
        for rid in {rid for rid, _ in detached}:
            refresh_has_top5(rid)
    return {"urls_flagged": len(urls), "shared_page": reasons[SHARED_PAGE],
            "owner_not_named": reasons[OWNER_NOT_NAMED],
            "owner_attachments_removed": len(detached),
            "papers_deleted": len(doomed), "feed_events_deleted": events, "applied_urls": applied}


STEP_ORDER = [
    merge_openalex_twins,
    clear_conflicting_openalex_ids,
    merge_shared_url_twins,
    route_page_papers_to_owner,
    deactivate_unowned_pages,
]
STEPS = {fn.__name__: fn for fn in STEP_ORDER}


def main(argv=None) -> dict:
    global APPLY
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--steps", help=f"comma-separated subset of: {', '.join(STEPS)}")
    parser.add_argument("--report", help="write every planned action to this JSON file")
    args = parser.parse_args(argv)
    APPLY = args.apply

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
