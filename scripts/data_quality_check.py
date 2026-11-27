#!/usr/bin/env python3
"""Daily data-quality check against the production database.

Runs the `tests_data_quality` invariants and a few cheap pipeline signals,
writes a JSON summary, and keeps GitHub issues in step with what fails.
The Data quality workflow (.github/workflows/data-quality.yml) calls it in
three steps:

  run    --out results.json        run the suite (read-only) and the signals
  plan   results.json --out-dir D  decide whether Claude must triage: only
                                   failures not yet tracked by an open issue
  report results.json [--triage T] apply Claude's triage within fixed limits,
                                   post the daily summary, close issues whose
                                   invariants passed on two consecutive days

`run` needs the backend dependencies and pytest; `plan` and `report` are
standard library only and talk to GitHub through the `gh` CLI.

State lives in a hidden comment in the body of one rolling issue titled
"Daily data-quality report": which invariant is tracked by which issue, and on
which days each tracked invariant passed. Claude never writes to GitHub
itself; it proposes issue comments and new issues in a JSON file and this
script applies them: at most MAX_NEW_ISSUES new issues per run, only the
`bug` and `high-priority` labels, and every @mention defused. A failure
whose fix is merged but whose one-off cleanup has not run yet is recorded as
"cleanup pending" (after checking the pull request is merged): one line with
the cleanup step in the daily report, no new issue and no reopening, and no
further triage until the invariant passes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUITE = "tests_data_quality"

REPORT_TITLE = "Daily data-quality report"
REPORT_MARKER = "<!-- data-quality-report -->"
STATE_RE = re.compile(r"<!-- dq-state: (.*?) -->", re.S)
MAX_NEW_ISSUES = 3
PASS_DAYS_TO_CLOSE = 2
ALLOWED_LABELS = ("bug", "high-priority")
MAX_BODY_CHARS = 20000
MAX_MESSAGE_CHARS = 1500

# Pipeline signals that scripts/monitor.py does not already alert on. The
# monitor covers scrape start age, scrapes stuck >24h, extraction throughput
# against the backlog, feed freshness, enrichment age and LLM billing or
# credential rejections (from the API logs); these complement it.
BACKLOG_STALE_DAYS = 14
BACKLOG_STALE_MAX = 500           # pages pending extraction for over two weeks
ZOMBIE_MIN_HOURS = 2              # the sweeper runs hourly
COMPLETED_SCRAPE_MAX_HOURS = 72   # a scrape that starts but never completes

# Session settings for every connection the check opens. Even if a writable
# user were configured by mistake, MySQL refuses writes in a read-only
# transaction, and no single query may run longer than five minutes.
READ_ONLY_INIT = "SET SESSION transaction_read_only = 1, SESSION max_execution_time = 300000"


# --- run: the suite and the signals ------------------------------------------

def failure_message(text: str) -> str:
    """The assertion message without pytest's introspection of the expression."""
    text = (text or "").strip()
    text = re.sub(r"^AssertionError:\s*", "", text)
    cut = re.search(r"\nassert ", text)
    if cut:
        text = text[:cut.start()]
    return text.strip()[:MAX_MESSAGE_CHARS]


def violation_count(message: str) -> int | None:
    """Row count from conftest.fmt_violations ("1,234 violating row(s)")."""
    match = re.search(r"(\d[\d,]*) violating row", message or "")
    if match:
        return int(match.group(1).replace(",", ""))
    match = re.match(r"\D{0,60}?(\d[\d,]*)", message or "")
    return int(match.group(1).replace(",", "")) if match else None


def example_ids(message: str, limit: int = 5) -> list[str]:
    """First few `id=123` / `paper_id=123` pairs from a failure message."""
    seen: list[str] = []
    for name, value in re.findall(r"\b(\w*id)=(\d+)", message or ""):
        pair = f"{name}={value}"
        if pair not in seen:
            seen.append(pair)
        if len(seen) >= limit:
            break
    return seen


def make_collector():
    """pytest plugin that records one outcome and message per test."""
    import pytest

    class Collector:
        def __init__(self):
            self.items: dict[str, dict] = {}

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            rep = outcome.get_result()
            doc = (getattr(getattr(item, "function", None), "__doc__", None) or "").strip()
            entry = self.items.setdefault(rep.nodeid, {
                "id": rep.nodeid, "outcome": "passed", "doc": doc.split("\n")[0][:200],
                "message": "", "count": None, "examples": [],
            })
            if rep.passed:
                return
            if rep.skipped:
                if entry["outcome"] == "passed":
                    entry["outcome"] = "skipped"
                    reason = rep.longrepr[2] if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
                    entry["message"] = str(reason).removeprefix("Skipped: ")[:300]
                return
            entry["outcome"] = "failed" if rep.when == "call" else "error"
            text = str(call.excinfo.value) if call.excinfo else str(rep.longrepr)
            if rep.when != "call" and call.excinfo:
                text = f"{call.excinfo.typename}: {text}"
            entry["message"] = failure_message(text)
            entry["count"] = violation_count(entry["message"])
            entry["examples"] = example_ids(entry["message"])

    return Collector()


def collect_signals(fetch_one) -> list[dict]:
    """Cheap pipeline numbers. `ok` is None when a signal is informational."""
    signals: list[dict] = []

    def add(sid, sql, build):
        try:
            row = fetch_one(sql) or {}
            ok, value, detail = build(row)
        except Exception as e:  # one broken query must not hide the others
            ok, value, detail = None, None, f"query failed: {str(e)[:200]}"
        signals.append({"id": f"signal:{sid}", "ok": ok, "value": value, "detail": detail})

    add("extraction_backlog_age", f"""
        SELECT COUNT(*) AS pending,
               SUM(hc.timestamp < NOW() - INTERVAL {BACKLOG_STALE_DAYS} DAY) AS stale,
               TIMESTAMPDIFF(DAY, MIN(hc.timestamp), NOW()) AS oldest_days
        FROM researcher_urls ru JOIN html_content hc ON hc.url_id = ru.id
        WHERE ru.is_active = TRUE AND hc.content_hash IS NOT NULL AND hc.has_stored_content = TRUE
          AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)""",
        lambda r: (int(r.get("stale") or 0) <= BACKLOG_STALE_MAX, int(r.get("stale") or 0),
                   f"{int(r.get('pending') or 0):,} pages pending extraction, "
                   f"{int(r.get('stale') or 0):,} for over {BACKLOG_STALE_DAYS} days "
                   f"(oldest {r.get('oldest_days')} days; limit {BACKLOG_STALE_MAX:,})"))
    add("scrape_log_zombies", f"""
        SELECT COUNT(*) AS zombies FROM scrape_log
        WHERE status = 'running' AND started_at < NOW() - INTERVAL {ZOMBIE_MIN_HOURS} HOUR
          AND id < (SELECT MAX(id) FROM scrape_log WHERE status = 'running')""",
        lambda r: (int(r.get("zombies") or 0) == 0, int(r.get("zombies") or 0),
                   f"{int(r.get('zombies') or 0)} scrape_log row(s) left 'running' behind a newer run "
                   f"for over {ZOMBIE_MIN_HOURS}h (the hourly sweeper should mark them failed)"))
    add("scrape_completed_recent", """
        SELECT TIMESTAMPDIFF(HOUR, MAX(finished_at), NOW()) AS hours,
               (SELECT COUNT(*) FROM scrape_log
                  WHERE status = 'failed' AND started_at >= NOW() - INTERVAL 7 DAY) AS failed_7d
        FROM scrape_log WHERE status = 'completed'""",
        lambda r: (r.get("hours") is not None and int(r["hours"]) <= COMPLETED_SCRAPE_MAX_HOURS,
                   r.get("hours"),
                   (f"last completed scrape finished {r['hours']}h ago" if r.get("hours") is not None
                    else "no scrape has ever completed")
                   + f"; {int(r.get('failed_7d') or 0)} failed in the last 7 days "
                   f"(limit {COMPLETED_SCRAPE_MAX_HOURS}h)"))
    add("llm_extraction_24h", """
        SELECT COUNT(*) AS calls, COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM llm_usage
        WHERE called_at >= NOW() - INTERVAL 24 HOUR AND call_type = 'publication_extraction'""",
        lambda r: (None, int(r.get("calls") or 0),
                   f"{int(r.get('calls') or 0):,} successful extraction calls in 24h "
                   f"(${float(r.get('cost') or 0):.2f}); rejected calls are alerted by the monitor"))
    return signals


def run_checks(out: Path) -> int:
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    import pytest

    # conftest.py imports backend.config, which reads the environment. Load it
    # first and make every pooled connection read-only before any test runs.
    sys.path.insert(0, str(PROJECT_ROOT / SUITE))
    import conftest  # noqa: F401  (sets placeholder env vars, loads .env)
    from backend.config import db_config

    db_config["init_command"] = READ_ONLY_INIT

    collector = make_collector()
    exit_code = pytest.main(
        [SUITE, "-q", "-p", "no:cacheprovider", "--no-header", "--tb=short"],
        plugins=[collector],
    )
    invariants = sorted(collector.items.values(), key=lambda i: i["id"])
    totals = {k: sum(1 for i in invariants if i["outcome"] == k)
              for k in ("passed", "failed", "error", "skipped")}
    complete = totals["passed"] + totals["failed"] > 0
    error = None
    if not complete:
        reasons = {i["message"] for i in invariants if i["outcome"] == "skipped"}
        error = "; ".join(sorted(reasons))[:500] or f"pytest exited with {int(exit_code)}"

    signals: list[dict] = []
    if complete:
        from backend.database.connection import fetch_one

        signals = collect_signals(fetch_one)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "complete": complete,
        "error": error,
        "totals": totals,
        "invariants": invariants,
        "signals": signals,
    }
    out.write_text(json.dumps(result, indent=2, default=str))
    print(f"\n{totals} complete={complete} -> {out}")
    return 0 if complete else 1


# --- shared helpers for plan and report --------------------------------------

def load_results(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict) or "invariants" not in data:
            raise ValueError("not a results file")
        return data
    except (OSError, ValueError) as e:
        return {"complete": False, "error": f"no usable results ({e})", "totals": {},
                "invariants": [], "signals": []}


def failures(results: dict) -> list[dict]:
    """Failing invariants and failing signals, in one shape."""
    out = []
    for inv in results.get("invariants", []):
        if inv["outcome"] in ("failed", "error"):
            out.append({"id": inv["id"], "kind": "invariant", "outcome": inv["outcome"],
                        "doc": inv.get("doc", ""), "message": inv.get("message", ""),
                        "count": inv.get("count"), "examples": inv.get("examples", [])})
    for sig in results.get("signals", []):
        if sig.get("ok") is False:
            out.append({"id": sig["id"], "kind": "signal", "outcome": "failed", "doc": "",
                        "message": sig["detail"], "count": None, "examples": []})
    return out


def passed_ids(results: dict) -> set[str]:
    ids = {i["id"] for i in results.get("invariants", []) if i["outcome"] == "passed"}
    ids |= {s["id"] for s in results.get("signals", []) if s.get("ok") is True}
    return ids


def short_id(node_id: str) -> str:
    return node_id.removeprefix(f"{SUITE}/")


def defuse_mentions(text: str) -> str:
    """Wrap @names in backticks so nobody is notified; leave code and emails alone."""
    parts = re.split(r"(```.*?```|`[^`\n]*`)", text or "", flags=re.S)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"(?<![\w`])@([A-Za-z0-9][\w-]*(?:/[\w.-]+)?)", r"`@\1`", parts[i])
    return "".join(parts)


def clean_text(text: str, limit: int = MAX_BODY_CHARS) -> str:
    text = defuse_mentions(str(text or "")).strip()
    return text if len(text) <= limit else text[:limit] + "\n\n…(truncated)"


def read_state(body: str) -> dict:
    match = STATE_RE.search(body or "")
    try:
        state = json.loads(match.group(1)) if match else {}
    except ValueError:
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("issue_map", {})
    state.setdefault("pass_days", {})
    # invariant -> {issue, fix_pr, cleanup, since}: the fix is merged and the
    # invariant fails only until a one-off cleanup (or the next scrape) runs.
    state.setdefault("cleanup_pending", {})
    return state


def today(results: dict) -> str:
    stamp = results.get("generated_at") or datetime.now(timezone.utc).isoformat()
    return stamp[:10]


class GitHub:
    """Thin `gh` wrapper. Writes are printed instead of sent in dry-run mode;
    offline mode also answers every read with nothing (for local testing)."""

    def __init__(self, dry_run: bool = False, offline: bool = False):
        self.dry_run = dry_run or offline
        self.offline = offline
        self.writes: list[list[str]] = []
        self._issue_cache: dict[int, dict | None] = {}

    def _gh(self, *args: str) -> str:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
        return result.stdout

    def read(self, *args: str) -> str:
        return "" if self.offline else self._gh(*args)

    def write(self, *args: str) -> str:
        self.writes.append(list(args))
        if self.dry_run:
            print("[dry-run] gh " + " ".join(a if len(a) < 80 else a[:77] + "..." for a in args))
            return "https://github.com/dry-run/issues/0\n"
        return self._gh(*args)

    def report_issue(self) -> dict | None:
        out = self.read("issue", "list", "--state", "open", "--search", f'"{REPORT_TITLE}" in:title',
                        "--json", "number,title,body", "--limit", "20")
        for issue in json.loads(out or "[]"):
            if REPORT_MARKER in (issue.get("body") or ""):
                return issue
        return None

    def pr_merged(self, number: int) -> bool:
        try:
            out = self.read("pr", "view", str(number), "--json", "state")
            return bool(out) and json.loads(out).get("state") == "MERGED"
        except (RuntimeError, ValueError):
            return False

    def issue(self, number: int) -> dict | None:
        if number not in self._issue_cache:
            try:
                out = self.read("issue", "view", str(number), "--json", "number,state,title")
                self._issue_cache[number] = json.loads(out) if out else None
            except (RuntimeError, ValueError):
                self._issue_cache[number] = None
        return self._issue_cache[number]


def untracked_failures(fails: list[dict], state: dict, gh: GitHub) -> list[dict]:
    """Failures with no open issue tracking them and no cleanup pending; each
    notes any closed issue it had."""
    state.setdefault("cleanup_pending", {})
    out = []
    for f in fails:
        if f["id"] in state["cleanup_pending"]:
            continue
        number = state["issue_map"].get(f["id"])
        issue = gh.issue(int(number)) if number else None
        if issue and issue.get("state") == "OPEN":
            continue
        out.append({**f, "previous_issue": number if issue else None,
                    "previous_issue_state": issue.get("state") if issue else None})
    return out


# --- plan --------------------------------------------------------------------

def plan(results_path: str, out_dir: str, gh: GitHub) -> int:
    results = load_results(results_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    needs = False
    if results.get("complete"):
        issue = gh.report_issue()
        state = read_state(issue["body"] if issue else "")
        fails = failures(results)
        untracked = untracked_failures(fails, state, gh)
        needs = bool(untracked)
        tracked = [{"id": f["id"], "issue": state["issue_map"][f["id"]], "count": f["count"]}
                   for f in fails if f not in untracked and f["id"] in state["issue_map"]]
        triage_input = {
            "date": today(results),
            "max_new_issues": MAX_NEW_ISSUES,
            "report_issue": issue["number"] if issue else None,
            "untracked_failures": untracked,
            "already_tracked": tracked,
            "totals": results.get("totals", {}),
        }
        (out / "triage_input.json").write_text(json.dumps(triage_input, indent=2))
        print(f"{len(fails)} failing, {len(untracked)} not tracked by an open issue")
    else:
        print(f"check incomplete: {results.get('error')}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"needs_triage={'true' if needs else 'false'}\n")
    print(f"needs_triage={needs}")
    return 0


# --- report ------------------------------------------------------------------

def load_triage(path: str | None) -> dict:
    if not path:
        return {}
    try:
        text = Path(path).read_text().strip()
        data = json.loads(text) if text else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        print(f"ignoring triage file {path}: {e}")
        return {}


def footer(invariant_ids: list[str], by_id: dict[str, dict], day: str) -> str:
    lines = [f"Failing in the daily data-quality check on {day}:"]
    for iid in invariant_ids:
        f = by_id[iid]
        count = f" ({f['count']:,} rows)" if isinstance(f.get("count"), int) else ""
        examples = f", e.g. {', '.join(f['examples'][:3])}" if f.get("examples") else ""
        lines.append(f"- `{short_id(iid)}`{count}{examples}")
    lines.append(f"<!-- dq-invariants: {json.dumps(invariant_ids)} -->")
    return "\n".join(lines)


def apply_triage(triage: dict, fails: list[dict], state: dict, gh: GitHub,
                 day: str, run_url: str) -> list[str]:
    """Carry out Claude's proposals within the limits. Returns log lines."""
    by_id = {f["id"]: f for f in fails}
    log: list[str] = []
    created = 0
    run_link = f"\n\n[Data-quality run]({run_url})" if run_url else ""
    for group in triage.get("groups", []) if isinstance(triage.get("groups"), list) else []:
        if not isinstance(group, dict):
            continue
        ids = [i for i in group.get("invariants", []) if isinstance(i, str) and i in by_id]
        if not ids:
            continue
        action = group.get("action")
        fix_pr = group.get("fix_pr")
        fix_line = (f"\n\nA fix is proposed in #{int(fix_pr)}; its description should say "
                    f"`Fixes #<this issue>` so merging closes this issue." if isinstance(fix_pr, int) else "")
        text = clean_text(group.get("body", ""))
        number = group.get("issue")
        try:
            if action == "create":
                if created >= MAX_NEW_ISSUES:
                    log.append(f"not filed (limit of {MAX_NEW_ISSUES} new issues per run): "
                               f"{clean_text(group.get('title', ''), 120)}")
                    continue
                title = clean_text(group.get("title") or f"Data quality: {short_id(ids[0])}", 120)
                labels = ["bug"] + (["high-priority"] if group.get("high_priority") is True else [])
                url = gh.write("issue", "create", "--title", title,
                               "--body", f"{text}{fix_line}\n\n{footer(ids, by_id, day)}{run_link}",
                               *sum((["--label", lab] for lab in labels if lab in ALLOWED_LABELS), []))
                created += 1
                number = int(url.strip().rsplit("/", 1)[-1])
                log.append(f"filed #{number}: {title}")
            elif action == "cleanup_pending" and isinstance(number, int) and isinstance(fix_pr, int):
                # Fix merged, existing rows not yet cleaned up: one line in the
                # daily report instead of a new bug or a reopened issue.
                cleanup = clean_text(str(group.get("cleanup") or "").replace("`", "'"), 300)
                if not cleanup:
                    log.append(f"skipped cleanup note for #{number}: no cleanup step given")
                    continue
                if gh.issue(number) is None:
                    log.append(f"skipped: issue #{number} not found")
                    continue
                if not gh.pr_merged(fix_pr):
                    log.append(f"skipped cleanup note for #{number}: fix #{fix_pr} is not merged")
                    continue
                for iid in ids:
                    state["cleanup_pending"][iid] = {"issue": number, "fix_pr": fix_pr,
                                                     "cleanup": cleanup, "since": day}
                log.append(f"cleanup pending for #{number} (fix #{fix_pr} merged)")
            elif action in ("comment", "none") and isinstance(number, int):
                issue = gh.issue(number)
                if issue is None:
                    log.append(f"skipped: issue #{number} not found")
                    continue
                if action == "comment":
                    if issue.get("state") != "OPEN":
                        if group.get("reopen") is not True:
                            log.append(f"skipped: #{number} is closed and reopening was not proposed")
                            continue
                        gh.write("issue", "reopen", str(number))
                        log.append(f"reopened #{number}")
                    gh.write("issue", "comment", str(number),
                             "--body", f"{text}{fix_line}\n\n{footer(ids, by_id, day)}{run_link}")
                    log.append(f"commented on #{number}")
                elif issue.get("state") != "OPEN":
                    log.append(f"skipped: #{number} is closed")
                    continue
                else:
                    log.append(f"tracked by #{number}")
            else:
                log.append(f"skipped malformed proposal for {', '.join(short_id(i) for i in ids)}")
                continue
        except RuntimeError as e:
            log.append(f"failed to update GitHub for {', '.join(short_id(i) for i in ids)}: {e}")
            continue
        for iid in ids:
            state["issue_map"][iid] = number
            state["pass_days"][iid] = []
    return log


def update_streaks(state: dict, results: dict, day: str) -> None:
    """Days on which each tracked invariant passed in an unbroken run of checks."""
    state.setdefault("cleanup_pending", {})
    failing = {f["id"] for f in failures(results)}
    passed = passed_ids(results)
    for iid in list(state["issue_map"]):
        days = state["pass_days"].get(iid, [])
        if iid in failing:
            days = []
        elif iid in passed and day not in days:
            days = (days + [day])[-PASS_DAYS_TO_CLOSE:]
        state["pass_days"][iid] = days
    # A pending cleanup is done once the invariant passes; if it fails again
    # later, that is a new problem for triage rather than the same cleanup.
    for iid in list(state["cleanup_pending"]):
        if iid in passed:
            state["cleanup_pending"].pop(iid)


def close_recovered(state: dict, gh: GitHub, run_url: str) -> list[str]:
    """Close issues whose tracked invariants all passed on two consecutive days."""
    state.setdefault("cleanup_pending", {})
    log: list[str] = []
    by_issue: dict[int, list[str]] = {}
    for iid, number in state["issue_map"].items():
        by_issue.setdefault(int(number), []).append(iid)
    for number, ids in sorted(by_issue.items()):
        if not all(len(state["pass_days"].get(i, [])) >= PASS_DAYS_TO_CLOSE for i in ids):
            continue
        issue = gh.issue(number)
        days = sorted({d for i in ids for d in state["pass_days"][i]})
        try:
            if issue and issue.get("state") == "OPEN":
                names = ", ".join(f"`{short_id(i)}`" for i in ids)
                link = f" [Data-quality run]({run_url})" if run_url else ""
                gh.write("issue", "comment", str(number), "--body",
                         f"The data-quality check for this issue passed on {PASS_DAYS_TO_CLOSE} "
                         f"consecutive daily runs ({', '.join(days)}): {names}. Closing; the daily "
                         f"check will report it again if it comes back.{link}")
                gh.write("issue", "close", str(number), "--reason", "completed")
                log.append(f"closed #{number} (passed {', '.join(days)})")
        except RuntimeError as e:
            log.append(f"could not close #{number}: {e}")
            continue
        for iid in ids:
            state["issue_map"].pop(iid, None)
            state["pass_days"].pop(iid, None)
            state["cleanup_pending"].pop(iid, None)
    return log


def fmt_count(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "–"


def summary_comment(results: dict, state: dict, triage: dict, log: list[str], run_url: str) -> str:
    state.setdefault("cleanup_pending", {})
    day = today(results)
    link = f" [Run]({run_url})" if run_url else ""
    if not results.get("complete"):
        return (f"**Data-quality check {day}: could not run.** {clean_text(results.get('error'), 500)}"
                f"\n\nNo issue was opened, updated or closed.{link}")
    t = results.get("totals", {})
    fails = failures(results)
    lines = [f"**Data-quality check {day}**: {t.get('passed', 0)} passed, {t.get('failed', 0)} failed, "
             f"{t.get('error', 0)} errors, {t.get('skipped', 0)} skipped.{link}", ""]
    if fails:
        lines += ["| Check | Rows | Examples or detail | Issue |", "|---|---|---|---|"]
        for f in fails:
            number = state["issue_map"].get(f["id"])
            detail = ", ".join(f["examples"][:3]) or f["message"].split("\n")[0][:120] or "–"
            where = f"#{number}" if number is not None else "untracked"
            if f["id"] in state["cleanup_pending"]:
                where = f"#{state['cleanup_pending'][f['id']]['issue']} (cleanup pending)"
            lines.append(f"| `{short_id(f['id'])}` | {fmt_count(f['count'])} | "
                         f"{detail.replace('|', '/')} | {where} |")
        lines.append("")
        failing_ids = {f["id"] for f in fails}
        for iid, c in sorted(state["cleanup_pending"].items()):
            if iid in failing_ids:
                lines.append(f"- Cleanup pending since {c['since']}: `{short_id(iid)}` (#{c['issue']}, "
                             f"fix #{c['fix_pr']} merged). Run `{c['cleanup']}`.")
        if any(iid in failing_ids for iid in state["cleanup_pending"]):
            lines.append("")
    else:
        lines += ["Every invariant passes.", ""]
    signals = [s for s in results.get("signals", []) if s.get("ok") is not False]
    if signals:
        lines.append("Pipeline: " + "; ".join(s["detail"] for s in signals) + ".")
        lines.append("")
    if triage.get("summary"):
        lines += [f"Triage: {clean_text(triage['summary'], 1500)}", ""]
    if log:
        lines += ["Actions: " + "; ".join(log) + ".", ""]
    return clean_text("\n".join(lines).strip(), 60000)


def report_body(state: dict) -> str:
    return "\n".join([
        REPORT_MARKER,
        "The daily data-quality check (`.github/workflows/data-quality.yml`) posts one "
        "comment here per run. Failing invariants are filed as `bug` issues or added to "
        "existing ones; an issue closes itself after its invariants pass on two "
        "consecutive days.",
        "",
        "Do not edit the state line below; the check keeps it up to date.",
        f"<!-- dq-state: {json.dumps(state, sort_keys=True)} -->",
    ])


def report(results_path: str, triage_path: str | None, gh: GitHub) -> int:
    results = load_results(results_path)
    triage = load_triage(triage_path)
    run_url = os.environ.get("DQ_RUN_URL", "")
    day = today(results)
    issue = gh.report_issue()
    state = read_state(issue["body"] if issue else "")
    log: list[str] = []
    if results.get("complete"):
        fails = failures(results)
        if triage:
            untracked_ids = {f["id"] for f in untracked_failures(fails, state, gh)}
            log += apply_triage(triage, [f for f in fails if f["id"] in untracked_ids],
                                state, gh, day, run_url)
        update_streaks(state, results, day)
        log += close_recovered(state, gh, run_url)
    comment = summary_comment(results, state, triage, log, run_url)
    print(comment)
    body = report_body(state)
    if issue:
        gh.write("issue", "edit", str(issue["number"]), "--body", body)
        number = issue["number"]
    else:
        url = gh.write("issue", "create", "--title", REPORT_TITLE, "--body", body)
        number = url.strip().rsplit("/", 1)[-1]
    gh.write("issue", "comment", str(number), "--body", comment)
    if not results.get("complete"):
        print(f"::error::data-quality check could not run: {results.get('error')}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run the suite and the signals")
    p_run.add_argument("--out", default="results.json")
    p_plan = sub.add_parser("plan", help="decide whether Claude must triage")
    p_plan.add_argument("results")
    p_plan.add_argument("--out-dir", default=".")
    p_rep = sub.add_parser("report", help="update GitHub issues")
    p_rep.add_argument("results")
    p_rep.add_argument("--triage")
    for p in (p_plan, p_rep):
        p.add_argument("--dry-run", action="store_true", help="print GitHub writes instead")
        p.add_argument("--offline", action="store_true", help="no GitHub at all (local testing)")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run_checks(Path(args.out).resolve())
    gh = GitHub(dry_run=args.dry_run, offline=args.offline)
    if args.cmd == "plan":
        return plan(args.results, args.out_dir, gh)
    return report(args.results, args.triage, gh)


if __name__ == "__main__":
    sys.exit(main())
