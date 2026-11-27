#!/usr/bin/env python3
"""Production health checks that raise an alert when something breaks.

Standard library only: the daily Monitor workflow streams this file to the
server's system Python over SSH, so it cannot import the backend package.

Modes:
  --external     probe the public API, the frontend and the TLS certificate
                 from wherever the script runs (the GitHub runner)
  --host         inspect containers, disk, backups and pipeline liveness on
                 the production server
  --report F...  merge JSON results and open, update or close the alert issue

Alerting goes through one GitHub issue. A new failure first asks Claude to fix
it with an @claude comment; the owner is only mentioned (which is what sends
the email) when the checks still fail at the next daily run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = os.environ.get("MONITOR_API_URL", "https://econ-newsfeed.duckdns.org")
FRONTEND_URL = os.environ.get("MONITOR_FRONTEND_URL", "https://econ-newsfeed.vercel.app")
LOCAL_API_URL = "http://localhost:8000"
API_CONTAINER = "econ-newsfeed-api-1"
DB_CONTAINER = "econ-newsfeed-db-1"
BACKUP_DIR = Path("/backups")
ALERT_LABEL = "monitor-alert"

TLS_MIN_DAYS = 10                 # Caddy renews with ~30 days left
DISK_MAX_PERCENT = 90
BACKUP_MAX_AGE_HOURS = 30         # daily at 03:00 UTC
BACKUP_MIN_BYTES = 100 * 1024 * 1024
SCRAPE_MAX_AGE_HOURS = 36         # daily scrape
STUCK_SCRAPE_HOURS = 24
# The 2026-11 billing outage still completed 1-3 pages an hour, so a "last
# success" age check stayed green for ten days. Compare throughput to backlog.
EXTRACTION_BACKLOG_PAGES = 1000
EXTRACTION_MIN_PER_6H = 30
# The worker takes changed pages that waited 12h before anything else
# (backend.database.extraction_queue), so a changed page waiting three days
# means extraction is not keeping up with the pages that feed the newsfeed.
EXTRACTION_CHANGED_MAX_WAIT_HOURS = 72
# A few pages that always fail must not keep the alert on; a real stall leaves
# hundreds of changed pages behind.
EXTRACTION_STALE_CHANGED_MAX = 20
FEED_WINDOW_HOURS = 72
ENRICHMENT_MAX_AGE_HOURS = 48
LLM_ACCOUNT_ERRORS_PER_HOUR = 3
# API scrape protection (backend/api_protection.py), over the last 24 hours.
API_RATE_LIMITED_PER_DAY = 2000   # 429s; a burst from one scraper is expected, a flood is not
API_INVALID_KEY_WARNINGS = 12     # logged at most every 5 min per worker: the frontend key is wrong

# Claude gets until the next daily run; a same-day manual rerun must not
# escalate before a fix could have been merged and deployed.
ESCALATE_AFTER_HOURS = 20
CLAUDE_REQUEST = (
    "@claude The production monitor found the failing checks listed in this issue. "
    "Find the cause and, if a change to this repository can fix it, make the fix on a "
    "branch and reply with the pull request link; merging to main deploys the backend. "
    "If it cannot be fixed from this repository (for example billing, disk space or an "
    "unreachable server), say what the owner needs to do instead."
)

PIPELINE_COLUMNS = (
    "scrape_age_h", "stuck_scrapes", "pending", "extracted_6h", "feed_recent", "enrich_age_h",
    "snapshot_guard", "never_extracted", "oldest_changed_h", "stale_changed",
)
# Only idx_extraction_state columns: any other html_content column in these
# filters makes MySQL read every stored page (~60 s on production).
_PENDING = """FROM researcher_urls ru JOIN html_content hc ON hc.url_id = ru.id
     WHERE ru.is_active = TRUE AND hc.content_hash IS NOT NULL AND hc.has_stored_content = TRUE
       AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)"""
# Wait of the oldest changed (previously extracted) page: index-only list
# first, then row lookups for those few hundred pages. content_changed_at is
# NULL on rows last changed before it existed; their last fetch stands in.
_CHANGED = f"""FROM (SELECT hc.url_id {_PENDING} AND hc.extracted_hash IS NOT NULL) p
     JOIN html_content h2 ON h2.url_id = p.url_id"""
_OLDEST_CHANGED = f"""SELECT /*+ NO_MERGE(p) */
       TIMESTAMPDIFF(HOUR, MIN(COALESCE(h2.content_changed_at, h2.timestamp)), NOW()) {_CHANGED}"""
_STALE_CHANGED = f"""SELECT /*+ NO_MERGE(p) */
       COUNT(*) {_CHANGED}
     WHERE COALESCE(h2.content_changed_at, h2.timestamp)
           < NOW() - INTERVAL {EXTRACTION_CHANGED_MAX_WAIT_HOURS} HOUR
       -- Pages that failed 3+ times on this version sit in the worker's
       -- persisted backoff: a poisoned page, not a stalled worker.
       AND NOT (h2.extraction_failed_hash <=> h2.content_hash AND h2.extraction_failures >= 3)"""
PIPELINE_SQL = f"""
SELECT
  TIMESTAMPDIFF(HOUR, (SELECT MAX(started_at) FROM scrape_log), NOW()),
  (SELECT COUNT(*) FROM scrape_log
     WHERE status = 'running' AND started_at < NOW() - INTERVAL {STUCK_SCRAPE_HOURS} HOUR),
  (SELECT COUNT(*) {_PENDING}),
  (SELECT COUNT(*) FROM html_content WHERE extracted_at >= NOW() - INTERVAL 6 HOUR),
  (SELECT COUNT(*) FROM feed_events WHERE recorded_at >= NOW() - INTERVAL {FEED_WINDOW_HOURS} HOUR),
  TIMESTAMPDIFF(HOUR, (SELECT MAX(enrichment_attempted_at) FROM papers), NOW()),
  (SELECT COUNT(*) FROM information_schema.TRIGGERS
     WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = 'trg_feed_events_snapshot_guard'),
  (SELECT COUNT(*) {_PENDING} AND hc.extracted_hash IS NULL),
  ({_OLDEST_CHANGED}),
  ({_STALE_CHANGED});
"""


def check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


# --- external probes ---------------------------------------------------------

def http_get(url: str, attempts: int = 3, pause: float = 10.0) -> tuple[int | None, bytes]:
    """Return (status, body); status is None when the host never answered."""
    status, body = None, b""
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": "econ-newsfeed-monitor"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, b""
        except (OSError, ValueError):
            status, body = None, b""
        if attempt + 1 < attempts:
            time.sleep(pause)
    return status, body


def http_check(name: str, url: str) -> dict:
    status, _ = http_get(url)
    return check(name, status == 200, f"{url} returned {status or 'no response'}")


def proxy_check() -> dict:
    """The frontend must reach the backend through its /api rewrite."""
    url = f"{FRONTEND_URL}/api/publications?per_page=1"
    status, body = http_get(url)
    try:
        has_items = bool(json.loads(body).get("items"))
    except (ValueError, AttributeError):
        has_items = False
    return check("frontend_api_proxy", status == 200 and has_items,
                 f"{url} returned {status or 'no response'}"
                 + ("" if has_items or status != 200 else " without publications"))


def tls_check(now: float | None = None) -> dict:
    host = API_URL.split("://", 1)[-1].split("/", 1)[0]
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=20) as raw, \
                context.wrap_socket(raw, server_hostname=host) as tls:
            not_after = tls.getpeercert()["notAfter"]
        days = (ssl.cert_time_to_seconds(not_after) - (now or time.time())) / 86400
    except (OSError, KeyError, ValueError) as e:
        return check("tls_certificate", False, f"could not read the certificate for {host}: {e}")
    return check("tls_certificate", days >= TLS_MIN_DAYS, f"{host} certificate expires in {days:.0f} days")


def external_checks() -> list[dict]:
    return [
        http_check("public_api", f"{API_URL}/api/ready"),
        http_check("frontend", f"{FRONTEND_URL}/"),
        proxy_check(),
        tls_check(),
    ]


# --- server checks -----------------------------------------------------------

def run(cmd: list[str], stdin: str | None = None, timeout: int = 120,
        env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, env=env)


def container_check(container: str) -> dict:
    name = f"container_{container.removeprefix('econ-newsfeed-').removesuffix('-1')}"
    try:
        result = run(["docker", "inspect", "-f", "{{.State.Running}}", container])
        running = result.returncode == 0 and result.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        running = False
    return check(name, running, f"{container} is {'running' if running else 'not running'}")


def disk_check(path: str = "/") -> dict:
    usage = shutil.disk_usage(path)
    percent = usage.used / usage.total * 100
    free_gb = usage.free / 1024 ** 3
    return check("disk_space", percent < DISK_MAX_PERCENT,
                 f"{path} is {percent:.0f}% full ({free_gb:.1f} GB free)")


def backup_check(backup_dir: Path = BACKUP_DIR, now: float | None = None) -> dict:
    now = now or time.time()
    dumps = sorted(backup_dir.glob("econ_newsfeed_*.sql.gz"), key=lambda p: p.stat().st_mtime)
    if not dumps:
        return check("backup", False, f"no backup files in {backup_dir}")
    newest = dumps[-1]
    age_hours = (now - newest.stat().st_mtime) / 3600
    size = newest.stat().st_size
    if age_hours > BACKUP_MAX_AGE_HOURS:
        return check("backup", False, f"newest backup is {age_hours:.0f}h old")
    if size < BACKUP_MIN_BYTES:
        return check("backup", False, f"newest backup is only {size / 1024 ** 2:.0f} MB")
    log = backup_dir / "backup.log"
    if log.exists():
        lines = log.read_text(errors="replace").splitlines()[-200:]
        starts = [i for i, line in enumerate(lines) if "Starting backup" in line]
        last_run = lines[starts[-1]:] if starts else lines
        errors = [line for line in last_run if "ERROR" in line]
        if errors:
            return check("backup", False, f"last backup run logged: {errors[-1][:200]}")
    return check("backup", True, f"newest backup is {age_hours:.0f}h old, {size / 1024 ** 3:.1f} GB")


def parse_pipeline_row(output: str) -> dict:
    """Parse one tab-separated `mysql -N -B` row into named integers."""
    fields = output.strip().splitlines()[-1].split("\t")
    if len(fields) != len(PIPELINE_COLUMNS):
        raise ValueError(f"expected {len(PIPELINE_COLUMNS)} columns, got {len(fields)}")
    return {name: None if value == "NULL" else int(value)
            for name, value in zip(PIPELINE_COLUMNS, fields)}


def evaluate_pipeline(stats: dict) -> list[dict]:
    scrape_age = stats["scrape_age_h"]
    enrich_age = stats["enrich_age_h"]
    changed_wait = stats["oldest_changed_h"]
    stalled = (stats["pending"] > EXTRACTION_BACKLOG_PAGES
               and stats["extracted_6h"] < EXTRACTION_MIN_PER_6H)
    return [
        check("scrape_recent", scrape_age is not None and scrape_age <= SCRAPE_MAX_AGE_HOURS,
              f"last scrape started {scrape_age}h ago" if scrape_age is not None
              else "no scrape has ever started"),
        check("scrape_not_stuck", stats["stuck_scrapes"] == 0,
              f"{stats['stuck_scrapes']} scrape(s) running for over {STUCK_SCRAPE_HOURS}h"),
        check("extraction_throughput", not stalled,
              f"{stats['pending']} pages waiting ({stats['never_extracted']} never extracted), "
              f"{stats['extracted_6h']} extracted in the last 6h, "
              + (f"clears in ~{stats['pending'] / (stats['extracted_6h'] * 4):.1f} days at that rate"
                 if stats["extracted_6h"] else "no extraction in 6h")),
        check("extraction_backlog_age", stats["stale_changed"] <= EXTRACTION_STALE_CHANGED_MAX,
              f"{stats['stale_changed']} changed pages waiting over {EXTRACTION_CHANGED_MAX_WAIT_HOURS}h"
              + (f", oldest {changed_wait}h" if changed_wait is not None else "")),
        check("feed_fresh", stats["feed_recent"] > 0,
              f"{stats['feed_recent']} feed events in the last {FEED_WINDOW_HOURS}h"),
        check("enrichment_active", enrich_age is not None and enrich_age <= ENRICHMENT_MAX_AGE_HOURS,
              f"last enrichment attempt {enrich_age}h ago" if enrich_age is not None
              else "no enrichment attempt recorded"),
        # The daily data-quality check uses a SELECT-only user, which MySQL
        # does not show triggers to, so the trigger is checked here as root.
        check("snapshot_guard_trigger", stats["snapshot_guard"] == 1,
              "feed_events snapshot-guard trigger "
              + ("exists" if stats["snapshot_guard"] == 1 else "is missing (see PR #158)")),
    ]


def pipeline_checks() -> list[dict]:
    mysql = 'exec mysql -N -B -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
    try:
        result = run(["docker", "exec", "-i", DB_CONTAINER, "sh", "-c", mysql], stdin=PIPELINE_SQL)
        if result.returncode != 0:
            raise ValueError("mysql exited with an error")
        stats = parse_pipeline_row(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        return [check("database_query", False, f"could not read pipeline state: {e}")]
    return evaluate_pipeline(stats)


def count_account_errors(log_text: str) -> int:
    """Count LLM calls the provider rejected for billing or credentials."""
    return len(re.findall(r'chat/completions "HTTP/1\.1 40[123] ', log_text))


def llm_provider_check() -> dict:
    try:
        result = run(["docker", "logs", "--since", "1h", API_CONTAINER])
    except (OSError, subprocess.SubprocessError) as e:
        return check("llm_provider", False, f"could not read API logs: {e}")
    rejected = count_account_errors(result.stdout + result.stderr)
    return check("llm_provider", rejected < LLM_ACCOUNT_ERRORS_PER_HOUR,
                 f"{rejected} LLM calls rejected for billing or credentials in the last hour")


RATE_LIMITED_RE = re.compile(r"rate_limited ip=(\S+)")


def summarize_api_traffic(lines) -> dict:
    """Count 429s (per IP), blocked requests, bad internal keys and API requests."""
    stats = {"rate_limited": 0, "blocked": 0, "invalid_key": 0, "requests": 0, "by_ip": {}}
    for line in lines:
        match = RATE_LIMITED_RE.search(line)
        if match:
            stats["rate_limited"] += 1
            ip = match.group(1)
            stats["by_ip"][ip] = stats["by_ip"].get(ip, 0) + 1
        elif "api_blocked reason=" in line:
            stats["blocked"] += 1
        elif "internal_key_invalid" in line:
            stats["invalid_key"] += 1
        elif '"GET /api/' in line:
            stats["requests"] += 1
    return stats


def evaluate_api_traffic(stats: dict) -> dict:
    top = sorted(stats["by_ip"].items(), key=lambda kv: -kv[1])[:3]
    top_text = ", ".join(f"{ip} x{n}" for ip, n in top) or "none"
    detail = (
        f"{stats['rate_limited']} rate-limited (top: {top_text}), {stats['blocked']} blocked, "
        f"{stats['invalid_key']} wrong-internal-key warnings, {stats['requests']} API GETs in 24h"
    )
    ok = (stats["rate_limited"] < API_RATE_LIMITED_PER_DAY
          and stats["invalid_key"] < API_INVALID_KEY_WARNINGS)
    return check("api_traffic", ok, detail)


def api_traffic_check() -> dict:
    """Stream a day of API logs (they can be large) and summarize scraping signals."""
    try:
        proc = subprocess.Popen(
            ["docker", "logs", "--since", "24h", API_CONTAINER],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        )
        try:
            stats = summarize_api_traffic(proc.stdout)
        finally:
            proc.stdout.close()
            proc.wait(timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return check("api_traffic", False, f"could not read API logs: {e}")
    return evaluate_api_traffic(stats)


def host_checks() -> list[dict]:
    return [
        container_check(API_CONTAINER),
        container_check(DB_CONTAINER),
        http_check("api_ready", f"{LOCAL_API_URL}/api/ready"),
        disk_check(),
        backup_check(),
        *pipeline_checks(),
        llm_provider_check(),
        api_traffic_check(),
    ]


# --- reporting ---------------------------------------------------------------

UNREADABLE = {
    "host": ("server_reachable", "could not run the checks on the server over SSH"),
    "external": ("external_probe", "the outside probe produced no results"),
}


def load_results(paths: list[str]) -> list[dict]:
    """Merge result files; a missing or broken file is itself a failure."""
    results: list[dict] = []
    for path in paths:
        try:
            loaded = json.loads(Path(path).read_text())
            if not isinstance(loaded, list) or not loaded:
                raise ValueError("empty result")
            results.extend(check(r["name"], r["ok"], r["detail"]) for r in loaded)
        except (OSError, ValueError, KeyError, TypeError):
            stem = Path(path).stem
            name, detail = UNREADABLE.get(stem, (f"{stem}_results", f"no results in {path}"))
            results.append(check(name, False, detail))
    return results


def read_state(issue_body: str) -> dict:
    """State the monitor recorded in the issue: failing names, stage, asked_at."""
    match = re.search(r"<!-- monitor-state: (.*?) -->", issue_body or "")
    try:
        state = json.loads(match.group(1)) if match else {}
    except ValueError:
        state = {}
    return state if isinstance(state, dict) else {}


def hours_since(timestamp: str | None, now: datetime) -> float:
    """Hours since an ISO timestamp; unreadable means "long ago" so alerts are never held back."""
    try:
        return (now - datetime.fromisoformat(timestamp)).total_seconds() / 3600
    except (TypeError, ValueError):
        return float("inf")


def plan_issue_action(failing: list[str], open_issue: dict | None,
                      can_ask_claude: bool, now: datetime) -> str:
    """Decide the single step for this run.

    ask_claude  new failure: open the issue and ask Claude to fix it (no email)
    open        new failure and Claude cannot be asked: alert the owner now
    escalate    still failing a day after asking Claude: alert the owner
    update      owner already alerted and the failing set changed
    close       everything passes again
    """
    if not failing:
        return "close" if open_issue else "none"
    if open_issue is None:
        return "ask_claude" if can_ask_claude else "open"
    state = read_state(open_issue.get("body", ""))
    if state.get("stage") == "claude":
        waited = hours_since(state.get("asked_at"), now)
        return "escalate" if waited >= ESCALATE_AFTER_HOURS else "none"
    return "none" if set(state.get("failing", [])) == set(failing) else "update"


def issue_title(failing: list[str]) -> str:
    shown = ", ".join(failing[:3])
    extra = f" (+{len(failing) - 3} more)" if len(failing) > 3 else ""
    return f"Production alert: {shown}{extra}"


def mention(notify: str) -> str:
    return f"@{notify} " if notify else ""


def issue_body(results: list[dict], now: datetime, run_url: str, stage: str,
               asked_at: str | None = None, notify: str = "") -> str:
    """Render the issue. `notify` puts the owner mention in the body (new issues only)."""
    failing = [r for r in results if not r["ok"]]
    passing = [r["name"] for r in results if r["ok"]]
    state = {"failing": [r["name"] for r in failing], "stage": stage, "asked_at": asked_at}
    lines = [
        f"{mention(notify)}Production monitoring found {len(failing)} failing check(s) "
        f"at {now:%Y-%m-%d %H:%M} UTC.",
        "",
        "| Check | Detail |",
        "|---|---|",
        *(f"| `{r['name']}` | {r['detail'].replace('|', '/')} |" for r in failing),
        "",
        f"Passing: {', '.join(passing) or 'none'}",
        "",
    ]
    if stage == "claude":
        lines += ["Claude was asked to fix this first. The owner is alerted if it is "
                  "still failing at the next daily check.", ""]
    lines += [
        "This issue closes itself once every check passes again."
        + (f" [Monitor run]({run_url})" if run_url else ""),
        f"<!-- monitor-state: {json.dumps(state)} -->",
    ]
    return "\n".join(lines)


def gh(*args: str, token: str | None = None) -> str:
    env = {**os.environ, "GH_TOKEN": token} if token else None
    result = run(["gh", *args], env=env)
    if result.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout


def report(paths: list[str], dry_run: bool) -> int:
    results = load_results(paths)
    failing = [r["name"] for r in results if not r["ok"]]
    now = datetime.now(timezone.utc)
    for r in results:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['name']}: {r['detail']}")

    if not dry_run:
        gh("label", "create", ALERT_LABEL, "--force", "--color", "B60205",
           "--description", "Opened by the production monitor")
    issues = json.loads(gh("issue", "list", "--label", ALERT_LABEL, "--state", "open",
                           "--json", "number,body", "--limit", "1"))
    open_issue = issues[0] if issues else None
    # The @claude comment must come from a real user: GitHub starts no workflow
    # for anything posted with the workflow's own token.
    ask_token = os.environ.get("MONITOR_ASK_TOKEN", "")
    action = plan_issue_action(failing, open_issue, bool(ask_token), now)
    print(f"alert issue action: {action}")
    if dry_run or action == "none":
        return 0

    notify = os.environ.get("MONITOR_NOTIFY", "")
    run_url = os.environ.get("MONITOR_RUN_URL", "")
    title = issue_title(failing)
    alerted_body = issue_body(results, now, run_url, "alerted")
    names = ", ".join(failing)

    if action == "open":
        gh("issue", "create", "--title", title, "--label", ALERT_LABEL,
           "--body", issue_body(results, now, run_url, "alerted", notify=notify))
    elif action == "ask_claude":
        url = gh("issue", "create", "--title", title, "--label", ALERT_LABEL,
                 "--body", issue_body(results, now, run_url, "claude", asked_at=now.isoformat()))
        number = url.strip().rsplit("/", 1)[-1]
        try:
            gh("issue", "comment", number, "--body", CLAUDE_REQUEST, token=ask_token)
        except RuntimeError as e:
            # A rejected token must not turn into silence: alert the owner instead.
            print(f"could not ask Claude: {e}")
            gh("issue", "edit", number, "--body", alerted_body)
            gh("issue", "comment", number, "--body",
               f"{mention(notify)}Claude could not be asked to fix this (the "
               f"MONITOR_GH_TOKEN secret was rejected), so this is the alert. Failing: {names}")
    else:
        number = str(open_issue["number"])
        if action == "escalate":
            gh("issue", "edit", number, "--title", title, "--body", alerted_body)
            gh("issue", "comment", number, "--body",
               f"{mention(notify)}Still failing at {now:%Y-%m-%d %H:%M} UTC after Claude was "
               f"asked to fix it: the fix did not work or has not been merged. Failing: {names}")
        elif action == "update":
            gh("issue", "edit", number, "--title", title, "--body", alerted_body)
            gh("issue", "comment", number, "--body",
               f"Failing checks changed at {now:%Y-%m-%d %H:%M} UTC: {names}")
        else:
            # Close without a comment: a comment plus a closure would be two
            # emails, and alerts are capped at one per daily run.
            gh("issue", "close", number, "--reason", "completed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--external", action="store_true", help="probe public endpoints")
    mode.add_argument("--host", action="store_true", help="run on the production server")
    mode.add_argument("--report", nargs="+", metavar="FILE", help="merge results into the alert issue")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("--dry-run", action="store_true", help="with --report: change nothing on GitHub")
    args = parser.parse_args(argv)

    if args.report:
        return report(args.report, args.dry_run)
    results = external_checks() if args.external else host_checks()
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['name']}: {r['detail']}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
