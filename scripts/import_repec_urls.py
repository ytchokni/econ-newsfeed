"""Import researchers with personal websites from local RePEc data.

Parses RePEc RDF files, filters to active economists (last login >= 2025)
with a Homepage field, and imports them into the database. For existing
researchers without URLs, adds the homepage. For new researchers, creates
the researcher record and adds the URL.

Usage:
    poetry run python scripts/import_repec_urls.py [--dry-run] [--since YEAR]
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from database.researchers import add_researcher_url

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

REPEC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "RePEc", "per", "pers",
)


def parse_rdf(path: str) -> dict | None:
    """Parse a RePEc RDF person file into a dict."""
    data = {
        "first_name": None,
        "middle_name": None,
        "last_name": None,
        "homepage": None,
        "workplace": None,
        "workplace_location": None,
        "login": None,
        "deceased": False,
    }
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("Name-First:"):
                data["first_name"] = line.split(":", 1)[1].strip()
            elif line.startswith("Name-Middle:"):
                data["middle_name"] = line.split(":", 1)[1].strip()
            elif line.startswith("Name-Last:"):
                data["last_name"] = line.split(":", 1)[1].strip()
            elif line.startswith("Homepage:"):
                data["homepage"] = line.split(":", 1)[1].strip()
            elif line.startswith("Workplace-Name:") and not data["workplace"]:
                data["workplace"] = line.split(":", 1)[1].strip()
            elif line.startswith("Workplace-Location:") and not data["workplace_location"]:
                data["workplace_location"] = line.split(":", 1)[1].strip()
            elif line.startswith("Last-Login-Date:"):
                data["login"] = line.split(":", 1)[1].strip()
            elif line.startswith("Deceased:"):
                data["deceased"] = True
    return data


def main():
    dry_run = "--dry-run" in sys.argv
    since = "2025"
    for i, arg in enumerate(sys.argv):
        if arg == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1]

    if not os.path.isdir(REPEC_DIR):
        logger.error("RePEc directory not found: %s", REPEC_DIR)
        sys.exit(1)

    # Parse all RDF files
    logger.info("Parsing RePEc RDF files (since %s)...", since)
    candidates = []
    for root, _dirs, files in os.walk(REPEC_DIR):
        for fname in files:
            if not fname.endswith(".rdf"):
                continue
            data = parse_rdf(os.path.join(root, fname))
            if (
                data["homepage"]
                and not data["deceased"]
                and data["login"]
                and data["login"] >= since
                and data["first_name"]
                and data["last_name"]
            ):
                candidates.append(data)

    logger.info("Found %d active researchers with homepages", len(candidates))

    # Build lookup of existing researchers
    existing = Database.fetch_all("SELECT id, first_name, last_name FROM researchers")
    name_to_ids: dict[tuple[str, str], list[int]] = {}
    for r in existing:
        key = (r["first_name"].lower().strip(), r["last_name"].lower().strip())
        name_to_ids.setdefault(key, []).append(r["id"])

    has_url = {
        r["researcher_id"]
        for r in Database.fetch_all("SELECT DISTINCT researcher_id FROM researcher_urls")
    }

    # Categorize
    new_researchers = []
    add_url = []
    skipped = 0

    for c in candidates:
        # Build first_name including middle name if available
        first = c["first_name"].strip()
        if c["middle_name"]:
            c["full_first_name"] = f"{first} {c['middle_name'].strip()}"
        else:
            c["full_first_name"] = first
        # Build affiliation with location if available
        affiliation = c["workplace"]
        if c["workplace_location"] and affiliation:
            c["full_affiliation"] = f"{affiliation}, {c['workplace_location']}"
        else:
            c["full_affiliation"] = affiliation

        key = (first.lower(), c["last_name"].lower().strip())
        ids = name_to_ids.get(key, [])
        if ids:
            rid = ids[0]
            if rid in has_url:
                skipped += 1
            else:
                add_url.append((rid, c))
        else:
            new_researchers.append(c)

    logger.info("Already have URL: %d (skipped)", skipped)
    logger.info("Existing researchers, adding URL: %d", len(add_url))
    logger.info("New researchers to create: %d", len(new_researchers))

    # 1. Add URLs for existing researchers
    for rid, c in add_url:
        name = f"{c['full_first_name']} {c['last_name']}"
        if dry_run:
            logger.info("  ADD URL [%d] %s → %s", rid, name, c["homepage"])
        else:
            add_researcher_url(rid, "personal", c["homepage"])

    # 2. Create new researchers + add URLs
    created = 0
    for c in new_researchers:
        name = f"{c['full_first_name']} {c['last_name']}"
        if dry_run:
            logger.info("  NEW %s (%s) → %s", name, c["full_affiliation"] or "?", c["homepage"])
        else:
            Database.execute_query(
                "INSERT INTO researchers (first_name, last_name, affiliation) VALUES (%s, %s, %s)",
                (c["full_first_name"], c["last_name"], c["full_affiliation"]),
            )
            row = Database.fetch_one(
                "SELECT id FROM researchers WHERE first_name = %s AND last_name = %s ORDER BY id DESC LIMIT 1",
                (c["full_first_name"], c["last_name"]),
            )
            if row:
                add_researcher_url(row["id"], "personal", c["homepage"])
        created += 1

    logger.info(
        "\n%s: %d URLs added to existing, %d new researchers created",
        "DRY RUN" if dry_run else "DONE",
        len(add_url),
        created,
    )


if __name__ == "__main__":
    main()
