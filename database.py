import mysql.connector
from mysql.connector import Error
from db_config import db_config
import csv
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(message)s')

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
            logging.error(f"Error creating database: {e}")
        finally:
            if conn.is_connected():
                cursor.close()
                conn.close()

    @staticmethod
    def get_connection():
        """
        Create and return a new database connection.
        """
        try:
            conn = mysql.connector.connect(**db_config)
            return conn
        except Error as e:
            logging.error(f"Error connecting to the database: {e}")
            return None

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
            logging.error(f"Database error in execute_query: {e}")
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
            logging.error(f"Database error in fetch_all: {e}")
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
            logging.error(f"Database error in fetch_one: {e}")
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

        for table_name, table_query in table_definitions.items():
            Database.execute_query(table_query)
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
    def get_researcher_id(first_name, last_name, position=None, affiliation=None):
        """
        Get the researcher ID based on the first name and last name.
        If the researcher does not exist, insert a new researcher and return the new ID.
        """
        query = """
            SELECT id FROM researchers
            WHERE first_name = %s AND last_name = %s
        """
        params = (first_name, last_name)
        result = Database.fetch_one(query, params)

        if result:
            return result[0]
        else:
            insert_query = """
                INSERT INTO researchers (first_name, last_name, position, affiliation)
                VALUES (%s, %s, %s, %s)
            """
            insert_params = (first_name, last_name, position, affiliation)
            new_id = Database.execute_query(insert_query, insert_params)
            return new_id

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
            logging.error(f"Error importing data from file: {e}")

# Example usage:
if __name__ == "__main__":
    Database.create_database()  # Create the database before creating tables
    Database.create_tables()
    Database.import_data_from_file('urls.csv')
