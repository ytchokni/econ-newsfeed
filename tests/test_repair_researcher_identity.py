"""Dry-run planning of scripts/repair_researcher_identity.py (no database)."""
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "repair_researcher_identity.py"
_spec = importlib.util.spec_from_file_location("repair_researcher_identity", _SCRIPT)
repair = importlib.util.module_from_spec(_spec)
sys.modules["repair_researcher_identity"] = repair
_spec.loader.exec_module(repair)


@pytest.fixture(autouse=True)
def dry_run(monkeypatch):
    monkeypatch.setattr(repair, "APPLY", False)
    repair.REPORT.clear()
    yield


def _fake_fetch(monkeypatch, routes):
    """routes: list of (substring, rows-or-callable); first match wins."""
    def fetch_all(sql, params=None):
        for needle, rows in routes:
            if needle in sql:
                return rows(sql, params) if callable(rows) else rows
        raise AssertionError(f"unexpected query: {sql[:120]}")
    monkeypatch.setattr(repair, "fetch_all", fetch_all)


def _rec(rid, first, last, oa="A1", urls=0, pubs=1, follows=0):
    return {"id": rid, "first_name": first, "last_name": last, "openalex_author_id": oa,
            "n_urls": urls, "n_pubs": pubs, "n_follows": follows}


GARIN_GROUP = [
    _rec(9583, "Andrew", "Garin", "A5", urls=2, pubs=22),
    _rec(42608, "Andy", "Garin", "A5", pubs=16, follows=1),
    _rec(7869, "Marcel", "Fafchamps", "A6", urls=7, pubs=128),
    _rec(29938, "Quinn", "Fafchamps", "A6"),
]


def test_merge_openalex_twins_plans_only_agreeing_names(monkeypatch):
    _fake_fetch(monkeypatch, [("GROUP BY openalex_author_id HAVING", GARIN_GROUP)])
    counts = repair.merge_openalex_twins()
    assert counts["clusters"] == 1 and counts["records_merged"] == 1
    assert counts["authorships_moved"] == 16 and counts["follows_moved"] == 1
    assert counts["applied"] == 0
    assert repair.REPORT["merge_openalex_twins"] == [
        {"keep": 9583, "name": "Andrew Garin", "merge": [{"id": 42608, "name": "Andy Garin"}]}]


def test_clear_conflicting_ids_keeps_the_person_openalex_names(monkeypatch):
    _fake_fetch(monkeypatch, [
        ("GROUP BY openalex_author_id HAVING", GARIN_GROUP),
        ("FROM openalex_coauthors", [{"openalex_author_id": "A6", "display_name": "Marcel Fafchamps"},
                                     {"openalex_author_id": "A5", "display_name": "Andrew Garin"}]),
    ])
    counts = repair.clear_conflicting_openalex_ids()
    assert counts["ids_resolved"] == 1 and counts["records_cleared"] == 1
    assert repair.REPORT["clear_conflicting_openalex_ids"][0]["clear"] == [
        {"id": 29938, "name": "Quinn Fafchamps"}]


def test_clear_conflicting_ids_prefers_full_name_over_initials(monkeypatch):
    group = [_rec(1, "David", "Powell", "A7"), _rec(2, "Daniel", "Powell", "A7"), _rec(3, "D.", "Powell", "A7")]
    _fake_fetch(monkeypatch, [
        ("GROUP BY openalex_author_id HAVING", group),
        ("FROM openalex_coauthors", [{"openalex_author_id": "A7", "display_name": "Daniel Powell"}]),
    ])
    repair.clear_conflicting_openalex_ids()
    cleared = {r["id"] for r in repair.REPORT["clear_conflicting_openalex_ids"][0]["clear"]}
    assert cleared == {1, 3}


def test_merge_shared_url_twins(monkeypatch):
    url = "https://econweb.ucsd.edu/~gdahl/research.html"
    _fake_fetch(monkeypatch, [
        ("HAVING COUNT(DISTINCT researcher_id) > 1) s", [
            {"url": url, "id": 24, "first_name": "Gordon B.", "last_name": "Dahl"},
            {"url": url, "id": 17140, "first_name": "Gordon B.", "last_name": "Dahl"},
            {"url": "http://www.stanford.edu/research", "id": 5655, "first_name": "Ran", "last_name": "Abramitzky"},
            {"url": "http://www.stanford.edu/research", "id": 18349, "first_name": "Nicholas", "last_name": "Bloom"},
        ]),
        ("FROM researchers r WHERE r.id IN", [_rec(24, "Gordon B.", "Dahl", None, urls=2, pubs=72),
                                              _rec(17140, "Gordon B.", "Dahl", None, urls=2, pubs=0)]),
    ])
    counts = repair.merge_shared_url_twins()
    assert counts["records_merged"] == 1
    assert repair.REPORT["merge_shared_url_twins"][0]["keep"] == 24


def test_route_page_papers_moves_stub_authorship_to_owner(monkeypatch):
    """Dora L. Costa 16478; all 50 papers of her page sit on stub "Dora Costa" 67804."""
    pairs = [{"owner_id": 16478, "paper_id": p, "owner_order": None} for p in (319495, 319496)]
    authors = [
        {"publication_id": 319495, "researcher_id": 67804, "author_order": 1, "first_name": "Dora",
         "last_name": "Costa", "openalex_author_id": None, "tracked": 0},
        {"publication_id": 319495, "researcher_id": 129563, "author_order": 2, "first_name": "Ziqi",
         "last_name": "Zhao", "openalex_author_id": None, "tracked": 0},
        {"publication_id": 319496, "researcher_id": 67804, "author_order": 1, "first_name": "Dora",
         "last_name": "Costa", "openalex_author_id": None, "tracked": 0},
    ]

    def weights(sql, params):
        rows = {16478: _rec(16478, "Dora L.", "Costa", None, urls=1, pubs=2),
                67804: _rec(67804, "Dora", "Costa", None, pubs=2)}
        return [rows[i] for i in params if i in rows]
    _fake_fetch(monkeypatch, [
        ("FROM paper_urls pu", pairs),
        ("FROM authorship a JOIN researchers r", authors),
        ("FROM researchers r WHERE r.id IN", weights),
    ])
    counts = repair.route_page_papers_to_owner()
    assert counts["authorships_moved"] == 2 and counts["papers"] == 2
    assert counts["stubs_merged"] == 1  # the stub had no other papers
    assert repair.REPORT["route_page_papers_to_owner"] == [
        {"owner": 16478, "moves": [{"paper": 319495, "from": 67804}, {"paper": 319496, "from": 67804}]}]


def test_route_page_papers_skips_tracked_namesakes_and_conflicting_ids(monkeypatch):
    pairs = [{"owner_id": 1, "paper_id": 10, "owner_order": 0}, {"owner_id": 1, "paper_id": 11, "owner_order": None}]
    authors = [
        {"publication_id": 10, "researcher_id": 2, "author_order": 1, "first_name": "J.",
         "last_name": "Smith", "openalex_author_id": None, "tracked": 1},
        {"publication_id": 11, "researcher_id": 3, "author_order": 1, "first_name": "Jane",
         "last_name": "Smith", "openalex_author_id": "A2", "tracked": 0},
    ]
    _fake_fetch(monkeypatch, [
        ("FROM paper_urls pu", pairs),
        ("FROM authorship a JOIN researchers r", authors),
        ("FROM researchers r WHERE r.id IN",
         lambda sql, params: [_rec(1, "Jane Q.", "Smith", "A1", urls=1)] if 1 in params else []),
    ])
    counts = repair.route_page_papers_to_owner()
    assert counts["authorships_moved"] == 0
    assert counts["skipped_other_tracked_researcher"] == 1
    assert counts["skipped_conflicting_openalex_id"] == 1


def test_main_rejects_unknown_step():
    with pytest.raises(SystemExit):
        repair.main(["--steps", "nope"])


def test_apply_requires_backup():
    with pytest.raises(SystemExit):
        repair.main(["--apply"])


def test_backup_round_trips_values_and_undo_skips_uncommitted_units(tmp_path, monkeypatch):
    import datetime
    path = tmp_path / "b.jsonl"
    bk = repair.Backup(str(path))
    when = datetime.datetime(2026, 11, 27, 14, 20, 47, 139131)
    done = bk.record("merge_openalex_twins", "a", [[["insert", "papers", [{"id": 1, "at": when, "blob": b"\x00z"}]]]])
    bk.committed(done)
    bk.record("merge_openalex_twins", "b", [[["insert", "papers", [{"id": 2}]]]])  # never committed
    assert repair.Backup(str(path)).next_id == 3

    applied = []
    monkeypatch.setattr(repair, "APPLY", True)
    monkeypatch.setattr(repair, "_write", lambda label, fn, record=True: applied.append(label) or True)
    counts = repair.undo(str(path))
    assert counts["units"] == 1 and applied == ["undo 1 a"]

    unit = repair._decode(__import__("json").loads(path.read_text().splitlines()[0]))
    row = unit["groups"][0][0][2][0]
    assert row["at"] == when and row["blob"] == b"\x00z"


def test_undo_pairs_removes_only_rows_moved_onto_survivor():
    from unittest.mock import MagicMock
    cur = MagicMock()
    cur.fetchall.return_value = []
    rows = [{"user_id": 1, "researcher_id": 42608}, {"user_id": 2, "researcher_id": 9583}]
    repair._undo_op(cur, ["pairs", "user_follows", "user_id", [9583, 42608], rows])
    deletes = [c.args[1] for c in cur.execute.call_args_list if c.args[0].startswith("DELETE")]
    assert deletes == [(9583, 1)]


def test_undo_set_keeps_values_changed_after_the_repair():
    from unittest.mock import MagicMock
    cur = MagicMock()
    cur.fetchone.side_effect = [{"v": None}, {"v": "A9"}]
    repair._undo_op(cur, ["set", "researchers", "openalex_author_id", [[1, "A6", None], [2, "A6", None]]])
    updates = [c.args[1] for c in cur.execute.call_args_list if c.args[0].startswith("UPDATE")]
    assert updates == [("A6", 1)]  # researcher 2 got a new ID since: kept


def test_undo_skips_units_that_recreate_opted_out_researchers(tmp_path, monkeypatch):
    import types
    fake = types.ModuleType("backend.database.optouts")
    fake.is_name_opted_out = lambda first, last: last == "Garin"
    fake.is_url_opted_out = lambda url: False
    monkeypatch.setitem(sys.modules, "backend.database.optouts", fake)
    path = tmp_path / "b.jsonl"
    bk = repair.Backup(str(path))
    unit = bk.record("merge_openalex_twins", "a", [[["rows", "researchers", ["id"],
                                                    [{"id": 42608, "first_name": "Andy", "last_name": "Garin"}]]]])
    bk.committed(unit)
    monkeypatch.setattr(repair, "APPLY", True)
    monkeypatch.setattr(repair, "_write", lambda *a, **k: pytest.fail("must not restore"))
    counts = repair.undo(str(path))
    assert counts["skipped_opted_out"] == 1 and counts["units"] == 0


def test_undo_filters_by_step_and_unit(tmp_path, monkeypatch):
    path = tmp_path / "b.jsonl"
    bk = repair.Backup(str(path))
    for step in ("merge_openalex_twins", "clear_conflicting_openalex_ids", "merge_openalex_twins"):
        bk.committed(bk.record(step, step, []))
    monkeypatch.setattr(repair, "APPLY", True)
    seen = []
    monkeypatch.setattr(repair, "_write", lambda label, fn, record=True: seen.append(label) or True)
    repair.undo(str(path), steps={"merge_openalex_twins"})
    assert seen == ["undo 3 merge_openalex_twins", "undo 1 merge_openalex_twins"]
    seen.clear()
    repair.undo(str(path), unit_ids=repair._parse_units("2-3"))
    assert seen == ["undo 3 merge_openalex_twins", "undo 2 clear_conflicting_openalex_ids"]
