"""Remove a researcher who asked to be removed, and keep them removed.

Opting out deletes the researcher row. Foreign keys cascade to their URLs,
cached HTML and HTML snapshots, authorships, follows, JEL codes, Searlo
discoveries and researcher snapshots. Papers that no other *tracked* researcher
(one with a crawled URL) lists are deleted with their feed events; co-authored
papers that another tracked researcher lists stay, without the authorship link
and without pointing back at the removed researcher's pages.

What remains is a hash-only tombstone (``researcher_optouts`` and
``researcher_optout_urls``) that refuses re-creation through co-author
extraction, URL imports and Searlo approvals, plus a rejected identity-hold row
in the site miner's queue so the miner's own blocklist refuses the URLs.
"""
from __future__ import annotations

import json
import logging

from backend.database.connection import get_connection
from backend.database.optouts import name_hash, name_variant_hashes, url_hash, url_key_hash

logger = logging.getLogger(__name__)

OPT_OUT_REASON = "Researcher opted out of Econ Newsfeed"
_HOLD_NAME = "[opted-out researcher]"


def _in(values) -> str:
    return ",".join(["%s"] * len(values))


def _table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) AS n FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s", (table,),
    )
    return cursor.fetchone()["n"] > 0


def _miner_hold(name: str, urls: list[dict]) -> dict | None:
    """A rejected identity-hold candidate covering every URL of the researcher."""
    from backend.discovery.mining_policy import url_key
    from backend.discovery.mining_store import candidate_key

    valid = [u for u in urls if url_key(u["url"])]
    if not valid:
        return None
    primary = next((u for u in valid if u["page_type"] == "HOME"), valid[0])
    return {
        "candidate_key": candidate_key({"name": name, "url": primary["url"], "researcher_id": None}),
        "url": primary["url"],
        "urls": [{"url": u["url"], "page_type": u["page_type"]} for u in valid],
    }


def opt_out_researcher(researcher_id: int, *, apply: bool = False) -> dict:
    """Remove a researcher and tombstone them. Dry run unless ``apply``."""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        if apply:
            # DDL commits implicitly, so it must run before the transaction.
            from backend.discovery.mining_store import ensure_mining_tables
            ensure_mining_tables(cursor)
        conn.rollback()
        conn.start_transaction()

        cursor.execute(
            "SELECT id, first_name, last_name FROM researchers WHERE id = %s FOR UPDATE",
            (researcher_id,),
        )
        person = cursor.fetchone()
        if person is None:
            raise LookupError(f"Researcher {researcher_id} not found")
        name = f"{person['first_name']} {person['last_name']}"

        cursor.execute(
            "SELECT id, url, page_type FROM researcher_urls WHERE researcher_id = %s ORDER BY id",
            (researcher_id,),
        )
        urls = cursor.fetchall()
        url_strings = [u["url"] for u in urls]

        cursor.execute(
            """SELECT a.publication_id AS id,
                      EXISTS (SELECT 1 FROM authorship o
                              JOIN researcher_urls ru ON ru.researcher_id = o.researcher_id
                              WHERE o.publication_id = a.publication_id
                                AND o.researcher_id <> a.researcher_id) AS shared
               FROM authorship a WHERE a.researcher_id = %s""",
            (researcher_id,),
        )
        papers = cursor.fetchall()
        delete_ids = sorted(p["id"] for p in papers if not p["shared"])
        keep_ids = sorted(p["id"] for p in papers if p["shared"])
        hold = _miner_hold(name, urls)

        summary = {
            "researcher_id": researcher_id,
            "name": name,
            "urls": url_strings,
            "papers_deleted": len(delete_ids),
            "papers_kept_for_coauthors": len(keep_ids),
            "miner_hold": hold is not None,
            "applied": apply,
        }
        if not apply:
            conn.rollback()
            return summary

        cursor.execute(
            "INSERT INTO researcher_optouts (name_hash, former_researcher_id) VALUES (%s, %s)",
            (name_hash(person["first_name"], person["last_name"]), researcher_id),
        )
        optout_id = cursor.lastrowid
        variants = name_variant_hashes(person["first_name"], person["last_name"])
        if variants:
            cursor.executemany(
                "INSERT IGNORE INTO researcher_optout_names (kind, name_hash, optout_id)"
                " VALUES (%s, %s, %s)",
                [(kind, h, optout_id) for kind, h in variants],
            )
        if urls:
            cursor.executemany(
                "INSERT IGNORE INTO researcher_optout_urls (url_hash, url_key_hash, optout_id)"
                " VALUES (%s, %s, %s)",
                [(url_hash(u), url_key_hash(u), optout_id) for u in url_strings],
            )

        if delete_ids:
            cursor.execute(f"DELETE FROM papers WHERE id IN ({_in(delete_ids)})", delete_ids)
        if keep_ids and url_strings:
            cursor.execute(
                f"UPDATE papers SET source_url = NULL WHERE id IN ({_in(keep_ids)})"
                f" AND source_url IN ({_in(url_strings)})",
                [*keep_ids, *url_strings],
            )
            cursor.execute(
                f"UPDATE feed_events SET event_source_url = NULL WHERE paper_id IN ({_in(keep_ids)})"
                f" AND event_source_url IN ({_in(url_strings)})",
                [*keep_ids, *url_strings],
            )

        if _table_exists(cursor, "site_mining_candidates"):
            cursor.execute(
                """UPDATE site_mining_candidates
                   SET status = 'rejected', reason = %s, researcher_id = NULL,
                       updated_at = UTC_TIMESTAMP()
                   WHERE researcher_id = %s""",
                (OPT_OUT_REASON, researcher_id),
            )
            if hold:
                evidence = json.dumps({"identity_hold": True, "opt_out": True})
                cursor.execute(
                    """INSERT INTO site_mining_candidates
                         (candidate_key, researcher_id, name, url, source_url, source_kind,
                          evidence, status, reason, urls)
                       VALUES (%s, NULL, %s, %s, %s, 'opt_out', %s, 'rejected', %s, %s)
                       ON DUPLICATE KEY UPDATE status = 'rejected', researcher_id = NULL,
                         evidence = VALUES(evidence), reason = VALUES(reason),
                         urls = VALUES(urls), updated_at = UTC_TIMESTAMP()""",
                    (hold["candidate_key"], _HOLD_NAME, hold["url"], hold["url"],
                     evidence, OPT_OUT_REASON, json.dumps(hold["urls"])),
                )

        cursor.execute("DELETE FROM researchers WHERE id = %s", (researcher_id,))
        conn.commit()
        logger.info("Researcher %d opted out: %s", researcher_id, summary)
        return summary
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_optouts() -> list[dict]:
    from backend.database.connection import fetch_all
    return fetch_all(
        """SELECT o.id, o.former_researcher_id, o.created_at, COUNT(u.url_hash) AS urls
           FROM researcher_optouts o LEFT JOIN researcher_optout_urls u ON u.optout_id = o.id
           GROUP BY o.id ORDER BY o.id"""
    )
