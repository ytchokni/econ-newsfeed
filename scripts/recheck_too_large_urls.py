"""Queue URLs deactivated as response_too_large for a recheck on the next scrape.

Until #283 the fetcher rejected every page over 1 MB. It now streams up to
FETCH_MAX_BYTES and compacts large pages, so most of those URLs fit, but the
scrape job only rechecks them TOO_LARGE_RECHECK_DAYS after deactivation. This
backdates deactivated_at past that window so the next scrape job fetches them:
pages that fit are reactivated, sign-in redirects (retired or private Google
Sites) become login_required, and pages still too large restart their clock.

Dry run by default: prints the URLs that would be queued, by host. --apply writes.

    poetry run python scripts/recheck_too_large_urls.py
    poetry run python scripts/recheck_too_large_urls.py --apply

In production (from /opt/econ-newsfeed), only while the extraction backlog
is small, since pages that now fit are queued for extraction:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api python scripts/recheck_too_large_urls.py
    docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api python scripts/recheck_too_large_urls.py --apply

Use this rather than a hand-written UPDATE of researcher_urls (as in #283's
notes): it touches only response_too_large rows not yet due, and the recheck
goes through the fetcher, which records sign-in redirects as login_required.

--before limits it to URLs deactivated before a timestamp (default: now).
Idempotent: queued rows are already due and are not selected again.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend.database.connection import execute_query, fetch_all  # noqa: E402
from backend.database.researchers import TOO_LARGE_RECHECK_DAYS  # noqa: E402

_PENDING = """is_active = FALSE AND deactivation_reason = 'response_too_large'
              AND deactivated_at >= NOW() - INTERVAL %s DAY
              AND deactivated_at < %s"""


def pending_urls(before: datetime) -> list[dict]:
    """response_too_large URLs not yet due for their recheck."""
    return fetch_all(
        f"SELECT id, url, deactivated_at FROM researcher_urls WHERE {_PENDING} ORDER BY id",
        (TOO_LARGE_RECHECK_DAYS, before),
    )


def queue_recheck(before: datetime) -> None:
    """Backdate deactivated_at one day past the recheck window."""
    execute_query(
        f"""UPDATE researcher_urls
            SET deactivated_at = NOW() - INTERVAL %s DAY
            WHERE {_PENDING}""",
        (TOO_LARGE_RECHECK_DAYS + 1, TOO_LARGE_RECHECK_DAYS, before),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the change")
    parser.add_argument("--before", type=datetime.fromisoformat,
                        help="only URLs deactivated before this UTC timestamp (default: now)")
    args = parser.parse_args(argv)
    before = args.before or datetime.now(timezone.utc).replace(tzinfo=None)

    rows = pending_urls(before)
    hosts = Counter((urlparse(r["url"]).hostname or "?") for r in rows)
    print(f"{len(rows)} response_too_large URLs deactivated before {before:%Y-%m-%d %H:%M} "
          f"and not yet due for a recheck")
    for host, n in hosts.most_common(15):
        print(f"  {n:5d}  {host}")
    if not args.apply:
        print("Dry run; rerun with --apply to queue them for the next scrape job.")
        return 0
    queue_recheck(before)
    left = len(pending_urls(before))
    print(f"Queued {len(rows) - left} URLs; the next scrape job rechecks them.")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
