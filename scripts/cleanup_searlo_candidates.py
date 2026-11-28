"""Repair site_mining_candidates after the first Searlo apply (#299). See backend/discovery/searlo_cleanup.py.

Dry run by default; prints how many rows each step would change.

    poetry run python scripts/cleanup_searlo_candidates.py --dump /backups/econ_newsfeed_20261127_030002.sql.gz
    docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --user root -v /backups:/backups api \
      python scripts/cleanup_searlo_candidates.py --dump /backups/econ_newsfeed_20261127_030002.sql.gz \
      --apply --backup /backups/searlo-cleanup.jsonl
    ... --undo /backups/searlo-cleanup.jsonl [--apply]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.discovery import searlo_cleanup  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dump", help="mysqldump (.sql or .sql.gz) taken before the Searlo apply")
    p.add_argument("--steps", default="restore,rekey", help="comma list of: restore, rekey")
    p.add_argument("--apply", action="store_true", help="write (requires --backup); default is a dry run")
    p.add_argument("--backup", help="JSONL file that records every replaced row")
    p.add_argument("--undo", metavar="BACKUP", help="restore the rows recorded in BACKUP (dry run unless --apply)")
    a = p.parse_args(argv)
    if a.undo:
        result = searlo_cleanup.undo(a.undo, apply=a.apply)
    else:
        result = searlo_cleanup.run(steps=a.steps.split(","), dump_path=a.dump, apply=a.apply, backup_path=a.backup)
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()
