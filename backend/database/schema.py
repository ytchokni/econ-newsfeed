"""Schema creation, migrations, and seeding."""
from __future__ import annotations

import logging

import mysql.connector
from mysql.connector import Error

from backend.config import db_config
from backend.database.connection import get_connection, execute_query
from backend.database.snapshots import STATUS_ORDER


def create_database() -> None:
    """Create the database if it doesn't exist."""
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


def _migrate_fk_cascade(cursor: object, table: str, column: str, ref_table: str,
                        ref_column: str) -> None:
    """Ensure FK on (table.column -> ref_table.ref_column) has ON DELETE CASCADE.
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
        return

    cursor.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND COLUMN_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
        (table, column),
    )
    for (name,) in cursor.fetchall():
        cursor.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{name}`")

    cursor.execute(
        f"ALTER TABLE `{table}` ADD FOREIGN KEY (`{column}`) "
        f"REFERENCES `{ref_table}`(`{ref_column}`) ON DELETE CASCADE"
    )


_TABLE_DEFINITIONS = {
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "researcher_urls": """
        CREATE TABLE IF NOT EXISTS researcher_urls (
            id INT AUTO_INCREMENT PRIMARY KEY,
            researcher_id INT NOT NULL,
            page_type VARCHAR(255) NOT NULL,
            url VARCHAR(2048) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            consecutive_failures INT NOT NULL DEFAULT 0,
            deactivated_at DATETIME DEFAULT NULL,
            deactivation_reason VARCHAR(255) DEFAULT NULL,
            UNIQUE KEY uq_researcher_url (researcher_id, url(500)),
            FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
            INDEX idx_is_active (is_active)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "papers": """
        CREATE TABLE IF NOT EXISTS papers (
            id INT AUTO_INCREMENT PRIMARY KEY,
            source_url VARCHAR(2048),
            title TEXT,
            title_hash CHAR(64) DEFAULT NULL,
            year VARCHAR(4),
            venue TEXT,
            abstract TEXT DEFAULT NULL,
            discovered_at DATETIME,
            status ENUM('published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper', 'work_in_progress') DEFAULT NULL,
            draft_url VARCHAR(2048) DEFAULT NULL,
            draft_url_status ENUM('unchecked', 'valid', 'invalid', 'timeout') DEFAULT 'unchecked',
            draft_url_checked_at DATETIME DEFAULT NULL,
            is_seed BOOLEAN NOT NULL DEFAULT FALSE,
            doi VARCHAR(255) DEFAULT NULL,
            openalex_id VARCHAR(255) DEFAULT NULL,
            UNIQUE KEY uq_title_hash (title_hash),
            INDEX idx_discovered_at (discovered_at),
            INDEX idx_status (status),
            INDEX idx_year (year),
            INDEX idx_is_seed (is_seed)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "html_snapshots": """
        CREATE TABLE IF NOT EXISTS html_snapshots (
            id INT AUTO_INCREMENT PRIMARY KEY,
            url_id INT NOT NULL,
            text_content_hash VARCHAR(64) NOT NULL,
            raw_html_hash VARCHAR(64) NOT NULL,
            raw_html_compressed MEDIUMBLOB NOT NULL,
            snapshot_at DATETIME NOT NULL,
            UNIQUE KEY uq_url_snapshot (url_id, text_content_hash),
            FOREIGN KEY (url_id) REFERENCES researcher_urls(id) ON DELETE CASCADE,
            INDEX idx_url_id_snapshot (url_id, snapshot_at DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "research_fields": """
        CREATE TABLE IF NOT EXISTS research_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            slug VARCHAR(255) NOT NULL,
            UNIQUE KEY uq_slug (slug)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "researcher_fields": """
        CREATE TABLE IF NOT EXISTS researcher_fields (
            researcher_id INT NOT NULL,
            field_id INT NOT NULL,
            PRIMARY KEY (researcher_id, field_id),
            FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
            FOREIGN KEY (field_id) REFERENCES research_fields(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "jel_codes": """
        CREATE TABLE IF NOT EXISTS jel_codes (
            code VARCHAR(10) NOT NULL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            parent_code VARCHAR(10) DEFAULT NULL,
            INDEX idx_parent (parent_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "researcher_jel_codes": """
        CREATE TABLE IF NOT EXISTS researcher_jel_codes (
            researcher_id INT NOT NULL,
            jel_code VARCHAR(10) NOT NULL,
            classified_at DATETIME NOT NULL,
            PRIMARY KEY (researcher_id, jel_code),
            FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE,
            FOREIGN KEY (jel_code) REFERENCES jel_codes(code) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "paper_topics": """
        CREATE TABLE IF NOT EXISTS paper_topics (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_id INT NOT NULL,
            openalex_topic_id VARCHAR(255) NOT NULL,
            topic_name VARCHAR(500) NOT NULL,
            subfield_name VARCHAR(255) DEFAULT NULL,
            field_name VARCHAR(255) DEFAULT NULL,
            domain_name VARCHAR(255) DEFAULT NULL,
            score DECIMAL(5,4) DEFAULT NULL,
            UNIQUE KEY uq_paper_topic (paper_id, openalex_topic_id),
            INDEX idx_paper_id (paper_id),
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "paper_snapshots": """
        CREATE TABLE IF NOT EXISTS paper_snapshots (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_id INT NOT NULL,
            title TEXT DEFAULT NULL,
            status ENUM('published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper', 'work_in_progress') DEFAULT NULL,
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "llm_usage": """
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INT AUTO_INCREMENT PRIMARY KEY,
            called_at DATETIME NOT NULL,
            call_type ENUM('publication_extraction','description_extraction','researcher_disambiguation','jel_classification','diff_extraction') NOT NULL,
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "feed_events": """
        CREATE TABLE IF NOT EXISTS feed_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_id INT NOT NULL,
            event_type ENUM('new_paper', 'status_change', 'title_change') NOT NULL,
            old_status ENUM('published','accepted','revise_and_resubmit','reject_and_resubmit','working_paper','work_in_progress') DEFAULT NULL,
            new_status ENUM('published','accepted','revise_and_resubmit','reject_and_resubmit','working_paper','work_in_progress') DEFAULT NULL,
            old_title TEXT DEFAULT NULL,
            new_title TEXT DEFAULT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
            INDEX idx_paper_id (paper_id),
            INDEX idx_created_at (created_at),
            INDEX idx_event_type (event_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "openalex_coauthors": """
        CREATE TABLE IF NOT EXISTS openalex_coauthors (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_id INT NOT NULL,
            display_name VARCHAR(500) NOT NULL,
            openalex_author_id VARCHAR(255) DEFAULT NULL,
            UNIQUE KEY uq_paper_name (paper_id, display_name(200)),
            INDEX idx_paper_id (paper_id),
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "paper_links": """
        CREATE TABLE IF NOT EXISTS paper_links (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_id INT NOT NULL,
            url VARCHAR(2048) NOT NULL,
            link_type ENUM('pdf', 'ssrn', 'nber', 'arxiv', 'doi', 'journal',
                            'drive', 'dropbox', 'repository', 'other') DEFAULT NULL,
            doi VARCHAR(255) DEFAULT NULL,
            discovered_at DATETIME NOT NULL,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
            UNIQUE KEY uq_paper_link (paper_id, url(500))
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            google_id VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL,
            name VARCHAR(255),
            picture_url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_google_id (google_id),
            INDEX idx_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "user_follows": """
        CREATE TABLE IF NOT EXISTS user_follows (
            user_id INT NOT NULL,
            researcher_id INT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, researcher_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (researcher_id) REFERENCES researchers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "user_notification_prefs": """
        CREATE TABLE IF NOT EXISTS user_notification_prefs (
            user_id INT PRIMARY KEY,
            digest_enabled BOOLEAN DEFAULT TRUE,
            last_digest_sent DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "newsletter_emails": """
        CREATE TABLE IF NOT EXISTS newsletter_emails (
            id INT AUTO_INCREMENT PRIMARY KEY,
            gmail_msg_id VARCHAR(255) NOT NULL,
            subject VARCHAR(500),
            papers_saved INT DEFAULT 0,
            processed_at DATETIME NOT NULL,
            UNIQUE KEY uq_gmail_msg_id (gmail_msg_id),
            INDEX idx_processed_at (processed_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
}


def create_tables() -> None:
    """Create all tables and run migrations."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            for table_query in _TABLE_DEFINITIONS.values():
                cursor.execute(table_query)

    # Migrations under advisory lock
    with get_connection() as conn:
        with conn.cursor(buffered=True) as cursor:
            cursor.execute("SELECT GET_LOCK('econ_migrations', 10)")
            got_lock = cursor.fetchone()[0]
            if got_lock == 1:
                try:
                    _migrations = [
                        ("scrape_log", "prompt_tokens_total", "INT DEFAULT 0"),
                        ("scrape_log", "completion_tokens_total", "INT DEFAULT 0"),
                        ("papers", "is_seed", "BOOLEAN NOT NULL DEFAULT FALSE"),
                        ("papers", "doi", "VARCHAR(255) DEFAULT NULL"),
                        ("papers", "openalex_id", "VARCHAR(255) DEFAULT NULL"),
                    ]
                    for table, col, definition in _migrations:
                        try:
                            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                            conn.commit()
                        except Exception as e:
                            if getattr(e, 'errno', None) != 1060:
                                logging.warning("Migration warning for %s.%s: %s", table, col, e)

                    try:
                        cursor.execute("ALTER TABLE papers ADD INDEX idx_is_seed (is_seed)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration warning for papers.idx_is_seed: %s", e)

                    try:
                        cursor.execute("ALTER TABLE papers ADD FULLTEXT INDEX ft_title_abstract (title, abstract)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration warning for papers.ft_title_abstract: %s", e)

                    try:
                        cursor.execute("ALTER TABLE researchers ADD FULLTEXT INDEX ft_name (first_name, last_name)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration warning for researchers.ft_name: %s", e)

                    try:
                        cursor.execute("ALTER TABLE feed_events ADD INDEX idx_event_type_created (event_type, created_at DESC)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration warning for feed_events.idx_event_type_created: %s", e)

                    try:
                        cursor.execute("ALTER TABLE feed_events ADD INDEX idx_created_paper (created_at DESC, paper_id)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration warning for feed_events.idx_created_paper: %s", e)

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
                            _migrate_fk_cascade(cursor, table, col, ref_table, ref_col)
                            conn.commit()
                        except Exception as e:
                            logging.warning("Migration: CASCADE for %s.%s: %s", table, col, e)

                    try:
                        cursor.execute("ALTER TABLE scrape_log ADD INDEX idx_scrape_status (status)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration: scrape_log.idx_scrape_status: %s", e)

                    try:
                        cursor.execute("ALTER TABLE html_content MODIFY content MEDIUMTEXT")
                        conn.commit()
                    except Exception as e:
                        logging.warning("Migration: html_content.content type: %s", e)

                    cursor.execute("SELECT COUNT(*) FROM papers WHERE is_seed = FALSE")
                    total_unseeded = cursor.fetchone()[0]
                    if total_unseeded > 0:
                        cursor.execute("SELECT COUNT(*) FROM papers WHERE is_seed = TRUE")
                        already_seeded = cursor.fetchone()[0]
                        if already_seeded == 0:
                            logging.info("Backfilling seed publications...")
                            backfill_seed_publications()
                            logging.info("Seed backfill complete")

                    _ALL_TABLES = [
                        "researchers", "researcher_urls", "papers", "html_content",
                        "html_snapshots",
                        "authorship", "research_fields", "researcher_fields",
                        "jel_codes", "researcher_jel_codes",
                        "scrape_log", "researcher_snapshots", "paper_snapshots",
                        "paper_urls", "llm_usage", "feed_events", "batch_jobs",
                        "openalex_coauthors",
                        "paper_links",
                        "paper_topics",
                        "users",
                        "user_follows",
                        "user_notification_prefs",
                        "newsletter_emails",
                    ]
                    for tbl in _ALL_TABLES:
                        try:
                            cursor.execute(
                                f"ALTER TABLE `{tbl}` CONVERT TO CHARACTER SET utf8mb4 "
                                f"COLLATE utf8mb4_unicode_ci"
                            )
                            conn.commit()
                        except Exception as e:
                            logging.warning("Migration: utf8mb4 for %s: %s", tbl, e)

                    # Extend llm_usage.call_type ENUM (jel_classification, diff_extraction).
                    # Without diff_extraction, every diff-path usage INSERT fails with
                    # MySQL 1265 and is silently dropped by log_llm_usage (seen in prod
                    # 2026-06-12: ~350 untracked calls overnight)
                    try:
                        cursor.execute(
                            "ALTER TABLE llm_usage MODIFY COLUMN call_type "
                            "ENUM('publication_extraction','description_extraction',"
                            "'researcher_disambiguation','jel_classification',"
                            "'diff_extraction') NOT NULL"
                        )
                        conn.commit()
                    except Exception as e:
                        logging.warning("Migration: llm_usage.call_type ENUM: %s", e)

                    # Rename papers.url → papers.source_url
                    try:
                        cursor.execute(
                            "SELECT COUNT(*) FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'papers' "
                            "AND COLUMN_NAME = 'url'"
                        )
                        if cursor.fetchone()[0] > 0:
                            cursor.execute("ALTER TABLE papers RENAME COLUMN url TO source_url")
                            conn.commit()
                    except Exception as e:
                        logging.warning("Migration: papers.url rename: %s", e)

                    # Rename papers.timestamp → papers.discovered_at
                    try:
                        cursor.execute(
                            "SELECT COUNT(*) FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'papers' "
                            "AND COLUMN_NAME = 'timestamp'"
                        )
                        if cursor.fetchone()[0] > 0:
                            cursor.execute("ALTER TABLE papers RENAME COLUMN `timestamp` TO discovered_at")
                            try:
                                cursor.execute("ALTER TABLE papers DROP INDEX idx_timestamp")
                            except Exception:
                                pass
                            cursor.execute("ALTER TABLE papers ADD INDEX idx_discovered_at (discovered_at)")
                            conn.commit()
                    except Exception as e:
                        logging.warning("Migration: papers.timestamp rename: %s", e)

                    try:
                        cursor.execute("""
                            ALTER TABLE html_content
                            ADD COLUMN raw_html MEDIUMTEXT DEFAULT NULL AFTER content
                        """)
                        logging.info("Added raw_html column to html_content")
                        conn.commit()
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: html_content.raw_html: %s", e)

                    # Add doi column to paper_links
                    try:
                        cursor.execute("""
                            ALTER TABLE paper_links
                            ADD COLUMN doi VARCHAR(255) DEFAULT NULL AFTER link_type
                        """)
                        conn.commit()
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: paper_links.doi: %s", e)

                    # Add openalex_author_id to researchers
                    try:
                        cursor.execute("""
                            ALTER TABLE researchers
                            ADD COLUMN openalex_author_id VARCHAR(255) DEFAULT NULL
                        """)
                        conn.commit()
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: researchers.openalex_author_id: %s", e)

                    # DB-level safety net: checks snapshot count only (≥2 snapshots required).
                    # Full validation (title-in-previous-snapshot) is in
                    # publication._title_in_previous_snapshot() — MySQL triggers cannot
                    # decompress zlib blobs, so this is a coarse guard only.
                    try:
                        cursor.execute("DROP TRIGGER IF EXISTS trg_feed_events_snapshot_guard")
                        cursor.execute("""
                            CREATE TRIGGER trg_feed_events_snapshot_guard
                            BEFORE INSERT ON feed_events
                            FOR EACH ROW
                            BEGIN
                                DECLARE v_source_url VARCHAR(2048) CHARACTER SET utf8mb4
                                    COLLATE utf8mb4_unicode_ci;
                                DECLARE v_snapshot_count INT DEFAULT 0;

                                IF NEW.event_type = 'new_paper' THEN
                                    SELECT source_url INTO v_source_url
                                    FROM papers WHERE id = NEW.paper_id;

                                    IF v_source_url IS NOT NULL
                                       AND v_source_url NOT LIKE 'newsletter://%' THEN
                                        SELECT COALESCE(MAX(cnt), 0) INTO v_snapshot_count
                                        FROM (
                                            SELECT COUNT(*) AS cnt
                                            FROM html_snapshots
                                            WHERE url_id IN (
                                                SELECT id FROM researcher_urls
                                                WHERE url = v_source_url
                                                    COLLATE utf8mb4_unicode_ci
                                            )
                                            GROUP BY url_id
                                        ) sub;
                                    END IF;

                                    IF v_snapshot_count < 2 THEN
                                        SIGNAL SQLSTATE '45000'
                                        SET MESSAGE_TEXT = 'new_paper blocked: source URL has < 2 snapshots';
                                    END IF;
                                END IF;
                            END
                        """)
                        conn.commit()
                        logging.info("Migration: feed_events snapshot guard trigger created")
                    except Exception as e:
                        # Without the trigger the DB-level new_paper guard silently
                        # does not exist (this hid MySQL error 1419 in prod for months)
                        logging.error("Migration: feed_events trigger NOT created: %s", e)

                    # Add title column to paper_snapshots for rename tracking
                    try:
                        cursor.execute("""
                            ALTER TABLE paper_snapshots
                            ADD COLUMN title TEXT DEFAULT NULL AFTER paper_id
                        """)
                        conn.commit()
                        logging.info("Migration: added title column to paper_snapshots")
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: paper_snapshots.title: %s", e)

                    # Add title_change event type to feed_events
                    try:
                        cursor.execute("""
                            ALTER TABLE feed_events MODIFY COLUMN event_type
                            ENUM('new_paper', 'status_change', 'title_change') NOT NULL
                        """)
                        conn.commit()
                        logging.info("Migration: added title_change to feed_events.event_type")
                    except Exception as e:
                        logging.warning("Migration: feed_events.event_type ENUM: %s", e)

                    # Add old_title/new_title columns to feed_events
                    try:
                        cursor.execute("""
                            ALTER TABLE feed_events
                            ADD COLUMN old_title TEXT DEFAULT NULL,
                            ADD COLUMN new_title TEXT DEFAULT NULL
                        """)
                        conn.commit()
                        logging.info("Migration: added old_title/new_title to feed_events")
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: feed_events title columns: %s", e)

                    # Add extraction_errors column to scrape_log
                    try:
                        cursor.execute("""
                            ALTER TABLE scrape_log
                            ADD COLUMN extraction_errors INT DEFAULT 0 AFTER pubs_extracted
                        """)
                        conn.commit()
                        logging.info("Migration: added extraction_errors to scrape_log")
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: scrape_log.extraction_errors: %s", e)

                    # Add URL deactivation / failure-tracking columns to researcher_urls
                    _url_deactivation_columns = [
                        ("researcher_urls", "is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
                        ("researcher_urls", "consecutive_failures", "INT NOT NULL DEFAULT 0"),
                        ("researcher_urls", "deactivated_at", "DATETIME DEFAULT NULL"),
                        ("researcher_urls", "deactivation_reason", "VARCHAR(255) DEFAULT NULL"),
                    ]
                    for table, col, definition in _url_deactivation_columns:
                        try:
                            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                            conn.commit()
                        except Exception as e:
                            if getattr(e, 'errno', None) != 1060:
                                logging.warning("Migration warning for %s.%s: %s", table, col, e)

                    try:
                        cursor.execute("ALTER TABLE researcher_urls ADD INDEX idx_is_active (is_active)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration: researcher_urls.idx_is_active: %s", e)

                    # Capitalize first letter of lowercase paper titles
                    try:
                        _cap_sql = """
                            UPDATE {table}
                            SET title = CONCAT(UPPER(LEFT(title, 1)), SUBSTRING(title, 2))
                            WHERE title REGEXP '^[a-z]'
                        """
                        total = 0
                        for table in ('papers', 'paper_snapshots'):
                            cursor.execute(_cap_sql.format(table=table))
                            total += cursor.rowcount
                        conn.commit()
                        if total:
                            logging.info("Migration: capitalized %d lowercase paper titles", total)
                    except Exception as e:
                        logging.warning("Migration: capitalize titles: %s", e)

                    # Clean up status regression events and restore papers.status
                    # to highest-ranked status seen across snapshots
                    _S = ",".join(f"'{s}'" for s in STATUS_ORDER)
                    try:
                        cursor.execute(f"""
                            DELETE FROM feed_events
                            WHERE event_type = 'status_change'
                            AND FIELD(new_status, {_S}) < FIELD(old_status, {_S})
                            AND FIELD(new_status, {_S}) > 0
                            AND FIELD(old_status, {_S}) > 0
                        """)
                        deleted = cursor.rowcount
                        cursor.execute(f"""
                            UPDATE papers p
                            JOIN (
                                SELECT paper_id,
                                       ELT(MAX(FIELD(status, {_S})), {_S}) AS best_status
                                FROM paper_snapshots
                                WHERE status IS NOT NULL
                                GROUP BY paper_id
                            ) best ON best.paper_id = p.id
                            SET p.status = best.best_status
                            WHERE FIELD(p.status, {_S}) < FIELD(best.best_status, {_S})
                        """)
                        restored = cursor.rowcount
                        conn.commit()
                        if deleted or restored:
                            logging.info(
                                "Migration: cleaned %d status regression events, "
                                "restored %d paper statuses to highest rank",
                                deleted, restored,
                            )
                    except Exception as e:
                        logging.warning("Migration: status regression cleanup: %s", e)

                    # Remove forward-flapping duplicates: a status_change event
                    # is spurious unless it advances past the highest rank an
                    # earlier event already reached for the same paper.
                    # Not just one-time cleanup: created_at is backdated to the
                    # HTML fetch time, so this enforces monotone rank in *feed
                    # order* on every boot (emission order can differ when the
                    # extraction backlog reorders pages)
                    try:
                        cursor.execute(f"""
                            DELETE b FROM feed_events b
                            JOIN feed_events a
                              ON a.paper_id = b.paper_id
                             AND a.event_type = 'status_change'
                             AND (a.created_at < b.created_at
                                  OR (a.created_at = b.created_at AND a.id < b.id))
                             AND FIELD(a.new_status, {_S}) >= FIELD(b.new_status, {_S})
                             AND FIELD(b.new_status, {_S}) > 0
                            WHERE b.event_type = 'status_change'
                        """)
                        flap_deleted = cursor.rowcount
                        conn.commit()
                        if flap_deleted:
                            logging.info(
                                "Migration: removed %d non-advancing status_change events",
                                flap_deleted,
                            )
                    except Exception as e:
                        logging.warning("Migration: forward-flapping cleanup: %s", e)

                    # Add work_in_progress to papers.status ENUM
                    try:
                        cursor.execute("""
                            ALTER TABLE papers
                            MODIFY COLUMN status ENUM('published', 'accepted', 'revise_and_resubmit',
                                                       'reject_and_resubmit', 'working_paper', 'work_in_progress')
                            DEFAULT NULL
                        """)
                        conn.commit()
                        logging.info("Migration: added work_in_progress to papers.status ENUM")
                    except Exception as e:
                        logging.warning("Migration: papers.status ENUM: %s", e)

                    # Add work_in_progress to paper_snapshots.status ENUM
                    try:
                        cursor.execute("""
                            ALTER TABLE paper_snapshots
                            MODIFY COLUMN status ENUM('published', 'accepted', 'revise_and_resubmit',
                                                       'reject_and_resubmit', 'working_paper', 'work_in_progress')
                            DEFAULT NULL
                        """)
                        conn.commit()
                        logging.info("Migration: added work_in_progress to paper_snapshots.status ENUM")
                    except Exception as e:
                        logging.warning("Migration: paper_snapshots.status ENUM: %s", e)

                    # Add work_in_progress to feed_events.old_status / new_status ENUMs
                    try:
                        cursor.execute("""
                            ALTER TABLE feed_events
                            MODIFY COLUMN old_status ENUM('published','accepted','revise_and_resubmit',
                                                          'reject_and_resubmit','working_paper','work_in_progress')
                            DEFAULT NULL,
                            MODIFY COLUMN new_status ENUM('published','accepted','revise_and_resubmit',
                                                          'reject_and_resubmit','working_paper','work_in_progress')
                            DEFAULT NULL
                        """)
                        conn.commit()
                        logging.info("Migration: added work_in_progress to feed_events status ENUMs")
                    except Exception as e:
                        logging.warning("Migration: feed_events status ENUMs: %s", e)

                    # Backfill: flip working_paper → work_in_progress for papers with no links
                    try:
                        cursor.execute("""
                            UPDATE papers p
                            SET p.status = 'work_in_progress'
                            WHERE p.status = 'working_paper'
                              AND NOT EXISTS (
                                  SELECT 1 FROM paper_links pl WHERE pl.paper_id = p.id
                              )
                              AND (p.draft_url IS NULL OR p.draft_url_status != 'valid')
                        """)
                        backfilled = cursor.rowcount
                        conn.commit()
                        logging.info("Migration: backfilled %d papers to work_in_progress", backfilled)
                    except Exception as e:
                        logging.warning("Migration: work_in_progress backfill: %s", e)

                    # Update feed_events to reflect backfilled statuses
                    try:
                        cursor.execute("""
                            UPDATE feed_events fe
                            JOIN papers p ON p.id = fe.paper_id
                            SET fe.new_status = 'work_in_progress'
                            WHERE fe.event_type = 'new_paper'
                              AND fe.new_status = 'working_paper'
                              AND p.status = 'work_in_progress'
                        """)
                        events_updated = cursor.rowcount
                        conn.commit()
                        logging.info("Migration: updated %d feed_events to work_in_progress", events_updated)
                    except Exception as e:
                        logging.warning("Migration: feed_events backfill: %s", e)

                    # Add has_top5_pub denormalized flag to researchers
                    try:
                        cursor.execute("""
                            ALTER TABLE researchers
                            ADD COLUMN has_top5_pub BOOLEAN NOT NULL DEFAULT FALSE
                        """)
                        conn.commit()
                        logging.info("Migration: added researchers.has_top5_pub column")
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: researchers.has_top5_pub: %s", e)

                    try:
                        cursor.execute("ALTER TABLE researchers ADD INDEX idx_has_top5_pub (has_top5_pub)")
                        conn.commit()
                    except Exception as e:
                        if getattr(e, 'errno', None) != 1061:
                            logging.warning("Migration: researchers.idx_has_top5_pub: %s", e)

                    # Backfill has_top5_pub from existing data
                    try:
                        from backend.database.search_helpers import top5_venue_clause
                        venue_clause, params = top5_venue_clause("p.venue")
                        cursor.execute(
                            f"""UPDATE researchers r
                                JOIN (
                                    SELECT DISTINCT a.researcher_id
                                    FROM authorship a
                                    JOIN papers p ON p.id = a.publication_id
                                    WHERE {venue_clause}
                                ) t5 ON t5.researcher_id = r.id
                                SET r.has_top5_pub = TRUE
                                WHERE r.has_top5_pub = FALSE""",
                            params,
                        )
                        backfilled = cursor.rowcount
                        conn.commit()
                        if backfilled:
                            logging.info("Migration: backfilled has_top5_pub for %d researchers", backfilled)
                    except Exception as e:
                        logging.warning("Migration: has_top5_pub backfill: %s", e)

                finally:
                    cursor.execute("SELECT RELEASE_LOCK('econ_migrations')")
                    cursor.fetchone()
            else:
                logging.info("Skipping migrations — another pod holds the lock")

    logging.info("All tables created successfully")
    seed_research_fields()
    seed_jel_codes()


def seed_research_fields() -> None:
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
        execute_query(
            "INSERT IGNORE INTO research_fields (name, slug) VALUES (%s, %s)",
            (name, slug),
        )


def seed_jel_codes() -> None:
    """Insert the standard AEA JEL classification top-level codes."""
    codes = [
        ("A", "General Economics and Teaching"),
        ("B", "History of Economic Thought, Methodology, and Heterodox Approaches"),
        ("C", "Mathematical and Quantitative Methods"),
        ("D", "Microeconomics"),
        ("E", "Macroeconomics and Monetary Economics"),
        ("F", "International Economics"),
        ("G", "Financial Economics"),
        ("H", "Public Economics"),
        ("I", "Health, Education, and Welfare"),
        ("J", "Labor and Demographic Economics"),
        ("K", "Law and Economics"),
        ("L", "Industrial Organization"),
        ("M", "Business Administration and Business Economics; Marketing; Accounting; Personnel Economics"),
        ("N", "Economic History"),
        ("O", "Economic Development, Innovation, Technological Change, and Growth"),
        ("P", "Economic Systems"),
        ("Q", "Agricultural and Natural Resource Economics; Environmental and Ecological Economics"),
        ("R", "Urban, Rural, Regional, Real Estate, and Transportation Economics"),
        ("Y", "Miscellaneous Categories"),
        ("Z", "Other Special Topics"),
    ]
    for code, name in codes:
        execute_query(
            "INSERT IGNORE INTO jel_codes (code, name) VALUES (%s, %s)",
            (code, name),
        )


def backfill_seed_publications() -> int:
    """Mark all existing publications as seed."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE papers SET is_seed = TRUE WHERE is_seed = FALSE")
            conn.commit()
            return cursor.rowcount
