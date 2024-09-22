import requests
import mysql.connector
from datetime import datetime
from db_config import db_config

def fetch_html(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"Error fetching HTML: {e}")
        return None

def save_to_database(url, html_content):
    try:
        with mysql.connector.connect(**db_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS html_content (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        url VARCHAR(255),
                        content LONGTEXT,
                        timestamp DATETIME
                    )
                """)

                insert_query = """
                    INSERT INTO html_content (url, content, timestamp)
                    VALUES (%s, %s, %s)
                """
                cursor.execute(insert_query, (url, html_content, datetime.now()))
                conn.commit()
                print(f"HTML content saved successfully for {url}")

    except mysql.connector.Error as e:
        print(f"Error saving to database: {e}")

def get_urls_from_database():
    try:
        with mysql.connector.connect(**db_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT DISTINCT url FROM researcher_urls")
                return [url[0] for url in cursor.fetchall()]
    except mysql.connector.Error as e:
        print(f"Error fetching URLs from database: {e}")
        return []

def fetch_and_save_urls():
    urls = get_urls_from_database()
    for url in urls:
        print(f"Fetching HTML for: {url}")
        html_content = fetch_html(url)
        if html_content:
            save_to_database(url, html_content)
        print("---")

def read_urls_from_file(filename):
    with open(filename, 'r') as file:
        return [line.strip() for line in file if line.strip()]

def create_researchers_table():
    try:
        with mysql.connector.connect(**db_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS researchers (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        last_name VARCHAR(255) NOT NULL,
                        first_name VARCHAR(255) NOT NULL,
                        position VARCHAR(255),
                        affiliation VARCHAR(255)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS researcher_urls (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        researcher_id INT,
                        url VARCHAR(255) NOT NULL,
                        FOREIGN KEY (researcher_id) REFERENCES researchers(id)
                    )
                """)
                print("Researchers and researcher_urls tables created successfully")
    except mysql.connector.Error as e:
        print(f"Error creating researchers tables: {e}")

def add_researcher(last_name, first_name, position, affiliation):
    try:
        with mysql.connector.connect(**db_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO researchers (last_name, first_name, position, affiliation)
                    VALUES (%s, %s, %s, %s)
                """, (last_name, first_name, position, affiliation))
                conn.commit()
                return cursor.lastrowid
    except mysql.connector.Error as e:
        print(f"Error adding researcher: {e}")
        return None

def add_url_to_researcher(researcher_id, url):
    try:
        with mysql.connector.connect(**db_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO researcher_urls (researcher_id, url) VALUES (%s, %s)", (researcher_id, url))
                conn.commit()
    except mysql.connector.Error as e:
        print(f"Error adding URL to researcher: {e}")

def add_urls_for_researcher():
    create_researchers_table()
    
    last_name = input("Enter researcher's last name: ")
    first_name = input("Enter researcher's first name: ")
    position = input("Enter researcher's position: ")
    affiliation = input("Enter researcher's affiliation: ")
    
    researcher_id = add_researcher(last_name, first_name, position, affiliation)
    
    if researcher_id is not None:
        while True:
            url = input("Enter a URL (or press Enter to finish): ")
            if not url:
                break
            add_url_to_researcher(researcher_id, url)
    else:
        print("Failed to add researcher. Aborting URL addition.")

if __name__ == "__main__":
    add_urls_for_researcher()