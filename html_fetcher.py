import difflib
import ipaddress
import os
import time
import socket
import requests
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from database import Database
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

RATE_LIMIT_SECONDS = float(os.environ.get('SCRAPE_RATE_LIMIT_SECONDS', '2'))
RATE_LIMIT_FAST_SECONDS = float(os.environ.get('SCRAPE_RATE_LIMIT_FAST_SECONDS', '0.5'))
CONTENT_MAX_CHARS = int(os.environ.get('CONTENT_MAX_CHARS', '4000'))
CONTENT_MAX_BYTES = 1_000_000  # 1 MB response size limit
SCRAPER_USER_AGENT = os.environ.get(
    'SCRAPER_USER_AGENT',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
)


# Large CDN/hosting platforms that can handle higher request rates.
# Matched by suffix so subdomains work (e.g. user.github.io matches github.io).
FAST_DOMAINS = (
    'sites.google.com',
    'github.io',
    'github.com',
    'scholar.google.com',
    'academia.edu',
    'researchgate.net',
    'ssrn.com',
)


def _is_fast_domain(hostname: str) -> bool:
    """Check if hostname matches a FAST_DOMAINS entry (exact or subdomain)."""
    for domain in FAST_DOMAINS:
        if hostname == domain or hostname.endswith('.' + domain):
            return True
    return False


class HTMLFetcher:
    session = requests.Session()
    session.headers.update({
        'User-Agent': SCRAPER_USER_AGENT
    })

    # Per-domain rate limiting: domain -> last request timestamp
    _domain_last_request = {}

    @staticmethod
    def is_allowed_by_robots(url):
        """Check if the URL is allowed by the site's robots.txt.
        If robots.txt can't be fetched or returns non-200, allow the request."""
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            resp = requests.get(robots_url, timeout=10, headers={
                'User-Agent': SCRAPER_USER_AGENT,
            })
            if resp.status_code != 200:
                return True
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            allowed = rp.can_fetch('HTMLFetcher/1.0', url)
            if not allowed:
                logging.info(f"URL disallowed by robots.txt: {url}")
            return allowed
        except Exception:
            return True

    @staticmethod
    def validate_url(url):
        """
        Validate a URL for SSRF protection.
        Rejects non-HTTP(S) schemes, private/reserved IPs, and cloud metadata endpoints.

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

        # Reject cloud metadata endpoints by hostname
        if hostname in ('169.254.169.254', 'metadata.google.internal'):
            logging.warning(f"Rejected metadata endpoint URL: {url}")
            return False

        # Resolve hostname and check for private/reserved IPs
        try:
            resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(resolved_ip)
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                logging.warning(
                    "Rejected URL resolving to private/reserved IP: %s -> [redacted]", url
                )
                return False
        except (socket.gaierror, ValueError):
            logging.warning(f"Could not resolve hostname for URL: {url}")
            return False

        return True

    @staticmethod
    def _rate_limit(url):
        """Enforce per-domain rate limiting."""
        domain = urlparse(url).hostname
        limit = RATE_LIMIT_FAST_SECONDS if _is_fast_domain(domain) else RATE_LIMIT_SECONDS
        if domain in HTMLFetcher._domain_last_request:
            elapsed = time.time() - HTMLFetcher._domain_last_request[domain]
            if elapsed < limit:
                wait = limit - elapsed
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
    def _was_fetched_recently(url_id, hours=24):
        """Return True if this URL was successfully fetched within the last N hours."""
        result = Database.fetch_one(
            "SELECT timestamp FROM html_content WHERE url_id = %s", (url_id,)
        )
        if not result or not result[0]:
            return False
        age = datetime.now(timezone.utc) - result[0].replace(tzinfo=timezone.utc)
        return age.total_seconds() < hours * 3600

    @staticmethod
    def fetch_and_save_if_changed(url_id, url, researcher_id):
        """
        Fetch HTML content from the given URL and save its text content if it has changed.
        Skips fetching if the URL was successfully fetched within the last 24 hours.
        Returns True if content changed, False otherwise.
        """
        if HTMLFetcher._was_fetched_recently(url_id):
            logging.info(f"Skipping URL ID {url_id} (fetched <24h ago): {url}")
            return False

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
    def extract_bio(text_content: str, url: str, scrape_log_id=None) -> str | None:
        """Legacy: extract a ≤2-sentence bio. Delegates to extract_description."""
        return HTMLFetcher.extract_description(text_content, url, scrape_log_id=scrape_log_id)

    @staticmethod
    def extract_description(text_content: str, url: str, scrape_log_id=None) -> str | None:
        """Extract a researcher description (up to 200 words) from plain text.

        Uses a single LLM call on text content (not HTML) for minimal input tokens.
        Output is capped with max_tokens and truncated to 200 words application-side.
        Returns the description string, or None if nothing could be extracted.
        """
        try:
            from publication import _openai_client, OPENAI_MODEL
        except ImportError:
            logging.error("Could not import OpenAI client for description extraction")
            return None

        prompt = (
            f"From the following text from a researcher's homepage at {url}, "
            "extract a professional description (up to 200 words) describing who this person is, "
            "their research interests, and their current position/affiliation. "
            "Return only the description text, nothing else. "
            "If no clear description can be extracted, reply with exactly: null\n\n"
            f"Content:\n{text_content[:3000]}"
        )
        try:
            from database import Database
            response = _openai_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=OPENAI_MODEL,
                max_completion_tokens=1024,
            )
            Database.log_llm_usage(
                "description_extraction", OPENAI_MODEL, response.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )
            desc = response.choices[0].message.content.strip()
            if desc.lower() in ("null", "none", ""):
                return None
            words = desc.split()
            if len(words) > 200:
                desc = ' '.join(words[:200])
            return desc
        except Exception as e:
            logging.error(f"Error extracting description from {url}: {e}")
            return None

    @staticmethod
    def validate_draft_url(url: str) -> str:
        """Validate a draft URL via HTTP HEAD with SSRF protection.

        Returns 'valid', 'invalid', or 'timeout'.
        """
        if not HTMLFetcher.validate_url(url):
            return 'invalid'

        try:
            # Disable auto-redirects to prevent SSRF via redirect to internal IPs
            response = HTMLFetcher.session.head(url, timeout=10, allow_redirects=False)
            if response.status_code < 400:
                return 'valid'
            # Follow redirects manually with SSRF validation (up to 5 hops)
            if response.status_code in (301, 302, 303, 307, 308):
                redirect_url = response.headers.get('Location')
                if redirect_url and HTMLFetcher.validate_url(redirect_url):
                    return HTMLFetcher.validate_draft_url(redirect_url)
                return 'invalid'
            return 'invalid'
        except requests.exceptions.Timeout:
            return 'timeout'
        except requests.exceptions.RequestException:
            return 'invalid'

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
    def needs_extraction(url_id):
        """
        Return True if LLM extraction is needed for this URL.
        Extraction is needed when content_hash differs from extracted_hash
        (content changed since last extraction, or never extracted).
        Returns False if no HTML has been downloaded yet.
        """
        query = """
            SELECT content_hash, extracted_hash
            FROM html_content
            WHERE url_id = %s
        """
        result = Database.fetch_one(query, (url_id,))
        if not result:
            return False  # No content downloaded yet — nothing to extract
        content_hash, extracted_hash = result
        return content_hash != extracted_hash

    @staticmethod
    def mark_extracted(url_id):
        """
        Record that extraction has been run on the current content by setting
        extracted_hash = content_hash and extracted_at = now.
        """
        query = """
            UPDATE html_content
            SET extracted_at = %s, extracted_hash = content_hash
            WHERE url_id = %s
        """
        Database.execute_query(query, (datetime.now(timezone.utc), url_id))

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
