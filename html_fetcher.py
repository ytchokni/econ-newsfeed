import requests
import hashlib
import logging
from datetime import datetime
from database import Database
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class HTMLFetcher:
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (compatible; HTMLFetcher/1.0)'
    })

    @staticmethod
    def fetch_html(url, timeout=10, max_retries=3):
        """
        Fetch HTML content from a given URL.
        Returns the HTML content as a string.
        """
        for attempt in range(max_retries):
            try:
                response = HTMLFetcher.session.get(url, timeout=timeout)
                response.raise_for_status()
                logging.info(f"Successfully fetched HTML content from {url}")
                return response.text
            except requests.exceptions.Timeout:
                logging.warning(f"Timeout occurred while fetching {url}. Attempt {attempt + 1} of {max_retries}")
            except requests.exceptions.RequestException as e:
                logging.error(f"Request exception for {url}: {e}")
                break  # Non-retryable error
        logging.error(f"Failed to fetch HTML content from {url} after {max_retries} attempts")
        return None

    @staticmethod
    def extract_text_content(html_content):
        """
        Extract only the text content from HTML, ignoring scripts, styles, and HTML tags.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        for script in soup(["script", "style"]):
            script.decompose()
        return soup.get_text()

    @staticmethod
    def hash_text_content(text_content):
        """
        Hash the text content using SHA-256.
        """
        return hashlib.sha256(text_content.encode('utf-8')).hexdigest()

    @staticmethod
    def save_text(url_id, text_content, text_hash, researcher_id):
        """
        Save pre-extracted text content and hash to the database using upsert.
        """
        query = """
            INSERT INTO html_content (url_id, content, content_hash, timestamp, researcher_id)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                content = VALUES(content),
                content_hash = VALUES(content_hash),
                timestamp = VALUES(timestamp)
        """
        try:
            Database.execute_query(query, (url_id, text_content, text_hash, datetime.utcnow(), researcher_id))
            logging.info(f"Text content saved for URL ID: {url_id} (Researcher ID: {researcher_id})")
        except Exception as e:
            logging.error(f"Error saving text content for URL ID: {url_id}: {e}")

    @staticmethod
    def has_text_changed(url_id, new_text_hash):
        """
        Compare the hash of the new text content to the stored hash to check for changes.
        """
        query = """
            SELECT content_hash
            FROM html_content
            WHERE url_id = %s
        """
        result = Database.fetch_one(query, (url_id,))

        if result:
            return result[0] != new_text_hash
        return True  # No previous record — treat as changed

    @staticmethod
    def fetch_and_save_if_changed(url_id, url, researcher_id):
        """
        Fetch HTML content from the given URL and save its text content if it has changed.
        Returns True if content changed, False otherwise.
        """
        html_content = HTMLFetcher.fetch_html(url)
        if not html_content:
            logging.warning(f"Failed to fetch HTML content for URL ID: {url_id}, URL: {url}")
            return False

        # Parse HTML once, reuse for both comparison and storage
        text_content = HTMLFetcher.extract_text_content(html_content)
        text_hash = HTMLFetcher.hash_text_content(text_content)

        if HTMLFetcher.has_text_changed(url_id, text_hash):
            HTMLFetcher.save_text(url_id, text_content, text_hash, researcher_id)
            logging.info(f"New version of text content saved for URL ID: {url_id}, URL: {url}")
            return True

        logging.info(f"No text changes detected for URL ID: {url_id}, URL: {url}")
        return False

    @staticmethod
    def get_latest_text(url_id):
        """Retrieve the latest text content for a given URL ID."""
        query = """
            SELECT content
            FROM html_content
            WHERE url_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """
        result = Database.fetch_one(query, (url_id,))
        return result[0] if result else None

