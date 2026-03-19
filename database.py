import hashlib
import mysql.connector
from mysql.connector import Error
from mysql.connector.pooling import MySQLConnectionPool
from db_config import db_config
import csv
import json
import logging
import os
import re
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(message)s')

_LLM_PRICING = {  # (prompt, completion) cost per 1M tokens
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
}

_pool: "MySQLConnectionPool | None" = None


_DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '5'))


def _get_pool() -> "MySQLConnectionPool":
    global _pool
    if _pool is None:
        _pool = MySQLConnectionPool(pool_size=_DB_POOL_SIZE, pool_name="econ_pool", **db_config)
    return _pool


class Database:
    @staticmethod
    def create_database():
        """
        Create the database if it doesn't exist.
        """
        conn = None
        try:
            conn = mysql.connector.connect(
                host=db_config['host'],
                user=db_config['user'],
                password=db_config['password']
            )
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_config['database']}`")
            logging.info(f"Database '{db_config['database']}' created or already exists.")
        except Error as e:
            logging.error("Error creating database: %s", type(e).__name__)
        finally:
            if conn is not None and conn.is_connected():
                cursor.close()
                conn.close()

    @staticmethod
    def get_connection():
        """
        Checkout and return a connection from the connection pool.
        """
        return _get_pool().get_connection()

    @staticmethod
    def execute_query(query, params=None):
        """
        Execute a query with optional parameters and commit the changes.
        Returns the last inserted row ID.
        """
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                conn.commit()
                return cursor.lastrowid

    @staticmethod
    def fetch_all(query, params=None):
        """
        Execute a query with optional parameters and fetch all results.
        Returns a list of tuples containing the results.
        """
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()

    @staticmethod
    def fetch_one(query, params=None):
        """
        Execute a SELECT query and fetch one result.
        """
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()

    @staticmethod
    def _migrate_fk_cascade(cursor, table, column, ref_table, ref_column):
        """Ensure FK on (table.column → ref_table.ref_column) has ON DELETE CASCADE.
        Idempotent: skips if CASCADE already in place."""
        cursor.execute(
            "SELECT rc.CONSTRAINT_NAME, rc.DELETE_RULE "
            "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
            "JOIN information_schema.KEY_COLUMN_USAGE kcu "
            "  ON rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
            "  AND rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA "
            "WHERE kcu.TABLE_SCHEMA = DATABASE() AND kcu.TABLE_NAME = %s "
            "AND kcu.COLUMN_NAME = %s AND kcu.REFERENCED_TABLE_NAME = %s",
            (table, column, ref_table),
        )
        row = cursor.fetchone()
        if row and row[1] == 'CASCADE':
            return  # Already correct

        # Drop existing FK(s) for this column
        cursor.execute(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND COLUMN_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
            (table, column),
        )
        for (name,) in cursor.fetchall():
            cursor.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{name}`")

        # Recreate with CASCADE
        cursor.execute(
            f"ALTER TABLE `{table}` ADD FOREIGN KEY (`{column}`) "
            f"REFERENCES `{ref_table}`(`{ref_column}`) ON DELETE CASCADE"
        )

    @staticmethod
    def create_tables():
        """
        Create the necessary tables if they do not already exist.
        """
        table_definitions = {
            "researchers": """
                CREATE TABLE IF NOT EXISTS researchers (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    last_name VARCHAR(255) NOT NULL,
                    first_name VARCHAR(255) NOT NULL,
                    position VARCHAR(255),
                    affiliation VARCHAR(255),
                    description TEXT,
                    description_updated_at DATETIME DEFAULT NULL,
                    INDEX idx_name (last_name, first_name),
                    INDEX idx_affiliation (affiliation)
                )
            """,
            "researcher_urls": """
                CREATE TABLE IF NOT EXISTS researcher_urls (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    researcher_id INT NOT NULL,
                    page_type VARCHAR(255) NOT NULL,
                    url VARCHAR(2048) NOT NULL,
                    UNIQUE KEY uq_researcher_url (researcher_id, url(500)),
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE
                )
            """,
            "papers": """
                CREATE TABLE IF NOT EXISTS papers (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    url VARCHAR(2048),
                    title TEXT,
                    title_hash CHAR(64) DEFAULT NULL,
                    year VARCHAR(4),
                    venue TEXT,
                    abstract TEXT DEFAULT NULL,
                    timestamp DATETIME,
                    status ENUM('published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper') DEFAULT NULL,
                    draft_url VARCHAR(2048) DEFAULT NULL,
                    draft_url_status ENUM('unchecked', 'valid', 'invalid', 'timeout') DEFAULT 'unchecked',
                    draft_url_checked_at DATETIME DEFAULT NULL,
                    is_seed BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE KEY uq_title_hash (title_hash),
                    INDEX idx_timestamp (timestamp),
                    INDEX idx_status (status),
                    INDEX idx_year (year),
                    INDEX idx_is_seed (is_seed)
                )
            """,
            "html_content": """
                CREATE TABLE IF NOT EXISTS html_content (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    url_id INT NOT NULL,
                    content MEDIUMTEXT,
                    content_hash VARCHAR(64),
                    timestamp DATETIME,
                    researcher_id INT,
                    extracted_at DATETIME,
                    extracted_hash VARCHAR(64),
                    UNIQUE KEY uq_url_id (url_id),
                    INDEX idx_url_id_ts (url_id, timestamp),
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
                    FOREIGN KEY (url_id) REFERENCES researcher_urls(id) ON DELETE CASCADE
                )
            """,
            "authorship": """
                CREATE TABLE IF NOT EXISTS authorship (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    researcher_id INT NOT NULL,
                    publication_id INT NOT NULL,
                    author_order INT,
                    UNIQUE KEY uq_researcher_pub (researcher_id, publication_id),
                    INDEX idx_researcher (researcher_id),
                    INDEX idx_publication (publication_id),
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
                    FOREIGN KEY (publication_id) REFERENCES papers(id) ON DELETE CASCADE
                )
            """,
            "research_fields": """
                CREATE TABLE IF NOT EXISTS research_fields (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    slug VARCHAR(255) NOT NULL,
                    UNIQUE KEY uq_slug (slug)
                )
            """,
            "researcher_fields": """
                CREATE TABLE IF NOT EXISTS researcher_fields (
                    researcher_id INT NOT NULL,
                    field_id INT NOT NULL,
                    PRIMARY KEY (researcher_id, field_id),
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
                    FOREIGN KEY (field_id) REFERENCES research_fields(id) ON DELETE CASCADE
                )
            """,
            "scrape_log": """
                CREATE TABLE IF NOT EXISTS scrape_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME,
                    status ENUM('running', 'completed', 'failed') DEFAULT 'running',
                    urls_checked INT DEFAULT 0,
                    urls_changed INT DEFAULT 0,
                    pubs_extracted INT DEFAULT 0,
                    prompt_tokens_total INT DEFAULT 0,
                    completion_tokens_total INT DEFAULT 0,
                    error_message TEXT,
                    INDEX idx_scrape_status (status)
                )
            """,
            "researcher_snapshots": """
                CREATE TABLE IF NOT EXISTS researcher_snapshots (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    researcher_id INT NOT NULL,
                    position VARCHAR(255),
                    affiliation VARCHAR(255),
                    description TEXT,
                    scraped_at DATETIME NOT NULL,
                    source_url VARCHAR(2048),
                    content_hash VARCHAR(64),
                    INDEX idx_researcher_time (researcher_id, scraped_at),
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE
                )
            """,
            "paper_snapshots": """
                CREATE TABLE IF NOT EXISTS paper_snapshots (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    paper_id INT NOT NULL,
                    status ENUM('published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper') DEFAULT NULL,
                    venue TEXT,
                    abstract TEXT,
                    draft_url VARCHAR(2048) DEFAULT NULL,
                    draft_url_status ENUM('unchecked', 'valid', 'invalid', 'timeout') DEFAULT 'unchecked',
                    year VARCHAR(4),
                    scraped_at DATETIME NOT NULL,
                    source_url VARCHAR(2048),
                    content_hash VARCHAR(64),
                    INDEX idx_paper_time (paper_id, scraped_at),
                    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
                )
            """,
            "paper_urls": """
                CREATE TABLE IF NOT EXISTS paper_urls (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    paper_id INT NOT NULL,
                    url VARCHAR(2048) NOT NULL,
                    discovered_at DATETIME NOT NULL,
                    UNIQUE KEY uq_paper_url (paper_id, url(500)),
                    INDEX idx_paper_id (paper_id),
                    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
                )
            """,
            "llm_usage": """
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    called_at DATETIME NOT NULL,
                    call_type ENUM('publication_extraction','description_extraction','researcher_disambiguation') NOT NULL,
                    model VARCHAR(100) NOT NULL,
                    prompt_tokens INT NOT NULL DEFAULT 0,
                    completion_tokens INT NOT NULL DEFAULT 0,
                    total_tokens INT NOT NULL DEFAULT 0,
                    estimated_cost_usd DECIMAL(10,6) DEFAULT NULL,
                    is_batch BOOLEAN NOT NULL DEFAULT FALSE,
                    context_url VARCHAR(2048) DEFAULT NULL,
                    researcher_id INT DEFAULT NULL,
                    scrape_log_id INT DEFAULT NULL,
                    batch_job_id INT DEFAULT NULL,
                    INDEX idx_called_at (called_at),
                    INDEX idx_call_type (call_type),
                    INDEX idx_scrape_log (scrape_log_id)
                )
            """,
            "feed_events": """
                CREATE TABLE IF NOT EXISTS feed_events (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    paper_id INT NOT NULL,
                    event_type ENUM('new_paper', 'status_change') NOT NULL,
                    old_status ENUM('published','accepted','revise_and_resubmit','reject_and_resubmit','working_paper') DEFAULT NULL,
                    new_status ENUM('published','accepted','revise_and_resubmit','reject_and_resubmit','working_paper') DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                    INDEX idx_paper_id (paper_id),
                    INDEX idx_created_at (created_at),
                    INDEX idx_event_type (event_type)
                )
            """,
            "batch_jobs": """
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    openai_batch_id VARCHAR(255) NOT NULL,
                    input_file_id VARCHAR(255) NOT NULL,
                    output_file_id VARCHAR(255) DEFAULT NULL,
                    status ENUM('submitted','validating','in_progress','finalizing','completed','failed','expired','cancelled') DEFAULT 'submitted',
                    url_count INT DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    completed_at DATETIME DEFAULT NULL,
                    prompt_tokens_total INT DEFAULT 0,
                    completion_tokens_total INT DEFAULT 0,
                    estimated_cost_usd DECIMAL(10,6) DEFAULT NULL,
                    error_message TEXT DEFAULT NULL,
                    UNIQUE KEY uq_batch_id (openai_batch_id),
                    INDEX idx_status (status)
                )
            """
        }

        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                for table_query in table_definitions.values():
                    cursor.execute(table_query)

        # Add columns to existing tables if they don't exist (migration for existing DBs).
        # Use a MySQL advisory lock so only one pod runs migrations on multi-pod startup.
        with Database.get_connection() as conn:
            with conn.cursor(buffered=True) as cursor:
                cursor.execute("SELECT GET_LOCK('econ_migrations', 10)")
                got_lock = cursor.fetchone()[0]
                if got_lock == 1:
                    try:
                        _migrations: list[tuple[str, str, str]] = [
                            ("scrape_log", "prompt_tokens_total", "INT DEFAULT 0"),
                            ("scrape_log", "completion_tokens_total", "INT DEFAULT 0"),
                            ("papers", "is_seed", "BOOLEAN NOT NULL DEFAULT FALSE"),
                        ]
                        for table, col, definition in _migrations:
                            try:
                                cursor.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {col} {definition}"
                                )
                                conn.commit()
                            except Exception as e:
                                if getattr(e, 'errno', None) != 1060:
                                    logging.warning("Migration warning for %s.%s: %s", table, col, e)
                        # Add index on is_seed if it doesn't exist
                        try:
                            cursor.execute(
                                "ALTER TABLE papers ADD INDEX idx_is_seed (is_seed)"
                            )
                            conn.commit()
                        except Exception as e:
                            if getattr(e, 'errno', None) != 1061:  # 1061 = Duplicate key name
                                logging.warning("Migration warning for papers.idx_is_seed: %s", e)

                        # Migrate FKs to ON DELETE CASCADE
                        _cascade_fks = [
                            ("researcher_urls", "researcher_id", "researchers", "id"),
                            ("html_content", "researcher_id", "researchers", "id"),
                            ("html_content", "url_id", "researcher_urls", "id"),
                            ("authorship", "researcher_id", "researchers", "id"),
                            ("authorship", "publication_id", "papers", "id"),
                            ("researcher_fields", "researcher_id", "researchers", "id"),
                            ("researcher_fields", "field_id", "research_fields", "id"),
                        ]
                        for table, col, ref_table, ref_col in _cascade_fks:
                            try:
                                Database._migrate_fk_cascade(cursor, table, col, ref_table, ref_col)
                                conn.commit()
                            except Exception as e:
                                logging.warning("Migration: CASCADE for %s.%s: %s", table, col, e)

                        # Add index on scrape_log.status
                        try:
                            cursor.execute("ALTER TABLE scrape_log ADD INDEX idx_scrape_status (status)")
                            conn.commit()
                        except Exception as e:
                            if getattr(e, 'errno', None) != 1061:  # Duplicate key name
                                logging.warning("Migration: scrape_log.idx_scrape_status: %s", e)

                        # Downgrade html_content.content from LONGTEXT to MEDIUMTEXT
                        try:
                            cursor.execute("ALTER TABLE html_content MODIFY content MEDIUMTEXT")
                            conn.commit()
                        except Exception as e:
                            logging.warning("Migration: html_content.content type: %s", e)

                        # Backfill seed publications if any unseeded papers exist
                        cursor.execute(
                            "SELECT COUNT(*) FROM papers WHERE is_seed = FALSE"
                        )
                        total_unseeded = cursor.fetchone()[0]
                        if total_unseeded > 0:
                            cursor.execute(
                                "SELECT COUNT(*) FROM papers WHERE is_seed = TRUE"
                            )
                            already_seeded = cursor.fetchone()[0]
                            if already_seeded == 0:
                                logging.info("Backfilling seed publications...")
                                Database.backfill_seed_publications()
                                logging.info("Seed backfill complete")
                    finally:
                        cursor.execute("SELECT RELEASE_LOCK('econ_migrations')")
                        cursor.fetchone()
                else:
                    logging.info("Skipping migrations — another pod holds the lock")

        logging.info("All tables created successfully")
        Database.seed_research_fields()

    @staticmethod
    def seed_research_fields():
        """Insert the initial research field taxonomy if not already present."""
        fields = [
            ("Macroeconomics", "macroeconomics"),
            ("Labour Economics", "labour-economics"),
            ("Cultural Economics", "cultural-economics"),
            ("Migration", "migration"),
            ("Political Economy", "political-economy"),
            ("Development Economics", "development-economics"),
            ("International Trade", "international-trade"),
            ("Finance", "finance"),
            ("Health Economics", "health-economics"),
            ("Public Economics", "public-economics"),
            ("Industrial Organisation", "industrial-organisation"),
            ("Econometrics/Methods", "econometrics-methods"),
        ]
        for name, slug in fields:
            Database.execute_query(
                "INSERT IGNORE INTO research_fields (name, slug) VALUES (%s, %s)",
                (name, slug),
            )

    @staticmethod
    def backfill_seed_publications() -> int:
        """Mark all existing publications as seed.

        Since html_content stores only one row per URL (upsert), we
        cannot reliably distinguish first-scrape from re-scrape papers
        using timestamps alone (researchers with multiple URLs get
        papers at different times within the same session).

        The safe approach: mark ALL existing papers as seed on first
        run.  Going forward, the scheduler sets is_seed=True only
        when old_text is None (first-ever scrape of a URL), so new
        papers found on re-scrapes will correctly be is_seed=False.
        """
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE papers SET is_seed = TRUE WHERE is_seed = FALSE"
                )
                conn.commit()
                return cursor.rowcount

    @staticmethod
    def log_llm_usage(call_type, model, usage, context_url=None, researcher_id=None,
                      scrape_log_id=None, is_batch=False, batch_job_id=None):
        """Log an LLM API call with token counts and estimated cost. Failures are silenced."""
        try:
            prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
            completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
            total_tokens = getattr(usage, 'total_tokens', 0) or (prompt_tokens + completion_tokens)
            pricing = _LLM_PRICING.get(model)
            if pricing:
                prompt_rate, completion_rate = pricing
                multiplier = 0.5 if is_batch else 1.0
                estimated_cost = multiplier * (
                    prompt_tokens * prompt_rate / 1_000_000
                    + completion_tokens * completion_rate / 1_000_000
                )
            else:
                estimated_cost = None
                logging.warning("No pricing entry for model '%s' — cost will be NULL", model)
            Database.execute_query(
                """INSERT INTO llm_usage
                   (called_at, call_type, model, prompt_tokens, completion_tokens,
                    total_tokens, estimated_cost_usd, is_batch, context_url,
                    researcher_id, scrape_log_id, batch_job_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (datetime.now(timezone.utc), call_type, model, prompt_tokens,
                 completion_tokens, total_tokens, estimated_cost, is_batch,
                 context_url, researcher_id, scrape_log_id, batch_job_id),
            )
        except Exception as e:
            logging.warning(f"log_llm_usage failed (non-fatal): {e}")

    @staticmethod
    def _disambiguate_researcher(first_name, last_name, candidates):
        """
        Use LLM to check if any same-last-name candidate is the same person as first_name last_name.
        Returns the matching researcher id (int) or None if no match.
        candidates: list of (id, first_name, last_name)
        """
        candidates_text = "\n".join(f"- ID {c[0]}: {c[1]} {c[2]}" for c in candidates)
        prompt = (
            f'You are disambiguating researcher names. A publication lists the author as: '
            f'"{first_name} {last_name}"\n\n'
            f'The database contains these existing researchers with the same last name:\n'
            f'{candidates_text}\n\n'
            f'Is the author the same person as any of these researchers? Consider:\n'
            f'- "J. Smith" and "John Smith" are likely the same person\n'
            f'- An abbreviated first name may match a full first name\n'
            f'- Only match if you are confident\n\n'
            f'Respond with JSON only: {{"match_id": <id or null>}}'
        )
        try:
            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
            model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model,
            )
            Database.log_llm_usage(
                "researcher_disambiguation", model, response.usage,
            )
            content = response.choices[0].message.content
            match = re.search(r'\{.*?\}', content, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                match_id = data.get('match_id')
                if match_id is not None:
                    candidate_ids = {c[0] for c in candidates}
                    match_id_int = int(match_id)
                    if match_id_int in candidate_ids:
                        return match_id_int
                    logging.warning(
                        f"LLM returned match_id={match_id} not in candidate IDs {candidate_ids}; ignoring"
                    )
        except Exception as e:
            logging.error(f"LLM researcher disambiguation error: {e}")
        return None

    @staticmethod
    def get_researcher_id(first_name, last_name, position=None, affiliation=None):
        """
        Get the researcher ID based on the first name and last name.
        If the researcher does not exist, query same-last-name candidates and use LLM
        to disambiguate abbreviated vs full names before inserting a new row.
        """
        # 1. Exact match
        result = Database.fetch_one(
            "SELECT id FROM researchers WHERE first_name = %s AND last_name = %s",
            (first_name, last_name),
        )
        if result:
            return result[0]

        # 2. Same-last-name candidates — let LLM decide if any is the same person
        candidates = Database.fetch_all(
            "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
            (last_name,),
        )
        if candidates:
            match_id = Database._disambiguate_researcher(first_name, last_name, candidates)
            if match_id is not None:
                logging.info(
                    f"LLM matched '{first_name} {last_name}' to existing researcher id={match_id}"
                )
                return match_id

        # 3. No match found — insert new researcher
        insert_query = """
            INSERT INTO researchers (first_name, last_name, position, affiliation)
            VALUES (%s, %s, %s, %s)
        """
        new_id = Database.execute_query(insert_query, (first_name, last_name, position, affiliation))
        return new_id

    @staticmethod
    def update_researcher_bio(researcher_id, bio):
        """Legacy: update researcher description only if the current description is NULL."""
        Database.execute_query(
            "UPDATE researchers SET description = %s WHERE id = %s AND description IS NULL",
            (bio, researcher_id),
        )

    # ── Title normalization for cross-researcher dedup ──

    @staticmethod
    def normalize_title(title):
        """Normalize a title for dedup: lowercase, strip punctuation, collapse whitespace."""
        if not title:
            return ''
        t = title.lower().strip()
        t = re.sub(r'[^a-z0-9\s]', '', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    @staticmethod
    def compute_title_hash(title):
        """SHA-256 hash of normalized title for cross-researcher dedup."""
        normalized = Database.normalize_title(title)
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    # ── Researcher snapshot (append-only versioning) ──

    @staticmethod
    def _compute_researcher_content_hash(position, affiliation, description):
        """Compute content hash for researcher change detection."""
        parts = '||'.join(str(v or '') for v in (position, affiliation, description))
        return hashlib.sha256(parts.encode('utf-8')).hexdigest()

    @staticmethod
    def get_latest_researcher_snapshot_hash(researcher_id):
        """Return the content_hash of the most recent snapshot, or None."""
        result = Database.fetch_one(
            "SELECT content_hash FROM researcher_snapshots "
            "WHERE researcher_id = %s ORDER BY scraped_at DESC LIMIT 1",
            (researcher_id,),
        )
        return result[0] if result else None

    @staticmethod
    def append_researcher_snapshot(researcher_id, position, affiliation, description, source_url=None):
        """Append a snapshot if profile changed. Updates denormalized researchers table.
        Both operations run in a single transaction for consistency.
        Returns True if a new snapshot was inserted, False if no change."""
        content_hash = Database._compute_researcher_content_hash(position, affiliation, description)
        prev_hash = Database.get_latest_researcher_snapshot_hash(researcher_id)

        if prev_hash == content_hash:
            return False

        now = datetime.now(timezone.utc)
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO researcher_snapshots
                       (researcher_id, position, affiliation, description, scraped_at, source_url, content_hash)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (researcher_id, position, affiliation, description, now, source_url, content_hash),
                )
                cursor.execute(
                    """UPDATE researchers
                       SET position = %s, affiliation = %s, description = %s, description_updated_at = %s
                       WHERE id = %s""",
                    (position, affiliation, description, now, researcher_id),
                )
                conn.commit()
        logging.info(f"Researcher snapshot appended for id={researcher_id}")
        return True

    @staticmethod
    def get_researcher_snapshots(researcher_id, limit=20):
        """Return recent snapshots for a researcher, newest first."""
        return Database.fetch_all(
            """SELECT position, affiliation, description, scraped_at, source_url
               FROM researcher_snapshots WHERE researcher_id = %s
               ORDER BY scraped_at DESC LIMIT %s""",
            (researcher_id, limit),
        )

    # ── Paper snapshot (append-only versioning) ──

    @staticmethod
    def _compute_paper_content_hash(status, venue, abstract, draft_url, year):
        """Compute content hash for paper change detection."""
        parts = '||'.join(str(v or '') for v in (status, venue, abstract, draft_url, year))
        return hashlib.sha256(parts.encode('utf-8')).hexdigest()

    @staticmethod
    def get_latest_paper_snapshot_hash(paper_id):
        """Return the content_hash of the most recent paper snapshot, or None."""
        result = Database.fetch_one(
            "SELECT content_hash FROM paper_snapshots "
            "WHERE paper_id = %s ORDER BY scraped_at DESC LIMIT 1",
            (paper_id,),
        )
        return result[0] if result else None

    @staticmethod
    def append_paper_snapshot(paper_id, status, venue, abstract, draft_url, year, source_url=None):
        """Append a paper snapshot if metadata changed. Updates denormalized papers table.
        Creates a feed_event if status changed.
        All operations run in a single transaction for consistency.
        Returns True if a new snapshot was inserted, False if no change."""
        content_hash = Database._compute_paper_content_hash(status, venue, abstract, draft_url, year)
        prev_hash = Database.get_latest_paper_snapshot_hash(paper_id)

        if prev_hash == content_hash:
            return False

        now = datetime.now(timezone.utc)
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                # Fetch previous status before inserting new snapshot
                cursor.execute(
                    "SELECT status FROM paper_snapshots WHERE paper_id = %s "
                    "ORDER BY scraped_at DESC LIMIT 1",
                    (paper_id,),
                )
                prev_row = cursor.fetchone()
                old_status = prev_row[0] if prev_row else None

                cursor.execute(
                    """INSERT INTO paper_snapshots
                       (paper_id, status, venue, abstract, draft_url, draft_url_status, year,
                        scraped_at, source_url, content_hash)
                       VALUES (%s, %s, %s, %s, %s, 'unchecked', %s, %s, %s, %s)""",
                    (paper_id, status, venue, abstract, draft_url, year, now, source_url, content_hash),
                )
                cursor.execute(
                    """UPDATE papers
                       SET status = %s, venue = %s, abstract = %s, draft_url = %s,
                           draft_url_status = 'unchecked', year = %s
                       WHERE id = %s""",
                    (status, venue, abstract, draft_url, year, paper_id),
                )

                # Create status_change feed event if status actually changed
                if (old_status != status
                        and old_status is not None
                        and status is not None):
                    cursor.execute(
                        """INSERT INTO feed_events
                           (paper_id, event_type, old_status, new_status, created_at)
                           VALUES (%s, 'status_change', %s, %s, %s)""",
                        (paper_id, old_status, status, now),
                    )

                conn.commit()
        logging.info(f"Paper snapshot appended for id={paper_id}")
        return True

    @staticmethod
    def get_paper_snapshots(paper_id, limit=20):
        """Return recent snapshots for a paper, newest first."""
        return Database.fetch_all(
            """SELECT status, venue, abstract, draft_url, draft_url_status, year, scraped_at, source_url
               FROM paper_snapshots WHERE paper_id = %s
               ORDER BY scraped_at DESC LIMIT %s""",
            (paper_id, limit),
        )

    # ── Draft URL validation ──

    @staticmethod
    def update_draft_url_status(paper_id, status):
        """Update draft URL validation status."""
        Database.execute_query(
            "UPDATE papers SET draft_url_status = %s, draft_url_checked_at = %s WHERE id = %s",
            (status, datetime.now(timezone.utc), paper_id),
        )

    @staticmethod
    def get_unchecked_draft_urls(limit=100):
        """Get papers with unchecked draft URLs for validation."""
        return Database.fetch_all(
            """SELECT id, draft_url FROM papers
               WHERE draft_url IS NOT NULL AND draft_url_status = 'unchecked'
               LIMIT %s""",
            (limit,),
        )

    @staticmethod
    def add_researcher_url(researcher_id, page_type, url):
        """
        Insert a new URL for a researcher into the researcher_urls table.
        """
        insert_query = """
            INSERT IGNORE INTO researcher_urls (researcher_id, page_type, url)
            VALUES (%s, %s, %s)
        """
        params = (researcher_id, page_type, url)
        Database.execute_query(insert_query, params)

    @staticmethod
    def import_data_from_file(file_path):
        """
        Import data from a CSV or TXT file into the database.
        """
        try:
            with open(file_path, mode='r', encoding='utf-8-sig') as file:
                reader = csv.reader(file)
                header = next(reader, None)  # Skip header if present
                for row in reader:
                    # Assuming the file has columns: first_name, last_name, position, affiliation, page_type, url
                    if len(row) < 6:
                        logging.warning(f"Skipping incomplete row: {row}")
                        continue
                    first_name, last_name, position, affiliation, page_type, url = row
                    researcher_id = Database.get_researcher_id(first_name, last_name, position, affiliation)
                    Database.add_researcher_url(researcher_id, page_type, url)
            logging.info("Data imported successfully from file")
        except Exception as e:
            logging.error("Error importing data from file: %s", type(e).__name__)

# Example usage:
if __name__ == "__main__":
    Database.create_database()
    Database.create_tables()
    Database.import_data_from_file('urls.csv')
