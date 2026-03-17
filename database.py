import mysql.connector
from mysql.connector import Error
from mysql.connector.pooling import MySQLConnectionPool
from db_config import db_config
import csv
import json
import logging
import os
import re
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(message)s')

_pool: "MySQLConnectionPool | None" = None


def _get_pool() -> "MySQLConnectionPool":
    global _pool
    if _pool is None:
        _pool = MySQLConnectionPool(pool_size=10, pool_name="econ_pool", **db_config)
    return _pool


class Database:
    @staticmethod
    def create_database():
        """
        Create the database if it doesn't exist.
        """
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
            if conn.is_connected():
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
        Returns the last inserted row ID or None if there's an error.
        """
        try:
            with Database.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    conn.commit()
                    return cursor.lastrowid
        except Error as e:
            logging.error("Database error in execute_query: %s", type(e).__name__)
            return None

    @staticmethod
    def fetch_all(query, params=None):
        """
        Execute a query with optional parameters and fetch all results.
        Returns a list of tuples containing the results or an empty list if there's an error.
        """
        try:
            with Database.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
        except Error as e:
            logging.error("Database error in fetch_all: %s", type(e).__name__)
            return []

    @staticmethod
    def fetch_one(query, params=None):
        """
        Execute a SELECT query and fetch one result.
        """
        try:
            with Database.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchone()
        except Error as e:
            logging.error("Database error in fetch_one: %s", type(e).__name__)
            return None

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
                    bio TEXT,
                    INDEX idx_name (last_name, first_name)
                )
            """,
            "researcher_urls": """
                CREATE TABLE IF NOT EXISTS researcher_urls (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    researcher_id INT NOT NULL,
                    page_type VARCHAR(255) NOT NULL,
                    url VARCHAR(2048) NOT NULL,
                    UNIQUE KEY uq_researcher_url (researcher_id, url(500)),
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id)
                )
            """,
            "papers": """
                CREATE TABLE IF NOT EXISTS papers (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    url VARCHAR(2048),
                    title TEXT,
                    year VARCHAR(4),
                    venue TEXT,
                    timestamp DATETIME,
                    status ENUM('published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit') DEFAULT NULL,
                    draft_url VARCHAR(2048) DEFAULT NULL,
                    UNIQUE KEY uq_title_url (title(200), url(200)),
                    INDEX idx_timestamp (timestamp),
                    INDEX idx_status (status)
                )
            """,
            "html_content": """
                CREATE TABLE IF NOT EXISTS html_content (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    url_id INT NOT NULL,
                    content LONGTEXT,
                    content_hash VARCHAR(64),
                    timestamp DATETIME,
                    researcher_id INT,
                    extracted_at DATETIME,
                    extracted_hash VARCHAR(64),
                    UNIQUE KEY uq_url_id (url_id),
                    INDEX idx_url_id_ts (url_id, timestamp),
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id),
                    FOREIGN KEY (url_id) REFERENCES researcher_urls(id)
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
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id),
                    FOREIGN KEY (publication_id) REFERENCES papers(id)
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
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id),
                    FOREIGN KEY (field_id) REFERENCES research_fields(id)
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
                    error_message TEXT
                )
            """
        }

        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                for table_query in table_definitions.values():
                    cursor.execute(table_query)

        # Migration: add bio column to existing researchers tables
        Database.execute_query(
            "ALTER TABLE researchers ADD COLUMN IF NOT EXISTS bio TEXT"
        )
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
    def apply_schema_migrations():
        """
        Apply schema migrations to existing tables.
        Safe to run on every startup — each step is idempotent.
        """
        # 1. Remove duplicate researcher_urls rows, keeping the lowest ID
        Database.execute_query("""
            DELETE r1 FROM researcher_urls r1
            JOIN researcher_urls r2
              ON r1.researcher_id = r2.researcher_id
              AND r1.url = r2.url
              AND r1.id > r2.id
        """)

        # 2. Add UNIQUE KEY to researcher_urls if not present
        result = Database.fetch_one("""
            SELECT COUNT(*)
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'researcher_urls'
              AND CONSTRAINT_NAME = 'uq_researcher_url'
        """)
        if result and result[0] == 0:
            Database.execute_query("""
                ALTER TABLE researcher_urls
                  ADD UNIQUE KEY uq_researcher_url (researcher_id, url(500))
            """)
            logging.info("Migration: added UNIQUE KEY uq_researcher_url to researcher_urls")

        # 3. Add extracted_at column to html_content if not present
        result = Database.fetch_one("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'html_content'
              AND COLUMN_NAME = 'extracted_at'
        """)
        if result and result[0] == 0:
            Database.execute_query("""
                ALTER TABLE html_content ADD COLUMN extracted_at DATETIME
            """)
            logging.info("Migration: added extracted_at column to html_content")

        # 4. Add extracted_hash column to html_content if not present
        result = Database.fetch_one("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'html_content'
              AND COLUMN_NAME = 'extracted_hash'
        """)
        if result and result[0] == 0:
            Database.execute_query("""
                ALTER TABLE html_content ADD COLUMN extracted_hash VARCHAR(64)
            """)
            logging.info("Migration: added extracted_hash column to html_content")

        logging.info("Schema migrations applied successfully")

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
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=os.environ.get('OPENAI_MODEL', 'gpt-4o-mini'),
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
        """Update researcher bio only if the current bio is NULL."""
        Database.execute_query(
            "UPDATE researchers SET bio = %s WHERE id = %s AND bio IS NULL",
            (bio, researcher_id),
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
    Database.create_database()  # Create the database before creating tables
    Database.create_tables()
    Database.apply_schema_migrations()
    Database.import_data_from_file('urls.csv')
