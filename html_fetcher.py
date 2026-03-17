import difflib
import ipaddress
import os
import time
import socket
import requests
import hashlib
import logging
import urllib3
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from database import Database
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

RATE_LIMIT_SECONDS = float(os.environ.get('SCRAPE_RATE_LIMIT_SECONDS', '2'))
RATE_LIMIT_FAST_SECONDS = float(os.environ.get('SCRAPE_RATE_LIMIT_FAST_SECONDS', '0.5'))
CONTENT_MAX_CHARS = int(os.environ.get('CONTENT_MAX_CHARS', '4000'))
CONTENT_MAX_BYTES = 1_000_000  # 1 MB response size limit
SCRAPER_USER_AGENT = os.environ.get(
    'SCRAPER_USER_AGENT', 'Mozilla/5.0 (compatible; HTMLFetcher/1.0)'
)


# ---------------------------------------------------------------------------
# DNS-pinning transport adapter (SSRF DNS rebinding prevention)
# ---------------------------------------------------------------------------

class _PinnedPoolManager(PoolManager):
    """Pool manager that connects to a pre-resolved IP, preventing DNS rebinding.

    For HTTPS, the original hostname is preserved for SNI and certificate
    verification so TLS still works correctly.
    """

    def __init__(self, hostname: str, resolved_ip: str, **kwargs):
        super().__init__(**kwargs)
        self._hostname = hostname
        self._resolved_ip = resolved_ip

    def connection_from_host(self, host, port=None, scheme='http', pool_kwargs=None):
        if host == self._hostname:
            pool_kwargs = (pool_kwargs or {}).copy()
            if scheme == 'https':
                # Keep original hostname for SNI handshake and cert verification
                pool_kwargs.setdefault('assert_hostname', self._hostname)
                pool_kwargs.setdefault('server_hostname', self._hostname)
            return super().connection_from_host(
                self._resolved_ip, port, scheme, pool_kwargs
            )
        return super().connection_from_host(host, port, scheme, pool_kwargs)


class _PinnedIPAdapter(HTTPAdapter):
    """Transport adapter that pins TCP connections to a pre-resolved IP.

    Prevents DNS rebinding SSRF attacks: the TCP connection always goes to the
    IP address validated at request time rather than performing a second DNS
    lookup that an attacker could control via low-TTL records.
    """

    def __init__(self, hostname: str, resolved_ip: str, **kwargs):
        self._hostname = hostname
        self._resolved_ip = resolved_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **connection_pool_kw):
        self.poolmanager = _PinnedPoolManager(
            hostname=self._hostname,
            resolved_ip=self._resolved_ip,
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **connection_pool_kw,
        )

# Large CDN/hosting platforms that can handle higher request rates
FAST_DOMAINS = {
    'sites.google.com',
    'github.io',
    'github.com',
    'scholar.google.com',
    'academia.edu',
    'researchgate.net',
    'ssrn.com',
}


class HTMLFetcher:
    session = requests.Session()
    session.headers.update({
        'User-Agent': SCRAPER_USER_AGENT
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
        Rejects non-HTTP(S) schemes, private/reserved IPs, and cloud metadata endpoints.

        Returns (True, resolved_ip) if safe, (False, None) otherwise.
        The resolved_ip is returned so callers can pin the TCP connection to that
        exact IP, preventing a second DNS resolution (DNS rebinding attack).
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False, None

        if parsed.scheme not in ('http', 'https'):
            logging.warning(f"Rejected URL with non-HTTP(S) scheme: {url}")
            return False, None

        hostname = parsed.hostname
        if not hostname:
            return False, None

        # Reject cloud metadata endpoints by hostname
        if hostname in ('169.254.169.254', 'metadata.google.internal'):
            logging.warning(f"Rejected metadata endpoint URL: {url}")
            return False, None

        # Resolve hostname and check for private/reserved IPs
        try:
            resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(resolved_ip)
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                logging.warning(
                    "Rejected URL resolving to private/reserved IP: %s -> [redacted]", url
                )
                return False, None
        except (socket.gaierror, ValueError):
            logging.warning(f"Could not resolve hostname for URL: {url}")
            return False, None

        return True, resolved_ip

    @staticmethod
    def _rate_limit(url):
        """Enforce per-domain rate limiting."""
        domain = urlparse(url).hostname
        limit = RATE_LIMIT_FAST_SECONDS if domain in FAST_DOMAINS else RATE_LIMIT_SECONDS
        if domain in HTMLFetcher._domain_last_request:
            elapsed = time.time() - HTMLFetcher._domain_last_request[domain]
            if elapsed < limit:
                wait = limit - elapsed
                logging.info(f"Rate limiting: waiting {wait:.1f}s for {domain}")
                time.sleep(wait)
        HTMLFetcher._domain_last_request[domain] = time.time()

    @staticmethod
    def fetch_html(url, timeout=10, max_retries=3, resolved_ip=None):
        """
        Fetch HTML content from a given URL with exponential backoff.
        Retries on timeouts and 5xx server errors.

        If resolved_ip is provided, the TCP connection is pinned to that IP to
        prevent DNS rebinding (the IP must already be validated by validate_url).

        Returns the HTML content as a string, or None on failure.
        """
        HTMLFetcher._rate_limit(url)

        if resolved_ip is not None:
            # Use a per-request session with a pinned-IP adapter to prevent
            # a second DNS resolution from returning a different (private) IP.
            hostname = urlparse(url).hostname
            session = requests.Session()
            session.headers.update(HTMLFetcher.session.headers)
            adapter = _PinnedIPAdapter(hostname=hostname, resolved_ip=resolved_ip)
            session.mount('https://', adapter)
            session.mount('http://', adapter)
        else:
            session = HTMLFetcher.session

        for attempt in range(max_retries):
            try:
                response = session.get(url, timeout=timeout)
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
                response.encoding = response.apparent_encoding
                return response.text
            except requests.exceptions.Timeout:
                backoff = 2 ** attempt
                logging.warning(f"Timeout for {url}. Retrying in {backoff}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                logging.error("Request exception for %s: %s", url, type(e).__name__)
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
            Database.execute_query(query, (url_id, text_content, text_hash, datetime.now(timezone.utc), researcher_id))
            logging.info(f"Text content saved for URL ID: {url_id} (Researcher ID: {researcher_id})")
        except Exception as e:
            logging.error("Error saving text content for URL ID %s: %s", url_id, type(e).__name__)

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
        valid, resolved_ip = HTMLFetcher.validate_url(url)
        if not valid:
            logging.warning(f"URL failed SSRF validation, skipping: {url}")
            return False

        if not HTMLFetcher.is_allowed_by_robots(url):
            return False

        # Pass the pre-resolved IP to avoid a second DNS lookup (DNS rebinding prevention)
        html_content = HTMLFetcher.fetch_html(url, resolved_ip=resolved_ip)
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
        """
        result = Database.fetch_one(query, (url_id,))
        return result[0] if result else None

    @staticmethod
    def get_previous_text(url_id):
        """Retrieve the previous text content for a given URL ID (before current upsert).
        With upsert, there's only one row per url_id, so this returns the current stored
        content (which represents the *previous* version before fetch_and_save_if_changed
        overwrites it).
        """
        return HTMLFetcher.get_latest_text(url_id)

    @staticmethod
    def compute_diff(old_text, new_text):
        """Compute a unified diff between old and new text content.
        Returns only added/changed lines to reduce LLM token usage.
        """
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, n=1)
        # Extract only added lines (starting with '+' but not '+++')
        added_lines = [line[1:] for line in diff if line.startswith('+') and not line.startswith('+++')]
        return ''.join(added_lines) if added_lines else new_text
