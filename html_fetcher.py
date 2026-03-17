import ipaddress
import os
import time
import requests
import hashlib
import logging
from datetime import datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from database import Database
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

RATE_LIMIT_SECONDS = float(os.environ.get('SCRAPE_RATE_LIMIT_SECONDS', '2'))
CONTENT_MAX_CHARS = int(os.environ.get('CONTENT_MAX_CHARS', '4000'))
CONTENT_MAX_BYTES = 1_000_000  # 1 MB response size limit


class HTMLFetcher:
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (compatible; HTMLFetcher/1.0)'
    })

    # Per-domain rate limiting: domain -> last request timestamp
    _domain_last_request = {}

    @staticmethod
    def is_allowed_by_robots(url):
        """Check if the URL is allowed by the site's robots.txt."""
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            allowed = rp.can_fetch('HTMLFetcher/1.0', url)
            if not allowed:
                logging.info(f"URL disallowed by robots.txt: {url}")
            return allowed
        except Exception:
            # If robots.txt can't be fetched, allow the request
            return True

    @staticmethod
    def validate_url(url):
        """
        Validate a URL for SSRF protection.
        Rejects non-HTTP(S) schemes, private/reserved IPs, and AWS metadata endpoints.
        Returns True if safe, False otherwise.
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        if parsed.scheme not in ('http', 'https'):
            logging.warning(f"Rejected URL with non-HTTP(S) scheme: {url}")
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Reject AWS metadata endpoints
        if hostname in ('169.254.169.254', 'metadata.google.internal'):
            logging.warning(f"Rejected metadata endpoint URL: {url}")
            return False

        # Resolve hostname and check for private/reserved IPs
        import socket
        try:
            resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(resolved_ip)
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                logging.warning(f"Rejected URL resolving to private/reserved IP: {url} -> {resolved_ip}")
                return False
        except (socket.gaierror, ValueError):
            logging.warning(f"Could not resolve hostname for URL: {url}")
            return False

        return True

    @staticmethod
    def _rate_limit(url):
        """Enforce per-domain rate limiting."""
        domain = urlparse(url).hostname
        if domain in HTMLFetcher._domain_last_request:
            elapsed = time.time() - HTMLFetcher._domain_last_request[domain]
            if elapsed < RATE_LIMIT_SECONDS:
                wait = RATE_LIMIT_SECONDS - elapsed
                logging.info(f"Rate limiting: waiting {wait:.1f}s for {domain}")
                time.sleep(wait)
        HTMLFetcher._domain_last_request[domain] = time.time()

    @staticmethod
    def fetch_html(url, timeout=10, max_retries=3):
        """
        Fetch HTML content from a given URL with exponential backoff.
        Retries on timeouts and 5xx server errors.
        Returns the HTML content as a string, or None on failure.
        """
        HTMLFetcher._rate_limit(url)

        for attempt in range(max_retries):
            try:
                response = HTMLFetcher.session.get(url, timeout=timeout)
                if response.status_code >= 500:
                    backoff = 2 ** attempt  # 1s, 2s, 4s
                    logging.warning(f"Server error {response.status_code} for {url}. Retrying in {backoff}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(backoff)
                    continue
                response.raise_for_status()
                if len(response.content) > CONTENT_MAX_BYTES:
                    logging.warning(f"Response too large ({len(response.content)} bytes) for {url}, rejecting")
                    return None
                logging.info(f"Successfully fetched HTML content from {url}")
                return response.text
            except requests.exceptions.Timeout:
                backoff = 2 ** attempt
                logging.warning(f"Timeout for {url}. Retrying in {backoff}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(backoff)
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
        if not HTMLFetcher.validate_url(url):
            logging.warning(f"URL failed SSRF validation, skipping: {url}")
            return False

        if not HTMLFetcher.is_allowed_by_robots(url):
            return False

        html_content = HTMLFetcher.fetch_html(url)
        if not html_content:
            logging.warning(f"Failed to fetch HTML content for URL ID: {url_id}, URL: {url}")
            return False

        # Parse HTML once, reuse for both comparison and storage
        text_content = HTMLFetcher.extract_text_content(html_content)
        if len(text_content) > CONTENT_MAX_CHARS:
            dropped = len(text_content) - CONTENT_MAX_CHARS
            logging.info(f"Truncating content for URL ID {url_id}: {len(text_content)} -> {CONTENT_MAX_CHARS} chars ({dropped} dropped)")
            text_content = text_content[:CONTENT_MAX_CHARS]
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

