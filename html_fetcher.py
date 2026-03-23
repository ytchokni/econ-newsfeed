import difflib
import ipaddress
import os
import threading
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

RATE_LIMIT_SECONDS = float(os.environ.get('SCRAPE_RATE_LIMIT_SECONDS', '2'))
RATE_LIMIT_FAST_SECONDS = float(os.environ.get('SCRAPE_RATE_LIMIT_FAST_SECONDS', '0.5'))
CONTENT_MAX_CHARS = int(os.environ.get('CONTENT_MAX_CHARS'))
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
    _thread_local = threading.local()

    @staticmethod
    def _get_session() -> requests.Session:
        """Get or create a thread-local requests.Session."""
        if not hasattr(HTMLFetcher._thread_local, 'session'):
            s = requests.Session()
            s.headers.update({'User-Agent': SCRAPER_USER_AGENT})
            HTMLFetcher._thread_local.session = s
        return HTMLFetcher._thread_local.session

    # Per-domain rate limiting: domain -> last request timestamp
    _domain_last_request = {}
    # Protects creation of per-domain locks
    _domain_locks_global = threading.Lock()
    # Per-domain locks to make the rate-limit check-and-update atomic
    _domain_locks: dict = {}

    # Cache parsed robots.txt per origin (scheme://netloc).
    # Value is a RobotFileParser or None (fetch failed / non-200).
    _robots_cache: dict = {}

    @staticmethod
    def _get_robots_parser(url: str) -> "RobotFileParser | None":
        """Get or fetch the RobotFileParser for a URL's domain. Cached per origin."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in HTMLFetcher._robots_cache:
            return HTMLFetcher._robots_cache[origin]
        try:
            robots_url = f"{origin}/robots.txt"
            resp = requests.get(robots_url, timeout=10, headers={
                'User-Agent': SCRAPER_USER_AGENT,
            })
            if resp.status_code != 200:
                HTMLFetcher._robots_cache[origin] = None
                return None
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            HTMLFetcher._robots_cache[origin] = rp
            return rp
        except Exception:
            HTMLFetcher._robots_cache[origin] = None
            return None

    @staticmethod
    def is_allowed_by_robots(url: str) -> bool:
        """Check if the URL is allowed by the site's robots.txt. Cached per domain."""
        rp = HTMLFetcher._get_robots_parser(url)
        if rp is None:
            return True
        allowed = rp.can_fetch('HTMLFetcher/1.0', url)
        if not allowed:
            logging.info(f"URL disallowed by robots.txt: {url}")
        return allowed

    @staticmethod
    def validate_url(url: str) -> bool:
        """Validate a URL for SSRF protection. Returns True if safe."""
        safe, _ = HTMLFetcher.validate_url_with_pin(url)
        return safe

    @staticmethod
    def validate_url_with_pin(url: str) -> tuple[bool, str | None]:
        """Validate URL for SSRF and return (is_safe, resolved_ip).
        The resolved IP should be used for the actual HTTP request to prevent DNS rebinding."""
        try:
            parsed = urlparse(url)
        except Exception:
            return False, None

        if parsed.scheme not in ('http', 'https'):
            return False, None

        hostname = parsed.hostname
        if not hostname:
            return False, None

        if hostname in ('169.254.169.254', 'metadata.google.internal'):
            return False, None

        try:
            resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(resolved_ip)
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                logging.warning("Rejected URL resolving to private/reserved IP: %s -> [redacted]", url)
                return False, None
        except (socket.gaierror, ValueError):
            logging.warning(f"Could not resolve hostname for URL: {url}")
            return False, None

        return True, resolved_ip

    @staticmethod
    def _rate_limit(url: str) -> None:
        """Enforce per-domain rate limiting (thread-safe)."""
        domain = urlparse(url).hostname
        limit = RATE_LIMIT_FAST_SECONDS if _is_fast_domain(domain) else RATE_LIMIT_SECONDS
        with HTMLFetcher._domain_locks_global:
            if domain not in HTMLFetcher._domain_locks:
                HTMLFetcher._domain_locks[domain] = threading.Lock()
            domain_lock = HTMLFetcher._domain_locks[domain]
        with domain_lock:
            if domain in HTMLFetcher._domain_last_request:
                elapsed = time.time() - HTMLFetcher._domain_last_request[domain]
                if elapsed < limit:
                    wait = limit - elapsed
                    logging.info(f"Rate limiting: waiting {wait:.1f}s for {domain}")
                    time.sleep(wait)
            HTMLFetcher._domain_last_request[domain] = time.time()

    @staticmethod
    def fetch_html(url: str, timeout: int = 10, max_retries: int = 3) -> str | None:
        """
        Fetch HTML content from a given URL with exponential backoff.
        Retries on timeouts and 5xx server errors.

        Returns the HTML content as a string, or None on failure.
        """
        HTMLFetcher._rate_limit(url)
        session = HTMLFetcher._get_session()

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
    def extract_text_content(html_content: str) -> str:
        """
        Extract only the text content from HTML, ignoring scripts, styles, and HTML tags.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        for script in soup(["script", "style"]):
            script.decompose()
        return soup.get_text()

    @staticmethod
    def hash_text_content(text_content: str) -> str:
        """
        Hash the text content using SHA-256.
        """
        return hashlib.sha256(text_content.encode('utf-8')).hexdigest()

    @staticmethod
    def save_text(url_id: int, text_content: str, text_hash: str, researcher_id: int, raw_html=None) -> None:
        """
        Save pre-extracted text content and hash to the database using upsert.
        Also stores raw_html if provided.
        """
        query = """
            INSERT INTO html_content (url_id, content, content_hash, timestamp, researcher_id, raw_html)
            VALUES (%s, %s, %s, %s, %s, %s) AS new_row
            ON DUPLICATE KEY UPDATE
                content = new_row.content,
                content_hash = new_row.content_hash,
                timestamp = new_row.timestamp,
                raw_html = new_row.raw_html
        """
        try:
            Database.execute_query(query, (url_id, text_content, text_hash, datetime.now(timezone.utc), researcher_id, raw_html))
            logging.info(f"Text content saved for URL ID: {url_id} (Researcher ID: {researcher_id})")
        except Exception as e:
            logging.error("Error saving text content for URL ID %s: %s", url_id, type(e).__name__)

    @staticmethod
    def has_text_changed(url_id: int, new_text_hash: str) -> bool:
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
            return result['content_hash'] != new_text_hash
        return True  # No previous record — treat as changed

    @staticmethod
    def _was_fetched_recently(url_id: int, hours: int = 24) -> bool:
        """Return True if this URL was successfully fetched within the last N hours."""
        result = Database.fetch_one(
            "SELECT timestamp FROM html_content WHERE url_id = %s", (url_id,)
        )
        if not result or not result['timestamp']:
            return False
        # MySQL connector returns naive datetimes (no tzinfo) stored as UTC.
        # Use replace() only when the value is naive; use astimezone() if aware.
        ts = result['timestamp']
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        age = datetime.now(timezone.utc) - ts
        return age.total_seconds() < hours * 3600

    @staticmethod
    def fetch_and_save_if_changed(url_id: int, url: str, researcher_id: int) -> bool:
        """
        Fetch HTML content from the given URL and save its text content if it has changed.
        Skips fetching if the URL was successfully fetched within the last 24 hours.
        Returns True if content changed, False otherwise.
        """
        if HTMLFetcher._was_fetched_recently(url_id):
            logging.info(f"Skipping URL ID {url_id} (fetched <24h ago): {url}")
            return False

        is_safe, _resolved_ip = HTMLFetcher.validate_url_with_pin(url)
        if not is_safe:
            logging.warning(f"URL failed SSRF validation, skipping: {url}")
            return False

        if not HTMLFetcher.is_allowed_by_robots(url):
            return False

        raw_html = HTMLFetcher.fetch_html(url)
        if not raw_html:
            logging.warning(f"Failed to fetch HTML content for URL ID: {url_id}, URL: {url}")
            return False

        # Parse HTML once, reuse for both comparison and storage
        text_content = HTMLFetcher.extract_text_content(raw_html)
        if len(text_content) > CONTENT_MAX_CHARS:
            dropped = len(text_content) - CONTENT_MAX_CHARS
            logging.info(f"Truncating content for URL ID {url_id}: {len(text_content)} -> {CONTENT_MAX_CHARS} chars ({dropped} dropped)")
            text_content = text_content[:CONTENT_MAX_CHARS]
        text_hash = HTMLFetcher.hash_text_content(text_content)

        if HTMLFetcher.has_text_changed(url_id, text_hash):
            HTMLFetcher.save_text(url_id, text_content, text_hash, researcher_id, raw_html=raw_html)
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
            f"Content:\n{text_content[:CONTENT_MAX_CHARS]}"
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
    def validate_draft_url(url: str, max_redirects: int = 5) -> str:
        """Validate a draft URL via HTTP HEAD with SSRF protection.

        Follows up to max_redirects hops manually to prevent SSRF via redirect chains.
        Returns 'valid', 'invalid', or 'timeout'.
        """
        if not HTMLFetcher.validate_url(url):
            return 'invalid'

        try:
            # Disable auto-redirects to prevent SSRF via redirect to internal IPs
            response = HTMLFetcher._get_session().head(url, timeout=10, allow_redirects=False)
            if response.status_code < 400:
                return 'valid'
            # Follow redirects manually with SSRF validation
            if response.status_code in (301, 302, 303, 307, 308):
                if max_redirects <= 0:
                    logging.warning(f"Redirect limit reached for URL: {url}")
                    return 'invalid'
                redirect_url = response.headers.get('Location')
                if redirect_url and HTMLFetcher.validate_url(redirect_url):
                    return HTMLFetcher.validate_draft_url(redirect_url, max_redirects=max_redirects - 1)
                return 'invalid'
            return 'invalid'
        except requests.exceptions.Timeout:
            return 'timeout'
        except requests.exceptions.RequestException:
            return 'invalid'

    @staticmethod
    def get_latest_text(url_id: int) -> str | None:
        """Retrieve the latest text content for a given URL ID."""
        query = """
            SELECT content
            FROM html_content
            WHERE url_id = %s
        """
        result = Database.fetch_one(query, (url_id,))
        return result['content'] if result else None

    @staticmethod
    def get_raw_html(url_id: int) -> str | None:
        """Retrieve stored raw HTML for a URL ID."""
        result = Database.fetch_one(
            "SELECT raw_html FROM html_content WHERE url_id = %s", (url_id,),
        )
        return result['raw_html'] if result else None

    @staticmethod
    def needs_extraction(url_id: int) -> bool:
        """
        Return True if LLM extraction is needed for this URL.
        Extraction is needed when content_hash differs from extracted_hash
        (content changed since last extraction, or never extracted).
        Returns False if no HTML has been downloaded yet.
        """
        result = Database.fetch_one(
            "SELECT content_hash, extracted_hash FROM html_content WHERE url_id = %s",
            (url_id,),
        )
        if not result:
            return False
        return result['content_hash'] != result['extracted_hash']

    @staticmethod
    def is_first_extraction(url_id: int) -> bool:
        """Return True if this URL has never been extracted before.

        Uses extracted_at IS NULL as the signal — mark_extracted() has
        never been called for this URL.
        """
        result = Database.fetch_one(
            "SELECT extracted_at FROM html_content WHERE url_id = %s",
            (url_id,),
        )
        return result is not None and result['extracted_at'] is None

    @staticmethod
    def mark_extracted(url_id: int) -> None:
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
    def get_previous_text(url_id: int) -> str | None:
        """Retrieve the previous text content for a given URL ID (before current upsert).
        With upsert, there's only one row per url_id, so this returns the current stored
        content (which represents the *previous* version before fetch_and_save_if_changed
        overwrites it).
        """
        return HTMLFetcher.get_latest_text(url_id)

    @staticmethod
    def compute_diff(old_text: str, new_text: str) -> str:
        """Compute a unified diff between old and new text content.
        Returns only added/changed lines to reduce LLM token usage.
        """
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, n=1)
        # Extract only added lines (starting with '+' but not '+++')
        added_lines = [line[1:] for line in diff if line.startswith('+') and not line.startswith('+++')]
        return ''.join(added_lines) if added_lines else new_text
