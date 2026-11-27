"""Researcher opt-out tombstones: checks used wherever people or URLs are added.

A tombstone holds SHA-256 hashes only: the normalized full name and each URL
(exact string, plus the miner's normalized comparison key). That is enough to
refuse re-adding the person or crawling their pages, without keeping their data.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from backend.database.connection import fetch_all, fetch_one

# SQL guard for queries over researcher_urls (alias the table and pass its url
# column). MySQL's SHA2 over a utf8mb4 string hashes its UTF-8 bytes, which
# matches url_hash() below.
def url_not_opted_out_sql(url_column: str) -> str:
    return (
        "NOT EXISTS (SELECT 1 FROM researcher_optout_urls opt "
        f"WHERE opt.url_hash = SHA2({url_column}, 256))"
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def full_name_hash(full_name: str) -> str:
    """Hash of the case-, Unicode-form- and whitespace-normalized full name."""
    return _sha(" ".join(unicodedata.normalize("NFKC", full_name or "").casefold().split()))


def name_hash(first_name: str, last_name: str) -> str:
    return full_name_hash(f"{first_name or ''} {last_name or ''}")


def url_hash(url: str) -> str:
    return _sha(url)


def url_key_hash(url: str) -> str | None:
    """Hash of the miner's normalized URL key (www, trailing slash, tracking params)."""
    from backend.discovery.mining_policy import url_key
    key = url_key(url)
    return _sha(key) if key else None


def _fold(value: str) -> str:
    """Casefold and strip accents (Müller -> muller) for variant matching."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _variant_parts(first_name: str, last_name: str) -> tuple[list[str], str] | None:
    given = [t for t in re.split(r"[\s.]+", _fold(first_name)) if t]
    last = " ".join(_fold(last_name).split())
    return (given, last) if given and last else None


# Name variants of an opted-out person, so co-author extraction does not
# re-create them as "Ada M. Optout" or "A. Optout". Kinds:
#   given         first given name + last name ("ada optout"; middle names dropped)
#   initial       first initial + last name, matched only by initials-only names
#   initial_only  the opted-out name itself was initials only ("A. Optout"), so
#                 any first name with that initial matches
def name_variant_hashes(first_name: str, last_name: str) -> list[tuple[str, str]]:
    """(kind, hash) rows stored with a tombstone."""
    parts = _variant_parts(first_name, last_name)
    if parts is None:
        return []
    given, last = parts
    initial = _sha(f"{given[0][0]} {last}")
    rows = [("initial", initial)]
    if len(given[0]) > 1:
        rows.append(("given", _sha(f"{given[0]} {last}")))
    if all(len(t) == 1 for t in given):
        rows.append(("initial_only", initial))
    return rows


def _variant_probes(first_name: str, last_name: str) -> list[tuple[str, str]]:
    """(kind, hash) pairs a new name is checked against."""
    parts = _variant_parts(first_name, last_name)
    if parts is None:
        return []
    given, last = parts
    initial = _sha(f"{given[0][0]} {last}")
    probes = [("initial_only", initial)]
    if len(given[0]) > 1:
        probes.append(("given", _sha(f"{given[0]} {last}")))
    if all(len(t) == 1 for t in given):
        probes.append(("initial", initial))
    return probes


def name_optout_query(first_name: str, last_name: str) -> tuple[str, tuple]:
    """(sql, params) returning a row iff this name, or a variant of it, belongs
    to an opted-out researcher (see name_variant_hashes)."""
    sql = "SELECT 1 AS hit FROM researcher_optouts WHERE name_hash = %s"
    params: list[str] = [name_hash(first_name, last_name)]
    probes = _variant_probes(first_name, last_name)
    if probes:
        sql += (
            " UNION ALL SELECT 1 AS hit FROM researcher_optout_names WHERE "
            + " OR ".join("(kind = %s AND name_hash = %s)" for _ in probes)
        )
        params += [v for probe in probes for v in probe]
    return sql + " LIMIT 1", tuple(params)


def url_optout_query(url: str) -> tuple[str, tuple]:
    """(sql, params) returning a row iff this URL (or an alias) was opted out."""
    key_hash = url_key_hash(url)
    if key_hash is None:
        return ("SELECT 1 AS hit FROM researcher_optout_urls WHERE url_hash = %s LIMIT 1",
                (url_hash(url),))
    return (
        "SELECT 1 AS hit FROM researcher_optout_urls WHERE url_hash = %s OR url_key_hash = %s LIMIT 1",
        (url_hash(url), key_hash),
    )


def is_name_opted_out(first_name: str, last_name: str) -> bool:
    return fetch_one(*name_optout_query(first_name, last_name)) is not None


def is_url_opted_out(url: str) -> bool:
    return fetch_one(*url_optout_query(url)) is not None


def opted_out_hashes() -> tuple[set[str], set[str]]:
    """(name hashes, URL key hashes) for bulk checks such as the site miner."""
    names = {r["name_hash"] for r in fetch_all("SELECT name_hash FROM researcher_optouts")}
    urls = {
        r["url_key_hash"] for r in fetch_all(
            "SELECT url_key_hash FROM researcher_optout_urls WHERE url_key_hash IS NOT NULL"
        )
    }
    return names, urls
