"""Researcher opt-out tombstones: checks used wherever people or URLs are added.

A tombstone holds SHA-256 hashes only: the normalized full name and each URL
(exact string, plus the miner's normalized comparison key). That is enough to
refuse re-adding the person or crawling their pages, without keeping their data.
"""
from __future__ import annotations

import hashlib
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


def name_optout_query(first_name: str, last_name: str) -> tuple[str, tuple]:
    """(sql, params) returning a row iff this name belongs to an opted-out researcher."""
    return (
        "SELECT 1 AS hit FROM researcher_optouts WHERE name_hash = %s LIMIT 1",
        (name_hash(first_name, last_name),),
    )


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
