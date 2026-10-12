"""Durable, conservative storage for the search-free website miner.

One connection owns the advisory lock throughout a run.  Approval is a separate
transaction per researcher/site bundle: preflight every URL, insert the complete
bundle, and mark its candidate approved together.  No project execute_query helper
is used here because it commits individual statements.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from backend.database.connection import get_connection
from backend.discovery.mining_policy import same_owner, scope_for, url_key


_TABLES = ("site_mining_sources", "site_mining_candidates", "site_mining_runs")
_DDL = (
    """CREATE TABLE IF NOT EXISTS site_mining_sources (
        source_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
        kind VARCHAR(32) NOT NULL, url VARCHAR(2048) NOT NULL,
        content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
        checked_at DATETIME NOT NULL, next_check_at DATETIME NOT NULL,
        last_error TEXT, INDEX idx_mining_source_due (kind, next_check_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS site_mining_candidates (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        candidate_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        researcher_id INT, name VARCHAR(511) NOT NULL,
        url VARCHAR(2048) NOT NULL, source_url VARCHAR(2048) NOT NULL,
        source_kind VARCHAR(32) NOT NULL, evidence JSON NOT NULL,
        status ENUM('pending','retry','review','approved','rejected')
            NOT NULL DEFAULT 'pending',
        reason TEXT, urls JSON, attempts INT NOT NULL DEFAULT 0,
        next_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_mining_candidate (candidate_key),
        INDEX idx_mining_candidate_due (status, next_attempt_at),
        INDEX idx_mining_candidate_researcher (researcher_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS site_mining_runs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        started DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished DATETIME,
        state ENUM('running','complete','failed') NOT NULL DEFAULT 'running',
        stats JSON, INDEX idx_mining_run_started (started)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
)
_TERMINAL = {"approved", "rejected", "review"}
_STATUSES = _TERMINAL | {"pending", "retry"}
_PAGE_TYPES = {"HOME", "RESEARCH", "PUBLICATIONS", "WORKING_PAPERS"}


def _utcnow():
    # MySQL DATETIME has no timezone; all miner timestamps are stored in UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def source_key(kind: str, url: str) -> str:
    """Key exact source URLs; unlike candidate URLs their fetched bytes differ."""
    return hashlib.sha256(f"{kind}\n{url}".encode()).hexdigest()


def _name_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def candidate_key(candidate: dict) -> str:
    key = url_key(candidate["url"])
    if not key:
        raise ValueError("Invalid candidate URL")
    rid = candidate.get("researcher_id")
    owner = f"id:{rid}" if rid is not None else f"name:{_name_key(candidate['name'])}"
    # A directory profile is a recurring source as well as a possible website.
    # Its weekly refresh must not inherit a terminal website-candidate decision.
    kind = "\ndirectory_profile" if candidate.get("source_kind") == "directory_profile" else ""
    return hashlib.sha256(f"{owner}\n{key}{kind}".encode()).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode(row: dict) -> dict:
    row = dict(row)
    for key in ("evidence", "urls"):
        if isinstance(row.get(key), (str, bytes, bytearray)):
            row[key] = json.loads(row[key])
    return row


class MiningStore:
    """Run-scoped store. Dry runs neither create tables nor mutate existing data."""

    def __init__(self, apply: bool = False):
        self.apply = apply
        self.acquired = False
        self.conn = None
        self._original_autocommit = False
        self._tables: set[str] = set()
        self._sources: dict[str, dict] = {}
        self._candidates: dict[str, dict] = {}
        self._dry_url_rows: list[dict] = []
        self._cached_seen: set[tuple[int, str]] = set()

    def __enter__(self):
        self.conn = get_connection()
        try:
            # Release a snapshot left on a pooled connection before locking.
            self.conn.rollback()
            self._original_autocommit = getattr(self.conn, "autocommit", False)
            # PooledMySQLConnection delegates attribute reads, not assignments:
            # assigning .autocommit would only shadow the wrapper attribute.
            # Set the server session so standalone queue/checkpoint writes are
            # durable and do not leave an implicit transaction before approval.
            self._execute("SET SESSION autocommit = 1")
            self.acquired = self._one(
                "SELECT GET_LOCK('econ_site_mining', 0) AS acquired"
            )["acquired"] == 1
            if not self.acquired:
                return self
            if self.apply:
                for ddl in _DDL:
                    self._execute(ddl)
                self._tables = set(_TABLES)
            else:
                self._tables = {
                    row["table_name"] for row in self._all(
                        """SELECT TABLE_NAME AS table_name FROM information_schema.tables
                           WHERE table_schema = DATABASE()
                             AND table_name IN (%s,%s,%s)""", _TABLES
                    )
                }
            return self
        except BaseException:
            self._close()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self._close()

    def _close(self):
        if self.conn is None:
            return
        try:
            try:
                self.conn.rollback()
            finally:
                if self.acquired:
                    self._one("SELECT RELEASE_LOCK('econ_site_mining') AS released")
        finally:
            self.acquired = False
            try:
                self._execute("SET SESSION autocommit = %s", (int(self._original_autocommit),))
            finally:
                self.conn.close()
                self.conn = None

    def _require_lock(self):
        if not self.acquired:
            raise RuntimeError("MiningStore has no run lock")

    def _all(self, sql, params=None):
        with self.conn.cursor(dictionary=True) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _one(self, sql, params=None):
        with self.conn.cursor(dictionary=True) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _execute(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid, cur.rowcount

    def researchers(self) -> list[dict]:
        self._require_lock()
        return self._all("SELECT id,first_name,last_name,affiliation FROM researchers")

    def known_urls(self) -> list[dict]:
        self._require_lock()
        return self._all(
            "SELECT id,researcher_id,url,page_type,is_active FROM researcher_urls"
        ) + self._dry_url_rows

    def cached_sources(self, limit: int):
        """Read a bounded batch whose *raw HTML* has not been mined successfully.

        Stored text hashes can stay unchanged when a link href changes. SHA2 is
        therefore calculated over raw_html in MySQL; only selected bodies cross
        the connection. Successful checkpoints remove rows from later batches.
        Failed rows back off, so an early failing URL cannot starve later rows.
        """
        self._require_lock()
        limit = max(0, int(limit))
        if not limit:
            return
        has_table = "site_mining_sources" in self._tables
        join = """LEFT JOIN site_mining_sources s
                    ON s.source_key = SHA2(CONCAT('cached',CHAR(10),ru.url),256)""" if has_table else ""
        due = """AND (s.source_key IS NULL OR
                   ((s.content_hash IS NULL OR s.content_hash <> SHA2(h.raw_html,256))
                     AND (s.last_error IS NULL OR s.next_check_at <= UTC_TIMESTAMP()))
                   OR (s.last_error IS NOT NULL AND s.next_check_at <= UTC_TIMESTAMP()))""" if has_table else ""
        # In apply mode persisted hashes are the cursor. Dry runs may repeatedly
        # read batches, so skip previously returned rows without buffering HTML.
        skip_ids = sorted({rid for rid, _ in self._cached_seen}) if not self.apply else []
        skip = "AND ru.id NOT IN (" + ",".join(["%s"] * len(skip_ids)) + ")" if skip_ids else ""
        rows = self._all(
            f"""SELECT ru.id AS url_id,ru.researcher_id,ru.url,
                       SHA2(h.raw_html,256) AS content_hash
                FROM researcher_urls ru JOIN html_content h ON h.url_id = ru.id
                {join}
                WHERE ru.is_active = 1 AND h.raw_html IS NOT NULL AND h.raw_html <> ''
                  {due} {skip}
                ORDER BY ru.id LIMIT %s""", (*skip_ids, limit)
        )
        # Only small metadata rows are materialized above. One MEDIUMTEXT body
        # at a time keeps a 500-page batch safe in the production 512MB process.
        for metadata in rows:
            row = self._one(
                """SELECT ru.id AS url_id,ru.researcher_id,ru.url,h.raw_html,
                          SHA2(h.raw_html,256) AS content_hash
                   FROM researcher_urls ru JOIN html_content h ON h.url_id = ru.id
                   WHERE ru.id = %s AND ru.is_active=1
                     AND h.raw_html IS NOT NULL AND h.raw_html <> ''""",
                (metadata["url_id"],),
            )
            if not row:
                continue
            if not self.apply:
                self._cached_seen.add((row["url_id"], row["content_hash"]))
            yield row

    def source_due(self, kind: str, url: str) -> bool:
        self._require_lock()
        key = source_key(kind, url)
        row = self._sources.get(key)
        if row is None and "site_mining_sources" in self._tables:
            row = self._one(
                "SELECT next_check_at FROM site_mining_sources WHERE source_key = %s", (key,)
            )
        return not row or row["next_check_at"] <= _utcnow()

    def checkpoint(self, kind, url, content_hash, error=None, retry_hours=168):
        self._require_lock()
        key = source_key(kind, url)
        now = _utcnow()
        next_check = now + timedelta(hours=max(0, retry_hours))
        if not self.apply:
            self._sources[key] = {"next_check_at": next_check, "content_hash": content_hash}
            return
        self._execute(
            """INSERT INTO site_mining_sources
               (source_key,kind,url,content_hash,checked_at,next_check_at,last_error)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE content_hash=VALUES(content_hash),
                 checked_at=VALUES(checked_at),next_check_at=VALUES(next_check_at),
                 last_error=VALUES(last_error)""",
            (key, kind, url, content_hash, now, next_check, error),
        )

    def enqueue(self, candidate: dict) -> bool:
        self._require_lock()
        row = dict(candidate)
        key = candidate_key(row)
        if key in self._candidates:
            return False
        existing = None
        if "site_mining_candidates" in self._tables:
            existing = self._one(
                "SELECT * FROM site_mining_candidates WHERE candidate_key = %s", (key,)
            )
        if existing:
            old_evidence = _decode(existing).get("evidence") or {}
            new_generation = (row.get("evidence") or {}).get("directory_generation")
            old_generation = old_evidence.get("directory_generation")
            # A profile can wait in the queue for days before verification.
            # Refresh when its directory was actually revisited, regardless of
            # when the previous profile verification happened. Keep the age
            # fallback only for legacy rows/callers without a generation.
            new_visit = bool(new_generation) and new_generation != old_generation
            legacy_due = (not new_generation and not old_generation
                          and existing["updated_at"] <= _utcnow() - timedelta(days=6))
            refresh = (
                row.get("source_kind") == "directory_profile"
                and existing["source_kind"] == "directory_profile"
                and existing["status"] in {"approved", "review"}
                and not old_evidence.get("identity_hold")
                and (new_visit or legacy_due)
            )
            if not refresh:
                return False
            if self.apply:
                _, count = self._execute(
                    """UPDATE site_mining_candidates SET status='pending',reason=NULL,urls=NULL,
                         evidence=%s,source_url=%s,attempts=0,
                         next_attempt_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP()
                       WHERE id=%s AND status IN ('approved','review')
                         AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(evidence,'$.identity_hold')),'false') <> 'true'
                         AND updated_at=%s""",
                    (_json(row.get("evidence", {})), row["source_url"], existing["id"], existing["updated_at"]),
                )
                return count == 1
        row.update(candidate_key=key, status="pending", attempts=0,
                   next_attempt_at=_utcnow())
        if not self.apply:
            row["id"] = existing["id"] if existing else -len(self._candidates) - 1
            self._candidates[key] = row
            return True
        row_id, count = self._execute(
            """INSERT INTO site_mining_candidates
               (candidate_key,researcher_id,name,url,source_url,source_kind,evidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE id=id""",
            (key, row.get("researcher_id"), row["name"], row["url"],
             row["source_url"], row["source_kind"], _json(row.get("evidence", {}))),
        )
        return count == 1 and bool(row_id)

    def due_candidates(self, limit: int) -> list[dict]:
        self._require_lock()
        limit = max(0, int(limit))
        if not limit:
            return []
        rows = []
        if "site_mining_candidates" in self._tables:
            processed = [r["id"] for r in self._candidates.values() if r["id"] > 0]
            skip = "AND id NOT IN (" + ",".join(["%s"] * len(processed)) + ")" if processed else ""
            rows = self._all(
                f"""SELECT * FROM site_mining_candidates
                   WHERE status IN ('pending','retry')
                     AND next_attempt_at <= UTC_TIMESTAMP()
                     {skip}
                   ORDER BY next_attempt_at,id LIMIT %s""", (*processed, limit)
            )
        if not self.apply:
            # Overlay results of candidates already assessed in this dry run.
            combined = {r["candidate_key"]: _decode(r) for r in rows}
            combined.update(self._candidates)
            rows = [r for r in combined.values() if r["status"] in {"pending", "retry"}
                    and r["next_attempt_at"] <= _utcnow()]
            rows.sort(key=lambda r: (r["next_attempt_at"], r["id"]))
        return [_decode(r) for r in rows[:limit]]

    def paper_titles(self, rid: int, source_rid: int | None = None) -> list[str]:
        self._require_lock()
        shared = """AND EXISTS (SELECT 1 FROM authorship peer
                     WHERE peer.publication_id = a.publication_id AND peer.researcher_id = %s)""" if source_rid is not None else ""
        params = (rid, source_rid) if source_rid is not None else (rid,)
        rows = self._all(
            f"""SELECT DISTINCT p.title FROM authorship a
                 JOIN papers p ON p.id = a.publication_id
                 WHERE a.researcher_id = %s AND p.title IS NOT NULL {shared}""", params
        )
        return [r["title"] for r in rows if r["title"]]

    def save_result(self, candidate, status, reason, urls=None, retry_hours=168, *, count_attempt=True):
        self._require_lock()
        if status not in _STATUSES:
            raise ValueError("Unknown mining candidate status")
        if status == "approved":
            raise ValueError("Only approve_bundle may approve a candidate")
        self._save_result(candidate, status, reason, urls, retry_hours, count_attempt=count_attempt)

    def _save_result(self, candidate, status, reason, urls, retry_hours=168, *, count_attempt=True):
        if not self.apply:
            row = dict(candidate)
            old = self._candidates.get(row.get("candidate_key") or candidate_key(row), row)
            if old.get("status") in _TERMINAL:
                return
            row.update(status=status, reason=reason, urls=urls,
                       attempts=row.get("attempts", 0) + int(count_attempt),
                       next_attempt_at=_utcnow() + timedelta(hours=retry_hours))
            self._candidates[row.get("candidate_key") or candidate_key(row)] = row
            return
        self._execute(
            """UPDATE site_mining_candidates SET status=%s,reason=%s,urls=%s,evidence=%s,
                 attempts=attempts+%s,next_attempt_at=%s,updated_at=UTC_TIMESTAMP()
               WHERE id=%s AND status IN ('pending','retry')""",
            (status, reason, _json(urls) if urls is not None else None, _json(candidate.get("evidence", {})), int(count_attempt),
             _utcnow() + timedelta(hours=retry_hours), candidate["id"]),
        )

    def _rejected_urls(self) -> set[str]:
        rows = self._all(
            "SELECT url,subpages FROM url_discoveries WHERE status = 'rejected'" +
            (" FOR UPDATE" if self.apply else "")
        )
        if "site_mining_candidates" in self._tables:
            rows += self._all(
                "SELECT url,urls AS subpages FROM site_mining_candidates WHERE status = 'rejected'" +
                (" FOR UPDATE" if self.apply else "")
            )
        rejected = set()
        for row in rows:
            values = [row.get("url")]
            subpages = row.get("subpages")
            if isinstance(subpages, (str, bytes)):
                try:
                    subpages = json.loads(subpages)
                except (ValueError, TypeError):
                    subpages = []
            if isinstance(subpages, list):
                values += [p.get("url") for p in subpages if isinstance(p, dict)]
            for value in values:
                if value and (key := url_key(value)):
                    rejected.add(key)
        return rejected

    @staticmethod
    def _expected_name(expected_name) -> tuple:
        if isinstance(expected_name, dict):
            return expected_name["first_name"], expected_name["last_name"]
        if isinstance(expected_name, (list, tuple)) and len(expected_name) == 2:
            return tuple(expected_name)
        raise ValueError("expected_name must contain exact first_name and last_name")

    def approve_bundle(self, candidate, urls, expected_name) -> dict:
        """Approve all URLs together, or persist a review hold without URL writes.

        Known URL rows are locked during the brief apply transaction. This also
        serializes the preflight with other URL writers that do not use our
        advisory lock; it is deliberately more conservative than INSERT IGNORE.
        """
        self._require_lock()
        result = {"inserted": [], "existing": [], "held": False, "reason": None}
        if self.apply:
            self.conn.start_transaction()
        try:
            reason, insertable, existing = self._preflight(candidate, urls, expected_name)
            result["existing"] = existing
            if reason:
                result.update(held=True, reason=reason)
                self._save_result(candidate, "review", reason, urls)
            else:
                for page in insertable:
                    row = {"researcher_id": candidate["researcher_id"],
                           "url": page["url"], "page_type": page["page_type"], "is_active": 1}
                    if self.apply:
                        row["id"], _ = self._execute(
                            """INSERT INTO researcher_urls
                               (researcher_id,url,page_type,is_active) VALUES (%s,%s,%s,1)""",
                            (row["researcher_id"], row["url"], row["page_type"]),
                        )
                    else:
                        row["id"] = -len(self._dry_url_rows) - 1
                        self._dry_url_rows.append(row)
                    result["inserted"].append(row)
                self._save_result(candidate, "approved", "Verified identity and crawl bundle", urls)
            if self.apply:
                self.conn.commit()
            return result
        except BaseException:
            if self.apply:
                self.conn.rollback()
            raise

    def _preflight(self, candidate, urls, expected_name):
        rid = candidate.get("researcher_id")
        if rid is None:
            return "New researchers require review", [], []
        if not urls or not any(p.get("page_type") == "HOME" for p in urls):
            return "Bundle has no verified HOME page", [], []
        person = self._one(
            "SELECT id,first_name,last_name FROM researchers WHERE id=%s" +
            (" FOR UPDATE" if self.apply else ""), (rid,)
        )
        if not person or (person["first_name"], person["last_name"]) != self._expected_name(expected_name):
            return "Researcher identity changed or is unresolved", [], []
        if self.apply:
            current = self._one(
                "SELECT id,status,researcher_id,url,urls FROM site_mining_candidates WHERE id=%s FOR UPDATE",
                (candidate["id"],),
            )
            if not current or current["researcher_id"] != rid or current["url"] != candidate["url"]:
                return "Candidate identity changed", [], []
            if current["status"] in {"review", "rejected"}:
                return f"Candidate already {current['status']}", [], []
            if current["status"] == "approved":
                approved_urls = _decode(current).get("urls") or []
                if sorted((p["url"], p["page_type"]) for p in urls) != sorted(
                    (p["url"], p["page_type"]) for p in approved_urls
                ):
                    return "Approved candidate cannot acquire a different bundle", [], []
        pages = []
        keys = set()
        for page in urls:
            url = page.get("url", "")
            key = url_key(url)
            if not key or len(url) > 2048 or page.get("page_type") not in _PAGE_TYPES:
                return "Invalid crawl URL or page type", [], []
            if key in keys:
                return "Duplicate URL aliases within crawl bundle", [], []
            keys.add(key)
            pages.append(page)
        rejected = self._rejected_urls()
        if keys & rejected:
            return "URL was previously rejected", [], []
        if "site_mining_candidates" in self._tables:
            holds = self._all(
                """SELECT researcher_id,url FROM site_mining_candidates
                   WHERE status IN ('review','rejected')
                     AND JSON_UNQUOTE(JSON_EXTRACT(evidence,'$.identity_hold')) = 'true'""" +
                (" FOR UPDATE" if self.apply else "")
            )
            if any(row["researcher_id"] == rid or url_key(row["url"]) in keys
                   or url_key(row["url"]) == url_key(candidate["url"]) for row in holds):
                return "Researcher has unresolved identity audit hold", [], []
        homes = [scope_for(p["url"]) for p in pages if p["page_type"] == "HOME"]
        if any(not any(same_owner(home, p["url"], explicit=True) for home in homes) for p in pages):
            return "Research page is outside its verified HOME scope", [], []
        known = self._all(
            "SELECT id,researcher_id,url,page_type,is_active FROM researcher_urls" +
            (" FOR UPDATE" if self.apply else "")
        ) + self._dry_url_rows
        by_key = {}
        scopes = [scope_for(p["url"]) for p in pages]
        hosts = set()
        research_subdomains = {"research", "papers", "publications", "workingpapers", "working-papers"}
        for scope in scopes:
            host = scope.host
            base = host.split(".", 1)[1] if host.split(".", 1)[0] in research_subdomains else host
            hosts.update({host, base, *(f"{prefix}.{base}" for prefix in research_subdomains)})
        scoped_known = []
        for row in known:
            if key := url_key(row["url"]):
                by_key.setdefault(key, []).append(row)
                if urlsplit(key).hostname in hosts:
                    scoped_known.append(row)
        existing, insertable = [], []
        for page in pages:
            key = url_key(page["url"])
            for row in by_key.get(key, []):
                if row["researcher_id"] != rid:
                    return "URL belongs to another researcher", [], []
                if not row["is_active"]:
                    return "URL is inactive and requires review", [], []
                if row["url"] != page["url"]:
                    return "Existing URL alias requires review", [], []
            if by_key.get(key):
                existing.extend(by_key[key])
            else:
                insertable.append(page)
        # Scope ownership includes different research paths on one personal
        # site, while policy preserves individual prefixes on academic hosts.
        for row in scoped_known:
            if row["researcher_id"] == rid or not row["is_active"]:
                continue
            if any(same_owner(scope, row["url"], explicit=True) for scope in scopes):
                return "Site scope has a different active researcher owner", [], []
            row_scope = scope_for(row["url"])
            if any(same_owner(row_scope, p["url"], explicit=True) for p in pages):
                return "Site scope has a different active researcher owner", [], []
        # Match MySQL's actual unique-index collation and prefix length, rather
        # than guessing how accents/case compare in Python.
        for i, page in enumerate(pages):
            prefix = self._all(
                """SELECT id,researcher_id,url,page_type,is_active FROM researcher_urls
                   WHERE LEFT(url,500) COLLATE utf8mb4_unicode_ci =
                     CONVERT(LEFT(%s,500) USING utf8mb4) COLLATE utf8mb4_unicode_ci""" +
                (" FOR UPDATE" if self.apply else ""),
                (page["url"],),
            )
            if any(r["url"] != page["url"] or r["researcher_id"] != rid or not r["is_active"] for r in prefix):
                return "MySQL URL prefix/collation collision", [], []
            for previous in pages[:i]:
                equal = self._one(
                    """SELECT CONVERT(LEFT(%s,500) USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                              CONVERT(LEFT(%s,500) USING utf8mb4) COLLATE utf8mb4_unicode_ci AS equal_prefix""",
                    (previous["url"], page["url"]),
                )
                if equal["equal_prefix"]:
                    return "MySQL URL prefix/collation collision within bundle", [], []
        return None, insertable, existing

    def start_run(self):
        self._require_lock()
        if not self.apply:
            return None
        # Holding the singleton lock proves earlier unfinished runs have died.
        # A container may stop between checkpoints without reaching finish_run.
        self._execute(
            """UPDATE site_mining_runs SET state='failed',finished=UTC_TIMESTAMP(),
                 stats=JSON_SET(COALESCE(stats,JSON_OBJECT()),'$.error',
                   'Worker interrupted before completion; durable queue resumed')
               WHERE state='running'"""
        )
        return self._execute("INSERT INTO site_mining_runs (started,state) VALUES (UTC_TIMESTAMP(),'running')")[0]

    def finish_run(self, run_id, stats, error=None):
        self._require_lock()
        if not self.apply:
            return
        details = dict(stats)
        if error:
            details["error"] = str(error)
        self._execute(
            "UPDATE site_mining_runs SET finished=UTC_TIMESTAMP(),state=%s,stats=%s WHERE id=%s",
            ("failed" if error else "complete", _json(details), run_id),
        )

    def status(self, limit=30):
        """Read-only operational/review summary, including before first apply."""
        if not self.acquired:
            return {"skipped": "another_mining_job_is_running"}
        result = {"counts": {}, "recent_runs": [], "review": []}
        if "site_mining_candidates" in self._tables:
            result["counts"] = {row["status"]: row["n"] for row in self._all(
                "SELECT status,COUNT(*) AS n FROM site_mining_candidates GROUP BY status"
            )}
            result["review"] = [_decode(row) for row in self._all(
                """SELECT id,researcher_id,name,url,source_url,source_kind,reason,
                          evidence,urls,attempts,updated_at
                   FROM site_mining_candidates WHERE status='review'
                   ORDER BY updated_at DESC,id DESC LIMIT %s""", (max(0, int(limit)),)
            )]
        if "site_mining_runs" in self._tables:
            result["recent_runs"] = self._all(
                "SELECT * FROM site_mining_runs ORDER BY id DESC LIMIT 10"
            )
            for row in result["recent_runs"]:
                if isinstance(row.get("stats"), (str, bytes)):
                    row["stats"] = json.loads(row["stats"])
        return result
