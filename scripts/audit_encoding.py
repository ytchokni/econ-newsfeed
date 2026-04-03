#!/usr/bin/env python3
"""Scan database text fields for mojibake and optionally fix them.

Usage:
    poetry run python scripts/audit_encoding.py          # dry-run: report only
    poetry run python scripts/audit_encoding.py --fix    # apply fixes
"""
import argparse
import csv
import logging
import os
import sys

# Ensure project root is on the path (script lives in scripts/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from encoding_guard import fix_encoding

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Tables and their text columns to scan, plus the primary key column
SCAN_TARGETS = [
    ("papers", "id", ["title", "abstract", "venue"]),
    ("researchers", "id", ["first_name", "last_name", "affiliation", "position", "description"]),
    ("openalex_coauthors", "id", ["display_name"]),
    ("paper_topics", "id", ["topic_name", "subfield_name", "field_name", "domain_name"]),
]


def scan_table(cursor, table: str, pk_col: str, columns: list[str]) -> list[dict]:
    """Scan a table for mojibake. Returns list of findings."""
    findings = []
    cols_sql = ", ".join([pk_col] + columns)
    cursor.execute(f"SELECT {cols_sql} FROM {table}")
    for row in cursor.fetchall():
        row_id = row[pk_col]
        for col in columns:
            value = row[col]
            if not value:
                continue
            fixed, was_changed = fix_encoding(value)
            if was_changed:
                findings.append({
                    "table": table,
                    "column": col,
                    "row_id": row_id,
                    "original": value,
                    "fixed": fixed,
                })
    return findings


def apply_fixes(cursor, findings: list[dict]) -> None:
    """Apply encoding fixes to the database."""
    for f in findings:
        cursor.execute(
            f"UPDATE {f['table']} SET {f['column']} = %s WHERE id = %s",
            (f["fixed"], f["row_id"]),
        )
        logger.warning(
            'Mojibake fixed in %s.%s (id=%s): "%s" → "%s"',
            f["table"], f["column"], f["row_id"], f["original"], f["fixed"],
        )


def main():
    parser = argparse.ArgumentParser(description="Scan and fix mojibake in database text fields.")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (default: dry-run report only)")
    args = parser.parse_args()

    from database.connection import get_connection

    all_findings = []

    with get_connection() as conn:
        with conn.cursor(dictionary=True) as cursor:
            for table, pk_col, columns in SCAN_TARGETS:
                findings = scan_table(cursor, table, pk_col, columns)
                all_findings.extend(findings)

            if not all_findings:
                print("No mojibake found. All text fields are clean.")
                return

            # Report as CSV
            writer = csv.DictWriter(
                sys.stdout,
                fieldnames=["table", "column", "row_id", "original", "fixed"],
            )
            writer.writeheader()
            for f in all_findings:
                writer.writerow(f)

            # Summary
            tables_affected = len(set(f["table"] for f in all_findings))
            rows_affected = len(set((f["table"], f["row_id"]) for f in all_findings))
            print(
                f"\nFound {len(all_findings)} mojibake values "
                f"across {rows_affected} rows in {tables_affected} tables.",
                file=sys.stderr,
            )

            if args.fix:
                apply_fixes(cursor, all_findings)
                conn.commit()
                print(
                    f"Fixed {len(all_findings)} values across {rows_affected} rows.",
                    file=sys.stderr,
                )
            else:
                print(
                    "Run with --fix to apply corrections.",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
