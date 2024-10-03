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
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_config['database']}")
            logging.info(f"Database '{db_config['database']}' created or already exists.")
        except Error as e:
            logging.error(f"Error creating database: {e}")
        finally:
            if conn.is_connected():
                cursor.close()
                conn.close()

    @staticmethod
    def execute_query(query, params=None):
        """
        Execute a query with optional parameters and commit the changes.
        Returns the last inserted row ID.
        """
        try:
            
            with mysql.connector.connect(**db_config) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    conn.commit()
                    return cursor.lastrowid
        except Error as e:
            logging.error(f"Database error: {e}")
            return None

    @staticmethod
    def fetch_all(query, params=None):
        """
        Execute a query with optional parameters and fetch all results.
        Returns a list of tuples containing the results.
        """
        try:
            with mysql.connector.connect(**db_config) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
        except Error as e:
            logging.error(f"Database error: {e}")
            return []

    @staticmethod
    def fetch_one(query, params=None):
        """
        Execute a query with optional parameters and fetch one result.
        Returns a tuple containing the result.
        """
        try:
            with mysql.connector.connect(**db_config) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchone()
        except Error as e:
            logging.error(f"Database error: {e}")
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
                    affiliation VARCHAR(255)
                )
            """,
            "researcher_urls": """
                CREATE TABLE IF NOT EXISTS researcher_urls (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    researcher_id INT,
                    page_type VARCHAR(255) NOT NULL,
                    url VARCHAR(255) NOT NULL,
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id)
                )
            """,
            "publications": """
                CREATE TABLE IF NOT EXISTS publications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    url VARCHAR(255),
                    title TEXT,
                    authors TEXT,
                    year VARCHAR(4),
                    venue TEXT,
                    timestamp DATETIME
                )
            """,
            "html_content": """
                CREATE TABLE IF NOT EXISTS html_content (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    url_id INT,
                    url VARCHAR(255),
                    content LONGTEXT,
                    content_hash VARCHAR(64),
                    timestamp DATETIME,
                    researcher_id INT,
                    FOREIGN KEY (researcher_id) REFERENCES researchers(id),
                    FOREIGN KEY (url_id) REFERENCES researcher_urls(id)
                )
            """
        }

        for table_name, table_query in table_definitions.items():
            Database.execute_query(table_query)
        logging.info("All tables created successfully")

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
        result = Database.fetch_all(query, params)

        if result:
            return result[0][0]
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
            INSERT INTO researcher_urls (researcher_id, page_type, url)
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
