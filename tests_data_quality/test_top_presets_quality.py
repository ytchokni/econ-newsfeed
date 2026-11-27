""""Has Top-5" and "Top-20" presets against the real database (audit bugs 11, 12).

* The MySQL filters and the Python helpers in backend/database/top_presets.py
  must classify every stored venue and affiliation the same way.
* researchers.has_top5_pub must follow the five-journal rule. A failure lists
  stale flags; run scripts/recompute_has_top5.py (dry run, then --apply).
* The feed's "Has Top-5" preset must stay fast (it took 3-10 s when it joined
  authorship to authorship with substring matches).
"""
import time

from conftest import fmt_violations

from backend.database import top_presets as tp


def test_sql_and_python_agree_on_every_venue(db):
    clause, params = tp.top5_venue_clause("venue")
    rows = db.fetch_all(
        f"SELECT venue, {clause} AS sql_match FROM papers WHERE venue IS NOT NULL GROUP BY venue",
        tuple(params),
    )
    violators = [
        {"venue": r["venue"], "sql": bool(r["sql_match"])}
        for r in rows if bool(r["sql_match"]) != tp.is_top5_venue(r["venue"])
    ]
    assert not violators, "top-5 venue SQL and Python disagree:\n" + fmt_violations(violators)


def test_sql_and_python_agree_on_every_affiliation(db):
    clause, params = tp.top20_affiliation_clause("affiliation")
    rows = db.fetch_all(
        f"SELECT affiliation, {clause} AS sql_match FROM researchers "
        "WHERE affiliation IS NOT NULL GROUP BY affiliation",
        tuple(params),
    )
    violators = [
        {"affiliation": r["affiliation"], "sql": bool(r["sql_match"])}
        for r in rows if bool(r["sql_match"]) != tp.is_top20_affiliation(r["affiliation"])
    ]
    assert not violators, "top-20 SQL and Python disagree:\n" + fmt_violations(violators)


def test_has_top5_flag_follows_the_five_journal_rule(db):
    from backend.database.researchers import has_top5_flag_changes

    changes = has_top5_flag_changes()
    assert changes["cleared"] == 0 and changes["newly_set"] == 0, (
        f"stale researchers.has_top5_pub: {changes}. "
        "Run scripts/recompute_has_top5.py --apply."
    )


def test_feed_has_top5_preset_is_fast(db):
    from backend.database.connection import connection_scope
    from backend.database.papers import search_feed_events

    with connection_scope():
        search_feed_events(preset="has_top5", limit=20)  # warm the buffer pool
        start = time.perf_counter()
        search_feed_events(preset="has_top5", limit=20)
        elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"feed has_top5 preset took {elapsed:.2f}s"
