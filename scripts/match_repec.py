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
