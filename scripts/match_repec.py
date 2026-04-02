"""Match DB researchers against RePEC person records."""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from urllib.parse import urlparse


def parse_rdf_file(path: str) -> dict | None:
    """Parse a single ReDIF .rdf file into a dict.

    Returns None if the record has no Homepage field.
    Extracts: name_first, name_last, name_full, workplace, homepage, handle.
    """
    fields: dict[str, str] = {}
    current_key: str | None = None

    with open(path, encoding="latin-1") as f:
        for line in f:
            line = line.rstrip("\n\r")
            # Continuation line for Workplace-Name (starts with "/ ")
            if line.startswith("/ ") and current_key == "workplace":
                continue  # we only keep the first line
            # Field line: "Key: Value"
            if ": " in line and not line.startswith(" "):
                key, _, value = line.partition(": ")
                key = key.strip()
                value = value.strip()
                if key == "Name-First":
                    fields["name_first"] = value
                    current_key = "name_first"
                elif key == "Name-Last":
                    fields["name_last"] = value
                    current_key = "name_last"
                elif key == "Name-Full":
                    fields["name_full"] = value
                    current_key = "name_full"
                elif key == "Workplace-Name":
                    fields["workplace"] = value
                    current_key = "workplace"
                elif key == "Homepage":
                    fields["homepage"] = value
                    current_key = "homepage"
                elif key == "Handle":
                    fields["handle"] = value
                    current_key = "handle"
                else:
                    current_key = None
            else:
                current_key = None

    if "homepage" not in fields:
        return None
    if "name_first" not in fields or "name_last" not in fields:
        return None

    fields.setdefault("name_full", f"{fields['name_first']} {fields['name_last']}")
    fields.setdefault("workplace", "")
    fields.setdefault("handle", "")

    return fields


def build_repec_index(repec_dir: str) -> tuple[dict, dict]:
    """Walk repec_dir, parse all .rdf files, return two indexes:

    by_name: dict[(first_lower, last_lower)] -> list[record]
    by_domain: dict[domain_str] -> list[record]
    """
    by_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_domain: dict[str, list[dict]] = defaultdict(list)
    parsed = 0
    skipped = 0

    for dirpath, _, filenames in os.walk(repec_dir):
        for fname in filenames:
            if not fname.endswith(".rdf"):
                continue
            record = parse_rdf_file(os.path.join(dirpath, fname))
            if record is None:
                skipped += 1
                continue
            parsed += 1
            key = (record["name_first"].lower().strip(), record["name_last"].lower().strip())
            by_name[key].append(record)

            domain = urlparse(record["homepage"]).netloc.lower()
            if domain:
                by_domain[domain].append(record)

    print(f"RePEC: parsed {parsed} records with homepage, skipped {skipped} without")
    return dict(by_name), dict(by_domain)


SHARED_HOSTING_DOMAINS = {
    "sites.google.com", "github.io", "wordpress.com", "weebly.com",
    "wixsite.com", "blogspot.com", "squarespace.com", "netlify.app",
    "vercel.app", "github.com",
}


def _normalize_url(url: str) -> str:
    """Normalize URL for comparison: lowercase, strip trailing slash."""
    return url.lower().rstrip("/")


def _make_match_row(researcher: dict, record: dict, match_type: str, confidence: str) -> dict:
    return {
        "researcher_id": researcher["id"],
        "first_name": researcher["first_name"],
        "last_name": researcher["last_name"],
        "db_affiliation": researcher.get("affiliation") or "",
        "repec_name": record["name_full"],
        "repec_workplace": record["workplace"],
        "repec_homepage": record["homepage"],
        "repec_handle": record["handle"],
        "match_type": match_type,
        "confidence": confidence,
    }


def match_by_url(researcher: dict, by_domain: dict) -> list[dict]:
    """Match a researcher against RePEC records by URL domain."""
    matches = []
    last_lower = researcher["last_name"].lower()
    for url in researcher.get("urls", []):
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain or domain not in by_domain:
            continue
        candidates = by_domain[domain]
        is_shared = domain in SHARED_HOSTING_DOMAINS
        for record in candidates:
            if record["name_last"].lower() != last_lower:
                continue
            if is_shared:
                if _normalize_url(url) != _normalize_url(record["homepage"]):
                    continue
            matches.append(_make_match_row(researcher, record, "url_match", "unique"))
    return matches


def _affiliation_matches(db_affil: str, repec_workplace: str) -> bool:
    """Check if DB affiliation matches RePEC workplace (case-insensitive).

    Handles substring matches in either direction, plus acronym matching
    (e.g. "MIT" matches "Massachusetts Institute of Technology").
    """
    if not db_affil or not repec_workplace:
        return False
    a = db_affil.lower()
    b = repec_workplace.lower()
    if a in b or b in a:
        return True
    # Acronym check: db_affil could be an abbreviation of workplace words
    words = [w for w in b.split() if w.isalpha() and len(w) > 2]
    if words:
        acronym = "".join(w[0] for w in words)
        if a == acronym:
            return True
    return False


def match_by_name(researcher: dict, by_name: dict) -> list[dict]:
    """Match a researcher against RePEC records by exact name."""
    key = (researcher["first_name"].lower().strip(), researcher["last_name"].lower().strip())
    candidates = by_name.get(key)
    if not candidates:
        return []
    if len(candidates) == 1:
        return [_make_match_row(researcher, candidates[0], "exact_name", "unique")]
    db_affil = researcher.get("affiliation") or ""
    if db_affil:
        affil_matches = [c for c in candidates if _affiliation_matches(db_affil, c["workplace"])]
        if len(affil_matches) == 1:
            return [_make_match_row(researcher, affil_matches[0], "exact_name", "affiliation_match")]
    return [_make_match_row(researcher, c, "exact_name", "ambiguous") for c in candidates]


CSV_COLUMNS = [
    "researcher_id", "first_name", "last_name", "db_affiliation",
    "repec_name", "repec_workplace", "repec_homepage", "repec_handle",
    "match_type", "confidence",
]


def write_csv(rows: list[dict], output_path: str) -> None:
    """Write match rows to CSV."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {output_path}")


def load_researchers() -> list[dict]:
    """Load all researchers with their URLs from the database."""
    from database.connection import fetch_all
    researchers = fetch_all(
        "SELECT r.id, r.first_name, r.last_name, r.affiliation "
        "FROM researchers r ORDER BY r.id"
    )
    urls = fetch_all("SELECT researcher_id, url FROM researcher_urls")
    url_map: dict[int, list[str]] = defaultdict(list)
    for row in urls:
        url_map[row["researcher_id"]].append(row["url"])
    for r in researchers:
        r["urls"] = url_map.get(r["id"], [])
    return researchers


def run_matching(researchers: list[dict], by_name: dict, by_domain: dict) -> list[dict]:
    """Run URL matching then name matching, deduplicate."""
    all_matches: list[dict] = []
    url_matched_ids: set[int] = set()
    for r in researchers:
        if not r["urls"]:
            continue
        url_matches = match_by_url(r, by_domain)
        if url_matches:
            all_matches.extend(url_matches)
            url_matched_ids.add(r["id"])
    for r in researchers:
        if r["id"] in url_matched_ids:
            continue
        name_matches = match_by_name(r, by_name)
        all_matches.extend(name_matches)
    return all_matches


def print_summary(matches: list[dict], total_researchers: int) -> None:
    """Print match statistics."""
    unique = sum(1 for m in matches if m["confidence"] == "unique")
    affil = sum(1 for m in matches if m["confidence"] == "affiliation_match")
    ambiguous = sum(1 for m in matches if m["confidence"] == "ambiguous")
    matched_ids = {m["researcher_id"] for m in matches}
    print(f"\n{'='*50}")
    print(f"RePEC Matching Summary")
    print(f"{'='*50}")
    print(f"Total researchers in DB:     {total_researchers}")
    print(f"Researchers matched:         {len(matched_ids)}")
    print(f"  - unique:                  {unique}")
    print(f"  - affiliation_match:       {affil}")
    print(f"  - ambiguous:               {ambiguous}")
    print(f"No match:                    {total_researchers - len(matched_ids)}")
    print(f"Total CSV rows:              {len(matches)}")
    url_matches = sum(1 for m in matches if m["match_type"] == "url_match")
    name_matches = sum(1 for m in matches if m["match_type"] == "exact_name")
    print(f"\nBy match type:")
    print(f"  - url_match:               {url_matches}")
    print(f"  - exact_name:              {name_matches}")


def main():
    parser = argparse.ArgumentParser(description="Match DB researchers against RePEC person records")
    parser.add_argument("--repec-dir", default="RePEc/per/pers/",
                        help="Path to RePEC person data (default: RePEc/per/pers/)")
    parser.add_argument("--output", default="repec_matches.csv",
                        help="Output CSV path (default: repec_matches.csv)")
    parser.add_argument("--stats-only", action="store_true",
                        help="Parse RePEC data and report stats without DB lookup")
    args = parser.parse_args()

    print(f"Parsing RePEC data from {args.repec_dir}...")
    by_name, by_domain = build_repec_index(args.repec_dir)
    print(f"Index: {len(by_name)} unique names, {len(by_domain)} unique domains")

    if args.stats_only:
        total_records = sum(len(v) for v in by_name.values())
        print(f"\nTotal records with homepage: {total_records}")
        multi_name = {k: v for k, v in by_name.items() if len(v) > 1}
        print(f"Names with multiple records: {len(multi_name)}")
        return

    print("\nLoading researchers from database...")
    researchers = load_researchers()
    print(f"Loaded {len(researchers)} researchers ({sum(1 for r in researchers if r['urls'])} with URLs)")

    print("\nRunning matching...")
    matches = run_matching(researchers, by_name, by_domain)

    write_csv(matches, args.output)
    print_summary(matches, len(researchers))


if __name__ == "__main__":
    main()
