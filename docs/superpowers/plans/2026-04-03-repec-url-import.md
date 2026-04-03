# RePEc Homepage URL Import Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import personal website URLs from RePEc for 1,839 coauthor-only researchers who were matched by name but have no URL in the database, turning them into trackable researchers.

**Architecture:** A one-time Python script reads `repec_matches_unique.csv`, filters to `match_type=exact_name` rows (researchers without existing URLs), and calls the existing `add_researcher_url()` function to insert each homepage. The script is idempotent via `INSERT IGNORE`. A dry-run mode previews changes. After import, `make scrape` will start fetching these researchers' pages.

**Tech Stack:** Python, CSV, existing `database.researchers.add_researcher_url()`

---

### Task 1: Create the import script with dry-run support

**Files:**
- Create: `scripts/import_repec_urls.py`
- Modify: `Makefile` (add target)

- [ ] **Step 1: Create the import script**

```python
"""Import personal website URLs from RePEc matches into researcher_urls.

Reads repec_matches_unique.csv, filters to exact_name matches (researchers
without existing URLs), and adds their RePEc homepage as a researcher URL.

Usage:
    poetry run python scripts/import_repec_urls.py [--dry-run]
"""
import csv
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from database.researchers import add_researcher_url

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "repec_matches_unique.csv",
)


def main():
    dry_run = "--dry-run" in sys.argv

    if not os.path.exists(CSV_PATH):
        logger.error("CSV not found: %s", CSV_PATH)
        sys.exit(1)

    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Only import exact_name matches — url_match researchers already have URLs
    candidates = [
        r for r in rows
        if r["match_type"] == "exact_name"
        and r.get("repec_homepage", "").strip()
    ]

    logger.info("Found %d exact_name researchers with RePEc homepages", len(candidates))

    # Check which already have a URL in the database
    existing = Database.fetch_all(
        "SELECT DISTINCT researcher_id FROM researcher_urls"
    )
    existing_ids = {r["researcher_id"] for r in existing}

    to_import = [r for r in candidates if int(r["researcher_id"]) not in existing_ids]
    already_have = len(candidates) - len(to_import)

    if already_have > 0:
        logger.info("Skipping %d researchers who already have URLs", already_have)

    logger.info("Will import %d new researcher URLs", len(to_import))

    imported = 0
    for r in to_import:
        rid = int(r["researcher_id"])
        url = r["repec_homepage"].strip()
        name = f"{r['first_name']} {r['last_name']}"

        if dry_run:
            logger.info("  [%d] %s → %s", rid, name, url)
        else:
            add_researcher_url(rid, "personal", url)
            logger.info("  [%d] %s → %s", rid, name, url)
        imported += 1

    logger.info(
        "\n%s %d researcher URLs from RePEc",
        "Would import" if dry_run else "Imported",
        imported,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add Makefile target**

Add to `Makefile` before the `check:` target:

```makefile
import-repec-urls:  ## Import researcher URLs from RePEc matches CSV
	poetry run python scripts/import_repec_urls.py
```

Update the `.PHONY` line to include `import-repec-urls`.

- [ ] **Step 3: Test with dry-run**

Run: `poetry run python scripts/import_repec_urls.py --dry-run`

Expected output:
```
Found 1839 exact_name researchers with RePEc homepages
Skipping 0 researchers who already have URLs
Will import 1839 new researcher URLs
  [20] Lisa Tarquinio → https://sites.google.com/view/lisatarquinio
  [22] Mark Gertler → http://www.econ.nyu.edu/user/gertlerm/
  ...
Would import 1839 researcher URLs from RePEc
```

Verify the count looks right and spot-check a few URLs.

- [ ] **Step 4: Run the import for real**

Run: `poetry run python scripts/import_repec_urls.py`

Expected: `Imported 1839 researcher URLs from RePEc`

- [ ] **Step 5: Verify in database**

Run:
```bash
poetry run python -c "
from database import Database
total = Database.fetch_one('SELECT COUNT(DISTINCT researcher_id) as c FROM researcher_urls')
print(f'Total researchers with URLs: {total[\"c\"]}')
"
```

Expected: The count should have increased by ~1839 from the previous value.

- [ ] **Step 6: Add script to .dockerignore whitelist**

Add `!scripts/import_repec_urls.py` to `.dockerignore`.

- [ ] **Step 7: Commit**

```bash
git add scripts/import_repec_urls.py Makefile .dockerignore
git commit -m "feat: add script to import researcher URLs from RePEc matches"
```

---

### Post-Import Notes

After importing, these researchers will be picked up by `make scrape`:
1. **Fetch phase** will download HTML from the new URLs
2. **Extract phase** will run LLM extraction on the HTML
3. **Enrichment** will enrich any new papers via OpenAlex

This will significantly increase the number of tracked researchers (from ~105 to ~1,944). Consider:
- Running `make fetch` first (no LLM cost) to see how many URLs are actually reachable
- Then `make parse` or `make batch-submit` (cheaper) for extraction
- Monitor OpenAI costs — 1,839 new pages × ~$0.01/page ≈ $18
