"""Tests for the pure decision logic in scripts/monitor.py."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from scripts import monitor

HEALTHY = {
    "scrape_age_h": 3, "stuck_scrapes": 0, "pending": 400,
    "extracted_6h": 300, "feed_recent": 250, "enrich_age_h": 12,
    "snapshot_guard": 1,
}


def _failing(results):
    return [r["name"] for r in results if not r["ok"]]


class TestEvaluatePipeline:
    def test_healthy_pipeline_passes_every_check(self):
        assert _failing(monitor.evaluate_pipeline(HEALTHY)) == []

    def test_billing_outage_trickle_counts_as_stalled(self):
        """2026-11 outage: 14,659 pages waiting while ~15 completed per 6h."""
        stats = {**HEALTHY, "pending": 14659, "extracted_6h": 15}
        assert _failing(monitor.evaluate_pipeline(stats)) == ["extraction_throughput"]

    def test_large_backlog_that_is_draining_is_healthy(self):
        stats = {**HEALTHY, "pending": 14659, "extracted_6h": 360}
        assert _failing(monitor.evaluate_pipeline(stats)) == []

    def test_small_leftover_queue_does_not_alert(self):
        """A few hundred permanently failing pages are not an outage."""
        stats = {**HEALTHY, "pending": 280, "extracted_6h": 0}
        assert _failing(monitor.evaluate_pipeline(stats)) == []

    @pytest.mark.parametrize("override, expected", [
        ({"scrape_age_h": 37}, "scrape_recent"),
        ({"scrape_age_h": None}, "scrape_recent"),
        ({"stuck_scrapes": 1}, "scrape_not_stuck"),
        ({"feed_recent": 0}, "feed_fresh"),
        ({"enrich_age_h": 49}, "enrichment_active"),
        ({"enrich_age_h": None}, "enrichment_active"),
        ({"snapshot_guard": 0}, "snapshot_guard_trigger"),
    ])
    def test_each_stage_alerts_on_its_own(self, override, expected):
        assert _failing(monitor.evaluate_pipeline({**HEALTHY, **override})) == [expected]


class TestParsePipelineRow:
    def test_parses_integers_and_nulls(self):
        row = monitor.parse_pipeline_row("3\t0\t14658\t17\t29\tNULL\t1\n")
        assert row == {
            "scrape_age_h": 3, "stuck_scrapes": 0, "pending": 14658,
            "extracted_6h": 17, "feed_recent": 29, "enrich_age_h": None,
            "snapshot_guard": 1,
        }

    def test_rejects_unexpected_shape(self):
        with pytest.raises(ValueError):
            monitor.parse_pipeline_row("3\t0\n")


class TestLlmAccountErrors:
    def test_counts_only_billing_and_credential_rejections(self):
        base = 'httpx INFO HTTP Request: POST https://x/v1beta/openai/chat/completions "HTTP/1.1 '
        log = "\n".join([
            base + '402 Payment Required"',
            base + '403 Forbidden"',
            base + '401 Unauthorized"',
            base + '429 Too Many Requests"',
            base + '200 OK"',
            base + '503 Service Unavailable"',
        ])
        assert monitor.count_account_errors(log) == 3


class TestApiTraffic:
    LINES = [
        "2026-11-27 10:00:00 backend.api WARNING rate_limited ip=203.0.113.9 tier=visitor path=/api/researchers/1 limit=60 per 1 minute retry_after=12",
        "2026-11-27 10:00:01 backend.api WARNING rate_limited ip=203.0.113.9 tier=visitor path=/api/researchers/2 limit=60 per 1 minute retry_after=11",
        "2026-11-27 10:00:02 backend.api WARNING rate_limited ip=198.51.100.4 tier=direct path=/api/publications limit=20 per 1 minute retry_after=30",
        "2026-11-27 10:00:03 backend.api_protection INFO api_blocked reason=user_agent ip=198.51.100.4 path=/api/publications ua='curl/8'",
        "2026-11-27 10:00:04 backend.api_protection WARNING internal_key_invalid ip=76.76.21.21 path=/api/publications: request carried a wrong X-Internal-Api-Key",
        'INFO:     172.18.0.1:5000 - "GET /api/publications?page=1 HTTP/1.1" 200 OK',
        'INFO:     172.18.0.1:5001 - "GET /api/health HTTP/1.1" 200 OK',
    ]

    def test_summarize(self):
        stats = monitor.summarize_api_traffic(self.LINES)
        assert stats["rate_limited"] == 3
        assert stats["by_ip"] == {"203.0.113.9": 2, "198.51.100.4": 1}
        assert stats["blocked"] == 1
        assert stats["invalid_key"] == 1
        assert stats["requests"] == 2

    def test_quiet_day_passes_and_names_top_ip(self):
        result = monitor.evaluate_api_traffic(monitor.summarize_api_traffic(self.LINES))
        assert result["ok"]
        assert "203.0.113.9 x2" in result["detail"]

    def test_429_flood_fails(self):
        lines = ["rate_limited ip=203.0.113.9 tier=visitor"] * monitor.API_RATE_LIMITED_PER_DAY
        assert not monitor.evaluate_api_traffic(monitor.summarize_api_traffic(lines))["ok"]

    def test_email_login_circuit_open_fails(self):
        lines = self.LINES + [
            "2026-11-27 10:05:00 backend.email_login ERROR email_login_circuit_open failures=104 "
            "limit=100 window=1h: email sign-in paused (possible code guessing)",
        ]
        stats = monitor.summarize_api_traffic(lines)
        assert stats["email_login_paused"] == 1
        result = monitor.evaluate_api_traffic(stats)
        assert not result["ok"]
        assert "email sign-in paused 1x" in result["detail"]

    def test_wrong_frontend_key_fails(self):
        lines = ["internal_key_invalid ip=76.76.21.21"] * monitor.API_INVALID_KEY_WARNINGS
        assert not monitor.evaluate_api_traffic(monitor.summarize_api_traffic(lines))["ok"]


class TestBackupCheck:
    NOW = 1_800_000_000.0

    def _dump(self, tmp_path, age_hours, size=monitor.BACKUP_MIN_BYTES):
        path = tmp_path / "econ_newsfeed_20261116_030001.sql.gz"
        with open(path, "wb") as f:
            f.truncate(size)  # sparse: no real disk use
        mtime = self.NOW - age_hours * 3600
        os.utime(path, (mtime, mtime))
        return path

    def test_recent_full_size_backup_passes(self, tmp_path):
        self._dump(tmp_path, age_hours=10)
        assert monitor.backup_check(tmp_path, now=self.NOW)["ok"]

    def test_missing_backup_fails(self, tmp_path):
        assert not monitor.backup_check(tmp_path, now=self.NOW)["ok"]

    def test_stale_backup_fails(self, tmp_path):
        self._dump(tmp_path, age_hours=31)
        result = monitor.backup_check(tmp_path, now=self.NOW)
        assert not result["ok"] and "31h old" in result["detail"]

    def test_truncated_backup_fails(self, tmp_path):
        self._dump(tmp_path, age_hours=1, size=1024)
        assert not monitor.backup_check(tmp_path, now=self.NOW)["ok"]

    def test_error_in_latest_run_fails(self, tmp_path):
        self._dump(tmp_path, age_hours=20)
        (tmp_path / "backup.log").write_text(
            "2026-11-15T03:00:01Z Starting backup\nBackup created\n"
            "2026-11-16T03:00:01Z Starting backup\nERROR: not enough free space, skipping\n"
        )
        result = monitor.backup_check(tmp_path, now=self.NOW)
        assert not result["ok"] and "not enough free space" in result["detail"]

    def test_error_from_an_earlier_run_is_ignored(self, tmp_path):
        self._dump(tmp_path, age_hours=2)
        (tmp_path / "backup.log").write_text(
            "2026-11-15T03:00:01Z Starting backup\nERROR: not enough free space, skipping\n"
            "2026-11-16T03:00:01Z Starting backup\nBackup created\n"
        )
        assert monitor.backup_check(tmp_path, now=self.NOW)["ok"]


class TestLoadResults:
    def test_merges_result_files(self, tmp_path):
        a, b = tmp_path / "external.json", tmp_path / "host.json"
        a.write_text(json.dumps([monitor.check("frontend", True, "ok")]))
        b.write_text(json.dumps([monitor.check("backup", False, "stale")]))
        assert [r["name"] for r in monitor.load_results([str(a), str(b)])] == ["frontend", "backup"]

    @pytest.mark.parametrize("content", [None, "", "ssh: connect to host timed out", "[]"])
    def test_unreadable_host_results_mean_the_server_is_unreachable(self, tmp_path, content):
        path = tmp_path / "host.json"
        if content is not None:
            path.write_text(content)
        results = monitor.load_results([str(path)])
        assert _failing(results) == ["server_reachable"]


class TestAlertIssue:
    NOW = datetime(2026, 11, 17, 7, 17, tzinfo=timezone.utc)

    def _issue(self, *failing, stage="alerted", asked_hours_ago=None):
        asked = None
        if asked_hours_ago is not None:
            asked = (self.NOW - timedelta(hours=asked_hours_ago)).isoformat()
        state = {"failing": list(failing), "stage": stage, "asked_at": asked}
        return {"number": 7, "body": f"text\n<!-- monitor-state: {json.dumps(state)} -->"}

    def _plan(self, failing, issue, can_ask=True):
        return monitor.plan_issue_action(failing, issue, can_ask, self.NOW)

    def test_no_failures_and_no_issue_does_nothing(self):
        assert self._plan([], None) == "none"

    def test_new_failure_asks_claude_before_alerting(self):
        assert self._plan(["backup"], None) == "ask_claude"

    def test_new_failure_alerts_directly_when_claude_cannot_be_asked(self):
        assert self._plan(["backup"], None, can_ask=False) == "open"

    def test_same_day_rerun_gives_claude_time(self):
        issue = self._issue("backup", stage="claude", asked_hours_ago=3)
        assert self._plan(["backup"], issue) == "none"

    def test_still_failing_next_day_alerts_the_owner(self):
        issue = self._issue("backup", stage="claude", asked_hours_ago=23.5)
        assert self._plan(["backup"], issue) == "escalate"

    def test_unreadable_ask_time_alerts_rather_than_waits(self):
        issue = self._issue("backup", stage="claude")
        assert self._plan(["backup"], issue) == "escalate"

    def test_fixed_by_next_day_closes_without_alerting(self):
        issue = self._issue("backup", stage="claude", asked_hours_ago=24)
        assert self._plan([], issue) == "close"

    def test_same_failures_do_not_notify_again_once_alerted(self):
        assert self._plan(["backup", "disk_space"], self._issue("disk_space", "backup")) == "none"

    def test_changed_failures_update_an_alerted_issue(self):
        assert self._plan(["backup", "llm_provider"], self._issue("backup")) == "update"

    def test_recovery_closes_the_issue(self):
        assert self._plan([], self._issue("backup")) == "close"

    def _results(self):
        return [
            monitor.check("backup", False, "newest backup is 31h old"),
            monitor.check("frontend", True, "ok"),
        ]

    def test_claude_stage_body_does_not_mention_anyone(self):
        """No owner mention means no alert email; no @claude means the ask is the comment."""
        body = monitor.issue_body(self._results(), self.NOW, "https://example.invalid/run/1",
                                  "claude", asked_at=self.NOW.isoformat())
        assert "@" not in body
        assert "| `backup` | newest backup is 31h old |" in body
        assert "Passing: frontend" in body
        assert monitor.read_state(body) == {
            "failing": ["backup"], "stage": "claude", "asked_at": self.NOW.isoformat(),
        }

    def test_direct_alert_body_mentions_the_owner(self):
        body = monitor.issue_body(self._results(), self.NOW, "", "alerted", notify="octocat")
        assert body.startswith("@octocat Production monitoring found 1 failing check(s)")
        assert monitor.read_state(body)["stage"] == "alerted"

    def test_request_addresses_claude(self):
        assert monitor.CLAUDE_REQUEST.startswith("@claude ")

    def test_title_truncates_long_lists(self):
        title = monitor.issue_title(["a", "b", "c", "d", "e"])
        assert title == "Production alert: a, b, c (+2 more)"


class TestReportFlow:
    """Which GitHub calls a run makes, and with which token."""

    def _run(self, tmp_path, monkeypatch, *, failing, open_issue=None, ask_token="pat",
             reject_ask=False):
        results = [monitor.check("backup", not failing, "detail"), monitor.check("frontend", True, "ok")]
        path = tmp_path / "host.json"
        path.write_text(json.dumps(results))
        calls = []

        def fake_gh(*args, token=None):
            calls.append((args[:2], token, args))
            if args[:2] == ("issue", "list"):
                return json.dumps([open_issue] if open_issue else [])
            if args[:2] == ("issue", "create"):
                return "https://github.com/o/r/issues/12\n"
            if args[:2] == ("issue", "comment") and token and reject_ask:
                raise RuntimeError("gh issue comment failed: Bad credentials")
            return ""

        monkeypatch.setattr(monitor, "gh", fake_gh)
        monkeypatch.setenv("MONITOR_ASK_TOKEN", ask_token)
        monkeypatch.setenv("MONITOR_NOTIFY", "octocat")
        assert monitor.report([str(path)], dry_run=False) == 0
        return [c for c in calls if c[0] not in (("label", "create"), ("issue", "list"))]

    @staticmethod
    def _body(call):
        return call[2][call[2].index("--body") + 1]

    def test_new_failure_opens_quietly_then_asks_claude_as_the_user(self, tmp_path, monkeypatch):
        create, comment = self._run(tmp_path, monkeypatch, failing=True)
        assert create[0] == ("issue", "create") and create[1] is None
        assert "@" not in self._body(create)
        assert comment[0] == ("issue", "comment") and comment[1] == "pat"
        assert comment[2][2] == "12"
        assert self._body(comment) == monitor.CLAUDE_REQUEST

    def test_rejected_token_falls_back_to_alerting_the_owner(self, tmp_path, monkeypatch):
        calls = self._run(tmp_path, monkeypatch, failing=True, reject_ask=True)
        alert = calls[-1]
        assert alert[0] == ("issue", "comment") and alert[1] is None
        assert self._body(alert).startswith("@octocat ")
        assert "@claude" not in self._body(alert)

    def test_without_a_token_the_owner_is_alerted_in_the_new_issue(self, tmp_path, monkeypatch):
        (create,) = self._run(tmp_path, monkeypatch, failing=True, ask_token="")
        assert create[1] is None and self._body(create).startswith("@octocat ")

    def test_escalation_mentions_the_owner_from_the_bot_and_not_claude(self, tmp_path, monkeypatch):
        state = {"failing": ["backup"], "stage": "claude", "asked_at": "2020-01-01T00:00:00+00:00"}
        issue = {"number": 7, "body": f"<!-- monitor-state: {json.dumps(state)} -->"}
        edit, comment = self._run(tmp_path, monkeypatch, failing=True, open_issue=issue)
        assert edit[0] == ("issue", "edit") and monitor.read_state(self._body(edit))["stage"] == "alerted"
        assert comment[1] is None, "a mention posted with the owner's own token sends no email"
        assert self._body(comment).startswith("@octocat ")
        assert "@claude" not in self._body(comment), "would start another Claude run"

    def test_recovery_closes_with_a_single_call(self, tmp_path, monkeypatch):
        issue = {"number": 7, "body": '<!-- monitor-state: {"failing": ["backup"], "stage": "alerted"} -->'}
        (close,) = self._run(tmp_path, monkeypatch, failing=False, open_issue=issue)
        assert close[2][:3] == ("issue", "close", "7")
