import requests
import hashlib
import logging
from datetime import datetime
from database import Database

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
    def hash_html_content(html_content):
        """
        Hash the HTML content using SHA-256.
        """
        return hashlib.sha256(html_content.encode('utf-8')).hexdigest()

    @staticmethod
    def save_html(url, html_content):
        """
        Save HTML content and its hash to the database.
        """
        html_hash = HTMLFetcher.hash_html_content(html_content)
        query = """
            INSERT INTO html_content (url, content, content_hash, timestamp)
            VALUES (%s, %s, %s, %s)
        """
        try:
            Database.execute_query(query, (url, html_content, html_hash, datetime.utcnow()))
            logging.info(f"HTML content and hash saved successfully for {url}")
        except Exception as e:
            logging.error(f"Error saving HTML content to database for {url}: {e}")

    @staticmethod
    def has_html_changed(url, new_html_content):
        """
        Compare the hash of the new HTML content to the stored hash to check for changes.
        """
        new_html_hash = HTMLFetcher.hash_html_content(new_html_content)
        
        query = "SELECT content_hash FROM html_content WHERE url = %s ORDER BY timestamp DESC LIMIT 1"
        result = Database.fetch_one(query, (url,))
        
        if result:
            old_html_hash = result[0]
            return old_html_hash != new_html_hash  # Returns True if the content has changed
        return True  # If no previous record exists, assume the content has changed

    @staticmethod
    def fetch_and_save_if_changed(url):
        """
        Fetch HTML content from the given URL and save it if it has changed.
        """
        html_content = HTMLFetcher.fetch_html(url)
        if html_content:
            if HTMLFetcher.has_html_changed(url, html_content):
                HTMLFetcher.save_html(url, html_content)
                logging.info(f"New version of HTML saved for {url}")
            else:
                logging.info(f"No changes detected for {url}")
        else:
            logging.warning(f"Failed to fetch HTML content for {url}")
