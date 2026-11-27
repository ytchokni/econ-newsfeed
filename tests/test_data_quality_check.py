"""Tests for the issue bookkeeping in scripts/data_quality_check.py."""
import json

from scripts import data_quality_check as dq

TITLE = "tests_data_quality/test_paper_quality.py::TestTitleQuality::test_no_lowercase"
YEAR = "tests_data_quality/test_paper_quality.py::TestYearQuality::test_null_year_rate"
ZOMBIE = "signal:scrape_log_zombies"


def results(day="2026-11-27", failed=(), passed=(), complete=True, signals=()):
    invariants = [{"id": i, "outcome": "failed", "doc": "", "count": 1234,
                   "examples": ["id=1", "id=2"], "message": "1,234 violating row(s)"} for i in failed]
    invariants += [{"id": i, "outcome": "passed", "doc": "", "count": None,
                    "examples": [], "message": ""} for i in passed]
    return {"generated_at": f"{day}T07:50:00+00:00", "complete": complete, "error": None,
            "totals": {"passed": len(passed), "failed": len(failed), "error": 0, "skipped": 0},
            "invariants": invariants, "signals": list(signals)}


class FakeGitHub(dq.GitHub):
    """Records writes; answers reads from an in-memory issue list."""

    def __init__(self, issues=None, report=None, merged=()):
        super().__init__()
        self.merged = set(merged)
        self.linked = {}
        self.issues = {n: {"number": n, "state": s, "title": f"#{n}"} for n, s in (issues or {}).items()}
        self.report = report
        self.next_number = 500

    def report_issue(self):
        return self.report

    def issue(self, number):
        return self.issues.get(number)

    def open_linked_prs(self, number):
        return self.linked.get(number, [])

    def pr_merged(self, number):
        return number in self.merged

    def write(self, *args):
        self.writes.append(list(args))
        if args[:2] == ("issue", "create"):
            self.next_number += 1
            return f"https://github.com/o/r/issues/{self.next_number}\n"
        if args[:2] == ("issue", "reopen"):
            self.issues[int(args[2])]["state"] = "OPEN"
        return ""

    def created(self):
        return [w for w in self.writes if w[:2] == ["issue", "create"]]


def report_with_state(state):
    return {"number": 300, "title": dq.REPORT_TITLE,
            "body": dq.report_body({"issue_map": {}, "pass_days": {}, **state})}


class TestParsing:
    def test_message_drops_pytest_introspection(self):
        text = "AssertionError: bad titles:\n3 violating row(s); first 3:\n  id=4\nassert not [{'id': 4}]"
        assert dq.failure_message(text) == "bad titles:\n3 violating row(s); first 3:\n  id=4"

    def test_counts_from_fmt_violations_and_ratios(self):
        assert dq.violation_count("x:\n1,234 violating row(s); first 15:") == 1234
        assert dq.violation_count("2/9 papers (22%) have NULL year") == 2
        assert dq.violation_count("trigger is missing") is None

    def test_example_ids_are_unique_and_capped(self):
        msg = "id=1, paper_id=7\n id=1, researcher_id=9\n id=2\n id=3\n id=4"
        assert dq.example_ids(msg) == ["id=1", "paper_id=7", "researcher_id=9", "id=2", "id=3"]


class TestMentions:
    def test_mentions_are_wrapped_in_backticks(self):
        assert dq.defuse_mentions("ask @ytchokni and @claude") == "ask @\u200bytchokni and @\u200bclaude"

    def test_stray_backtick_cannot_bypass(self):
        assert "@ytchokni" not in dq.defuse_mentions("x ` @ytchokni `@org/team")


class TestApplyTriage:
    def test_caps_new_issues_and_filters_labels_and_ids(self):
        fails = dq.failures(results(failed=[TITLE, YEAR, "a", "b"]))
        gh = FakeGitHub()
        state = dq.read_state("")
        groups = [{"invariants": [i], "action": "create", "title": f"t{i}", "body": "@x",
                   "high_priority": True, "labels": ["monitor-alert"]} for i in (TITLE, YEAR, "a", "b")]
        groups.append({"invariants": ["not-failing"], "action": "create", "title": "bogus"})
        log = dq.apply_triage({"groups": groups}, fails, state, gh, "2026-11-27", "")
        created = gh.created()
        assert len(created) == dq.MAX_NEW_ISSUES
        for args in created:
            assert "monitor-alert" not in args
            assert args.count("--label") == 2  # bug + high-priority only
            assert "@\u200bx" in args[args.index("--body") + 1]
        assert any("limit of 3" in line for line in log)
        assert set(state["issue_map"]) == {TITLE, YEAR, "a"}
        assert "b" not in state["issue_map"]  # retried tomorrow

    def test_comment_on_closed_issue_needs_explicit_reopen(self):
        fails = [{**f, "previous_issue": 261} for f in dq.failures(results(failed=[TITLE, YEAR]))]
        gh = FakeGitHub(issues={260: "CLOSED", 261: "CLOSED"})
        state = dq.read_state("")
        dq.apply_triage({"groups": [
            {"invariants": [TITLE], "action": "comment", "issue": 260, "body": "back"},
            {"invariants": [YEAR], "action": "comment", "issue": 261, "body": "back", "reopen": True},
        ]}, fails, state, gh, "2026-11-27", "")
        assert ["issue", "reopen", "261"] in gh.writes
        assert not any(w[:3] == ["issue", "comment", "260"] for w in gh.writes)
        assert state["issue_map"] == {YEAR: 261}

    def test_fix_pr_is_noted(self):
        fails = dq.failures(results(failed=[TITLE]))
        gh = FakeGitHub(issues={260: "OPEN"})
        state = dq.read_state("")
        dq.apply_triage({"groups": [{"invariants": [TITLE], "action": "comment", "issue": 260,
                                     "body": "same cause", "fix_pr": 281}]},
                        fails, state, gh, "2026-11-27", "")
        body = gh.writes[0][gh.writes[0].index("--body") + 1]
        assert "#281" in body and "Fixes #" in body and "dq-invariants" in body


class TestCleanupPending:
    CLEANUP = {"invariants": [TITLE], "action": "cleanup_pending", "issue": 260, "fix_pr": 280,
               "cleanup": "poetry run python scripts/cleanup_false_feed_events.py --apply", "body": "x"}

    def test_merged_fix_becomes_a_report_line_not_an_issue_change(self):
        fails = dq.failures(results(failed=[TITLE]))
        gh = FakeGitHub(issues={260: "CLOSED"}, merged={280})
        state = dq.read_state("")
        log = dq.apply_triage({"groups": [self.CLEANUP]}, fails, state, gh, "2026-11-27", "")
        assert gh.writes == []
        assert log == ["cleanup pending for #260 (fix #280 merged)"]
        assert state["cleanup_pending"][TITLE]["cleanup"].endswith("--apply")
        # Tomorrow it is still failing but needs no triage, and the report says what to run.
        assert dq.untracked_failures(fails, state, gh) == []
        text = dq.summary_comment(results(failed=[TITLE]), state, {}, [], "")
        assert "#260 (cleanup pending)" in text
        assert "Run `poetry run python scripts/cleanup_false_feed_events.py --apply`" in text

    def test_unmerged_fix_is_refused(self):
        fails = dq.failures(results(failed=[TITLE]))
        gh = FakeGitHub(issues={260: "OPEN"}, merged=())
        state = dq.read_state("")
        log = dq.apply_triage({"groups": [self.CLEANUP]}, fails, state, gh, "2026-11-27", "")
        assert "not merged" in log[0]
        assert state["cleanup_pending"] == {} and state["issue_map"] == {}

    def test_cleanup_ends_when_the_invariant_passes(self):
        state = {"issue_map": {TITLE: 260}, "pass_days": {},
                 "cleanup_pending": {TITLE: {"issue": 260, "fix_pr": 280, "cleanup": "x", "since": "d"}}}
        gh = FakeGitHub(issues={260: "CLOSED"})
        dq.update_streaks(state, results("2026-11-28", passed=[TITLE]), "2026-11-28")
        assert state["cleanup_pending"] == {}
        # A later failure is a new problem for triage again.
        fails = dq.failures(results(failed=[TITLE]))
        assert [f["id"] for f in dq.untracked_failures(fails, state, gh)] == [TITLE]


class TestClosing:
    def test_closes_after_two_passing_days_not_two_runs(self):
        state = {"issue_map": {TITLE: 260, YEAR: 260}, "pass_days": {}, "created_issues": [260]}
        gh = FakeGitHub(issues={260: "OPEN"})
        dq.update_streaks(state, results("2026-11-27", passed=[TITLE, YEAR]), "2026-11-27")
        dq.update_streaks(state, results("2026-11-27", passed=[TITLE, YEAR]), "2026-11-27")
        assert dq.close_recovered(state, gh, "") == []
        dq.update_streaks(state, results("2026-11-28", passed=[TITLE, YEAR]), "2026-11-28")
        log = dq.close_recovered(state, gh, "")
        assert log == ["closed #260 (passed 2026-11-27, 2026-11-28)"]
        assert ["issue", "close", "260", "--reason", "completed"] in gh.writes
        assert state["issue_map"] == {}

    def test_one_failing_invariant_keeps_the_issue_open(self):
        state = {"issue_map": {TITLE: 260, YEAR: 260}, "pass_days": {}, "created_issues": [260]}
        gh = FakeGitHub(issues={260: "OPEN"})
        dq.update_streaks(state, results("2026-11-27", passed=[TITLE, YEAR]), "2026-11-27")
        dq.update_streaks(state, results("2026-11-28", passed=[TITLE], failed=[YEAR]), "2026-11-28")
        dq.update_streaks(state, results("2026-11-29", passed=[TITLE, YEAR]), "2026-11-29")
        assert dq.close_recovered(state, gh, "") == []
        assert state["pass_days"][YEAR] == ["2026-11-29"]

    def test_skipped_invariant_neither_passes_nor_resets(self):
        state = {"issue_map": {TITLE: 260}, "pass_days": {TITLE: ["2026-11-27"]}}
        dq.update_streaks(state, results("2026-11-28"), "2026-11-28")
        assert state["pass_days"][TITLE] == ["2026-11-27"]

    def test_signal_recovery_counts(self):
        state = {"issue_map": {ZOMBIE: 262}, "pass_days": {}, "created_issues": [262]}
        gh = FakeGitHub(issues={262: "OPEN"})
        for day in ("2026-11-27", "2026-11-28"):
            sig = [{"id": ZOMBIE, "ok": True, "value": 0, "detail": "0"}]
            dq.update_streaks(state, results(day, signals=sig), day)
        assert dq.close_recovered(state, gh, "") == ["closed #262 (passed 2026-11-27, 2026-11-28)"]


class TestClosingGuards:
    def test_not_closed_when_not_filed_by_the_check_or_pr_open(self):
        for created, linked, closed in (([], {}, False), ([260], {260: [290]}, False), ([260], {}, True)):
            state = {"issue_map": {TITLE: 260}, "pass_days": {TITLE: ["d1", "d2"]}, "created_issues": created}
            gh = FakeGitHub(issues={260: "OPEN"})
            gh.linked = linked
            dq.close_recovered(state, gh, "")
            assert (["issue", "close", "260", "--reason", "completed"] in gh.writes) is closed


class TestPlanAndReport:
    def test_plan_skips_claude_when_every_failure_has_an_open_issue(self, tmp_path, monkeypatch):
        path = tmp_path / "results.json"
        path.write_text(json.dumps(results(failed=[TITLE])))
        output = tmp_path / "out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        gh = FakeGitHub(issues={260: "OPEN"}, report=report_with_state({"issue_map": {TITLE: 260}}))
        dq.plan(str(path), str(tmp_path), gh)
        assert output.read_text() == "needs_triage=false\n"

    def test_plan_asks_claude_when_the_tracking_issue_was_closed(self, tmp_path, monkeypatch):
        path = tmp_path / "results.json"
        path.write_text(json.dumps(results(failed=[TITLE])))
        output = tmp_path / "out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        gh = FakeGitHub(issues={260: "CLOSED"}, report=report_with_state({"issue_map": {TITLE: 260}}))
        dq.plan(str(path), str(tmp_path), gh)
        assert output.read_text() == "needs_triage=true\n"
        triage_input = json.loads((tmp_path / "triage_input.json").read_text())
        assert triage_input["untracked_failures"][0]["previous_issue"] == 260

    def test_incomplete_run_reports_and_fails_without_touching_state(self, tmp_path):
        path = tmp_path / "results.json"
        path.write_text(json.dumps({**results(), "complete": False, "error": "Database not reachable"}))
        state = {"issue_map": {TITLE: 260}, "pass_days": {TITLE: ["2026-11-26"]}}
        gh = FakeGitHub(issues={260: "OPEN"}, report=report_with_state(state))
        assert dq.report(str(path), None, gh) == 1
        edit = next(w for w in gh.writes if w[:2] == ["issue", "edit"])
        assert dq.read_state(edit[-1]) == {**state, "cleanup_pending": {}}
        comment = next(w for w in gh.writes if w[:2] == ["issue", "comment"])
        assert "could not run" in comment[-1]

    def test_report_creates_the_rolling_issue_once(self, tmp_path):
        path = tmp_path / "results.json"
        path.write_text(json.dumps(results(passed=[TITLE])))
        gh = FakeGitHub()
        assert dq.report(str(path), None, gh) == 0
        create = gh.created()[0]
        assert create[create.index("--title") + 1] == dq.REPORT_TITLE
        assert "Every invariant passes." in gh.writes[-1][-1]
