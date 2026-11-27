"""Status changes found by the one-time long-page backfill (#283).

A page whose text was cut at 20k characters before chunking is re-extracted
once in full, as a seed, even when its text did not change
(_backfill_long_page). Papers it creates were on the page all along and stay
silent, but a paper the database already had can show a newer status in that
extraction (prod, 27 Nov 2026: "Playing with Blocs", accepted -> published at
the Journal of International Economics, emitted nothing). Those status
changes follow the same rules as any extraction (_append_snapshots).

Runs the real extract_one_url (only the LLM call patched) against MySQL when
TEST_MYSQL_HOST is set (CI starts a mysql:8.0.40 service on 3307). Locally:

    docker run -d --rm --name econ-sql-test -e MYSQL_ROOT_PASSWORD=test \\
        -p 3307:3306 mysql:8.0.40
    TEST_MYSQL_HOST=127.0.0.1 TEST_MYSQL_PORT=3307 poetry run pytest tests/test_backfill_status_events_mysql.py
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone
from unittest.mock import patch

from backend.pipeline.publication import ExtractionLLMResult
from tests.test_title_rename_collision_mysql import _exec, _q, _researcher, db  # noqa: F401

PAGE = "https://sites.google.com/view/elliotkang"
FETCHED = datetime(2026, 11, 27, 14, 58, tzinfo=timezone.utc)


def _html(n_entries: int = 400, extra: str = "") -> str:
    entries = "".join(
        f"<li>Paper number {i} on trade and labor markets. With Jane Coauthor{i}. Mimeo.</li>"
        for i in range(n_entries)
    )
    return (f"<html><head><title>Elliot Kang</title></head><body><main><h1>Elliot Kang</h1>"
            f"<h2>Research</h2><ul>{entries}{extra}</ul></main></body></html>")


def _long_page(owner_id: int, raw_html: str, *, untruncated: bool = False) -> int:
    """A tracked page extracted before chunking, whose text is unchanged since."""
    url_id = _exec("INSERT INTO researcher_urls (researcher_id, page_type, url) VALUES (%s, 'PUB', %s)",
                   (owner_id, PAGE))
    for n in (1, 2):  # baseline: the page has been fetched before
        _exec("INSERT INTO html_snapshots (url_id, text_content_hash, raw_html_hash, raw_html_compressed, "
              "snapshot_at) VALUES (%s, %s, %s, %s, %s)",
              (url_id, f"t{n}", f"r{n}", zlib.compress(raw_html.encode()), datetime(2026, 11, n, tzinfo=timezone.utc)))
    _exec("INSERT INTO html_content (url_id, raw_html, content_hash, extracted_hash, timestamp, extracted_at, "
          "extracted_untruncated, researcher_id) VALUES (%s, %s, 'full', 'cut', %s, %s, %s, %s)",
          (url_id, raw_html, FETCHED, datetime(2026, 11, 1, tzinfo=timezone.utc), untruncated, owner_id))
    return url_id


def _paper(title: str, status: str, venue: str | None, year: str, owner_id: int) -> int:
    from backend.database.papers import compute_title_hash
    pid = _exec(
        "INSERT INTO papers (title, title_hash, source_url, status, venue, year, discovered_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (title, compute_title_hash(title), PAGE, status, venue, year, "2025-06-01"),
    )
    _exec("INSERT INTO authorship (researcher_id, publication_id, author_order) VALUES (%s, %s, 1)",
          (owner_id, pid))
    _exec("INSERT INTO paper_snapshots (paper_id, title, status, venue, year, scraped_at, source_url, "
          "content_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, 'seed')",
          (pid, title, status, venue, year, "2025-06-01", PAGE))
    return pid


def _pub(title, status, venue=None, year="2026"):
    return dict(title=title, authors=["Elliot Kang"], year=year, venue=venue, status=status,
                draft_url=None, abstract=None)


def _extract(url_id: int, pubs: list[dict]):
    from backend.pipeline.extraction import extract_one_url
    with patch("backend.pipeline.extraction.Publication.try_extract_publications",
               return_value=ExtractionLLMResult(pubs=pubs)) as llm, \
            patch("backend.pipeline.extraction.match_and_save_paper_links"):
        outcome = extract_one_url({"id": url_id, "url": PAGE, "researcher_id": None,
                                   "page_type": "PUB"})
    return outcome, llm


def _events():
    return _q("SELECT paper_id, event_type, old_status, new_status, created_at, event_venue "
              "FROM feed_events ORDER BY id")


def test_existing_paper_advanced_in_backfill_gets_one_status_change(db):
    """The Playing-with-Blocs shape, for a transition the feed shows."""
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html())
    blocs = _paper("Playing with Blocs", "working_paper", None, "2026", kang)

    outcome, llm = _extract(url_id, [
        _pub("Playing with Blocs", "published", "Journal of International Economics"),
        _pub("Paper number 399 on trade and labor markets", "working_paper"),
    ])

    assert outcome.status == "extracted"
    assert len(llm.call_args.kwargs["chunks"]) > 1  # the chunked backfill ran
    events = _events()
    assert len(events) == 1
    ev = events[0]
    assert (ev["paper_id"], ev["event_type"], ev["old_status"], ev["new_status"]) == (
        blocs, "status_change", "working_paper", "published")
    assert ev["event_venue"] == "Journal of International Economics"
    assert ev["created_at"] == FETCHED.replace(tzinfo=None)
    assert _q("SELECT status FROM papers WHERE id = %s", (blocs,))[0]["status"] == "published"
    # The tail paper was created as a seed paper: no new_paper event.
    tail = _q("SELECT is_seed FROM papers WHERE title LIKE 'Paper number 399%%'")
    assert tail == [{"is_seed": 1}]
    assert _q("SELECT extracted_untruncated FROM html_content WHERE url_id = %s",
              (url_id,))[0]["extracted_untruncated"] == 1


def test_accepted_to_published_in_backfill_follows_the_feed_rules(db):
    """Prod's actual case: accepted -> published is never emitted (2893e73, and the
    feed hides such events via feed_visible_event_sql). The paper is updated."""
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html())
    blocs = _paper("Playing with Blocs", "accepted", "Journal of International Economics", "2026", kang)

    outcome, _ = _extract(url_id, [
        _pub("Playing with Blocs", "published", "Journal of International Economics")])

    assert outcome.ok
    assert _events() == []
    assert _q("SELECT status FROM papers WHERE id = %s", (blocs,))[0]["status"] == "published"


def test_cut_off_title_found_in_backfill_emits_nothing(db):
    """A paper the truncated extraction never saw is not news, whatever its status."""
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html())

    outcome, _ = _extract(url_id, [
        _pub("Paper number 399 on trade and labor markets", "accepted", "Journal of Things"),
        _pub("Paper number 398 on trade and labor markets", "working_paper"),
    ])

    assert outcome.status == "extracted"
    assert _events() == []
    assert {r["is_seed"] for r in _q("SELECT is_seed FROM papers")} == {1}


def test_title_listed_twice_in_backfill_is_still_new(db):
    """Overlapping chunks can return one new title twice with two statuses."""
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html())

    _extract(url_id, [
        _pub("Paper number 399 on trade and labor markets", "working_paper"),
        _pub("Paper number 399 on trade and labor markets", "accepted", "Journal of Things"),
    ])

    assert _events() == []


def test_downgrade_in_backfill_emits_nothing(db):
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html())
    blocs = _paper("Playing with Blocs", "accepted", "Journal of International Economics", "2026", kang)

    _extract(url_id, [_pub("Playing with Blocs", "working_paper")])

    assert _events() == []
    assert _q("SELECT status FROM papers WHERE id = %s", (blocs,))[0]["status"] == "accepted"


def test_advance_without_journal_venue_in_backfill_emits_nothing(db):
    """#280's plausibility rules apply to the backfill too."""
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html())
    _paper("Playing with Blocs", "working_paper", None, "2026", kang)

    _extract(url_id, [_pub("Playing with Blocs", "published", "VoxEU column")])

    assert _events() == []


def test_noise_only_change_on_truncated_page_backfills_with_status_events(db):
    """The backfill taken from inside the insignificant-change branch."""
    kang = _researcher("Elliot", "Kang")
    old_html = _html(extra="<li>Visitors: 41</li>")
    url_id = _long_page(kang, old_html)
    _exec("UPDATE html_content SET raw_html = %s WHERE url_id = %s",
          (_html(extra="<li>Visitors: 42</li>"), url_id))
    blocs = _paper("Playing with Blocs", "revise_and_resubmit", None, "2025", kang)

    outcome, llm = _extract(url_id, [
        _pub("Playing with Blocs", "accepted", "Journal of International Economics", "2025")])

    assert outcome.ok
    assert len(llm.call_args.kwargs["chunks"]) > 1
    assert [(e["paper_id"], e["old_status"], e["new_status"]) for e in _events()] == [
        (blocs, "revise_and_resubmit", "accepted")]


def test_untruncated_page_never_backfills_again(db):
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html(), untruncated=True)
    _paper("Playing with Blocs", "working_paper", None, "2026", kang)

    outcome, llm = _extract(url_id, [_pub("Playing with Blocs", "published", "Journal of International Economics")])

    assert outcome.status == "empty"
    llm.assert_not_called()
    assert _events() == []


def test_true_seed_extraction_stays_silent(db):
    """A page never extracted before is a plain seed: no events at all."""
    kang = _researcher("Elliot", "Kang")
    url_id = _long_page(kang, _html())
    _exec("UPDATE html_content SET extracted_at = NULL WHERE url_id = %s", (url_id,))
    _paper("Playing with Blocs", "working_paper", None, "2026", kang)

    outcome, _ = _extract(url_id, [_pub("Playing with Blocs", "published", "Journal of International Economics")])

    assert outcome.ok
    assert _events() == []


# ── scripts/backfill_lost_status_events.py ──

def _script():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_lost_status_events.py"
    spec = importlib.util.spec_from_file_location("backfill_lost_status_events", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BEFORE = datetime(2026, 11, 20, 10, 0)
BACKFILL = datetime(2026, 11, 27, 15, 30)


def _snapshot(pid, status, venue, year, at, url=PAGE, title="Playing with Blocs"):
    _exec("INSERT INTO paper_snapshots (paper_id, title, status, venue, year, scraped_at, source_url, "
          "content_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
          (pid, title, status, venue, year, at, url, f"{status}{at}"))


def _lost_event_world(old_status="working_paper", new_status="accepted", raw_html=None):
    """What prod looks like after a backfill swallowed a status change."""
    kang = _researcher("Elliot", "Kang")
    _long_page(kang, raw_html or _html())
    other = _paper("Paper number 1 on trade and labor markets", "working_paper", None, "2026", kang)
    _snapshot(other, "working_paper", None, "2026", BEFORE, title="Paper number 1")  # page extracted pre-#283
    blocs = _paper("Playing with Blocs", old_status, None, "2026", kang)
    _snapshot(blocs, new_status, "Journal of International Economics", "2026", BACKFILL)
    _exec("UPDATE papers SET status = %s WHERE id = %s", (new_status, blocs))
    return blocs


def test_recovery_script_plans_applies_and_undoes(db, tmp_path, capsys):
    mod = _script()
    blocs = _lost_event_world()
    backup = tmp_path / "lost.jsonl"

    assert mod.main([]) == 0
    assert "1 status_change events lost" in capsys.readouterr().out
    assert _events() == []  # dry run

    mod.main(["--apply", "--backup", str(backup)])
    events = _events()
    assert [(e["paper_id"], e["event_type"], e["old_status"], e["new_status"], e["created_at"],
             e["event_venue"]) for e in events] == [
        (blocs, "status_change", "working_paper", "accepted", BACKFILL, "Journal of International Economics")]
    assert len(backup.read_text().splitlines()) == 1

    capsys.readouterr()
    mod.main([])  # idempotent: the event now exists
    assert "0 status_change events lost" in capsys.readouterr().out

    mod.main(["--undo", str(backup)])
    assert len(_events()) == 1  # undo is a dry run without --apply
    mod.main(["--undo", str(backup), "--apply"])
    assert _events() == []


def test_recovery_script_skips_accepted_to_published(db):
    _lost_event_world(old_status="accepted", new_status="published")
    assert _script().load_and_plan(_script().DEFAULT_SINCE) == []


def test_recovery_script_skips_short_pages(db):
    """A short page went through non-seed extraction, which emitted its events."""
    _lost_event_world(raw_html=_html(n_entries=3))
    assert _script().load_and_plan(_script().DEFAULT_SINCE) == []


def test_recovery_script_skips_pages_first_extracted_after_cutoff(db):
    """A page's first extraction is a true seed and stays silent."""
    _lost_event_world()
    _exec("DELETE FROM paper_snapshots WHERE scraped_at < %s", (datetime(2026, 11, 27),))
    _exec("INSERT INTO paper_snapshots (paper_id, title, status, scraped_at, source_url, content_hash) "
          "SELECT id, title, 'working_paper', %s, 'https://elsewhere.org', 'x' FROM papers "
          "WHERE title = 'Playing with Blocs'", (BEFORE,))
    assert _script().load_and_plan(_script().DEFAULT_SINCE) == []


def test_recovery_script_applies_plausibility_rules(db):
    blocs = _lost_event_world(new_status="published")
    _exec("UPDATE paper_snapshots SET venue = 'VoxEU column' WHERE paper_id = %s", (blocs,))
    assert _script().load_and_plan(_script().DEFAULT_SINCE) == []
