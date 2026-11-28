"""Cheap discovery sources and bounded, robots-aware public HTML fetching.

Directory adapters only produce candidates. They never identify database people
or approve crawl URLs; the mining queue performs those checks separately.
"""
from __future__ import annotations

import ipaddress
import json
import queue
import re
import threading
import time
import unicodedata
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag
from urllib3.util import Timeout

from backend.discovery.mining_policy import norm_name
from backend.pipeline.html_fetcher import HTMLFetcher, _pin_dns


USER_AGENT = "EconNewsfeedDiscovery/1.0 (+https://econ-newsfeed.vercel.app)"
MAX_BYTES = 2_000_000
_REDIRECTS = {301, 302, 303, 307, 308}
_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_LAST: dict[str, float] = {}
_HOST_GUARD = threading.Lock()


class BudgetExceeded(Exception):
    """The run must checkpoint its queue before attempting another request."""


class _FetchFailure(Exception):
    pass


def _public_url(url: str) -> str:
    """Validate URL syntax before DNS; DNS validation happens at every hop."""
    try:
        if not isinstance(url, str) or any(ord(c) < 32 for c in url) or "\\" in url:
            raise ValueError
        parsed = urlparse(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 80, 443}):
            raise ValueError
        return urldefrag(url)[0]
    except (ValueError, TypeError):
        raise _FetchFailure("unsafe_url") from None


class MiningFetcher:
    """Fetch HTML within a shared request/time budget, including robots requests.

    Each GET and retry consumes one request. Robots failures fail closed and
    remain retryable on a later call; they never become an implicit allow rule.
    Successful robots policies are cached for this bounded run only.
    """

    def __init__(self, max_requests: int = 200, max_seconds: float = 600,
                 host_delays: dict[str, float] | None = None):
        if max_requests < 0 or max_seconds <= 0:
            raise ValueError("Invalid mining fetch budget")
        self.max_requests = max_requests
        # Per-host minimum spacing (seconds) on top of the 1 s floor and any
        # robots.txt Crawl-delay, e.g. for registries queried many times a run.
        self.host_delays = {h.lower(): float(d) for h, d in (host_delays or {}).items()}
        self.deadline = time.monotonic() + max_seconds
        self.requests = 0
        self._exhausted = False
        self._robots: dict[str, RobotFileParser | bool] = {}
        self._crawl_delays: dict[str, float] = {}

    @property
    def budget_exhausted(self) -> bool:
        return self._exhausted or self.requests >= self.max_requests or time.monotonic() >= self.deadline

    def _stop(self, reason: str) -> None:
        self._exhausted = True
        raise BudgetExceeded(reason)

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self._exhausted:
            self._stop("Mining time budget exhausted")
        return remaining

    def _request_budget(self) -> None:
        self._remaining()
        if self.requests >= self.max_requests:
            self._stop("Mining request budget exhausted")

    def _sleep(self, seconds: float) -> None:
        if seconds >= self._remaining():
            self._stop("Insufficient mining time for host pacing")
        if seconds > 0:
            time.sleep(seconds)

    def _validated_ip(self, url: str) -> str:
        # The existing resolver has its own timeout, but its executor shutdown
        # may wait for system DNS. A daemon wrapper keeps this run bounded.
        result: queue.Queue = queue.Queue(maxsize=1)

        def validate() -> None:
            try:
                result.put(HTMLFetcher.validate_url_with_pin(url))
            except Exception:
                result.put((False, None))

        threading.Thread(target=validate, daemon=True, name="mining-dns").start()
        try:
            safe, address = result.get(timeout=min(10.0, self._remaining()))
        except queue.Empty:
            self._remaining()
            raise _FetchFailure("dns_timeout") from None
        self._remaining()
        try:
            address_info = ipaddress.ip_address(address) if address else None
            if not safe or not address_info or not address_info.is_global or address_info.is_multicast:
                raise ValueError
        except ValueError:
            raise _FetchFailure("unsafe_or_unresolved_url") from None
        return address

    def _get(self, url: str, *, robots: bool = False, accept: str = "html",
             headers: dict | None = None) -> dict:
        url = _public_url(url)
        self._request_budget()
        address = self._validated_ip(url)
        host = urlparse(url).hostname
        with _HOST_GUARD:
            lock = _HOST_LOCKS.setdefault(host, threading.Lock())
        if not lock.acquire(timeout=self._remaining()):
            self._stop("Mining time budget exhausted waiting for host")
        try:
            delay = max(1.0, self._crawl_delays.get(host, 1.0), self.host_delays.get(host.lower(), 0.0))
            self._sleep(max(0.0, delay - (time.monotonic() - _HOST_LAST.get(host, -float("inf")))))
            self._request_budget()
            remaining = self._remaining()
            with requests.Session() as session:
                session.trust_env = False
                # A fresh session avoids reusing an earlier, differently pinned
                # connection; the original hostname still verifies the TLS cert.
                with _pin_dns(host, address):
                    self.requests += 1
                    _HOST_LAST[host] = time.monotonic()
                    with session.get(
                        url, headers={**(headers or {}), "User-Agent": USER_AGENT}, stream=True,
                        allow_redirects=False,
                        timeout=Timeout(total=remaining, connect=min(8.0, remaining), read=min(15.0, remaining)),
                    ) as response:
                        self._remaining()
                        status = response.status_code
                        ctype = response.headers.get("Content-Type", "")
                        record = {"url": url, "status": status, "html": "", "content_type": ctype}
                        if status in _REDIRECTS:
                            record["location"] = response.headers.get("Location", "")
                            return record
                        if status != 200:
                            return record
                        mime = ctype.split(";", 1)[0].strip().lower()
                        if not robots and accept == "json" and not (mime == "application/json" or mime.endswith("+json")):
                            record["error"] = "not_json"
                            return record
                        if not robots and accept != "json" and mime not in {"text/html", "application/xhtml+xml"}:
                            record["error"] = "not_html"
                            return record
                        try:
                            if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
                                raise _FetchFailure("response_too_large")
                        except ValueError:
                            pass
                        chunks, size = [], 0
                        for chunk in response.iter_content(chunk_size=32768):
                            self._remaining()
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise _FetchFailure("response_too_large")
                            chunks.append(chunk)
                        payload = b"".join(chunks)
                        try:
                            record["html"] = payload.decode(response.encoding or "utf-8", errors="replace")
                        except LookupError:
                            record["html"] = payload.decode("utf-8", errors="replace")
                        return record
        finally:
            lock.release()

    def _get_with_retry(self, url: str, *, robots: bool = False, accept: str = "html",
                        headers: dict | None = None) -> dict:
        for attempt in range(2):
            try:
                result = self._get(url, robots=robots, accept=accept, headers=headers)
            except requests.RequestException:
                if attempt:
                    raise _FetchFailure("network_error") from None
            else:
                if result["status"] < 500 or attempt:
                    return result
            self._sleep(1.0)
        raise AssertionError("Unreachable retry state")

    def _load_robots(self, origin: str) -> RobotFileParser | bool:
        current = origin + "/robots.txt"
        for redirects in range(6):
            result = self._get_with_retry(current, robots=True)
            status = result["status"]
            if status in _REDIRECTS:
                if redirects == 5 or not result["location"]:
                    raise _FetchFailure("robots_redirect_error")
                current = _public_url(urljoin(current, result["location"]))
                continue
            if status in {404, 410}:
                return True
            if status in {401, 403}:
                return False
            if status != 200:
                raise _FetchFailure("robots_unavailable")
            if re.search(r"<(?:!doctype\s+html|html|body)\b", result["html"], re.I):
                raise _FetchFailure("robots_invalid_html")
            parser = RobotFileParser(origin + "/robots.txt")
            parser.parse(result["html"].splitlines())
            delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*")
            rate = parser.request_rate(USER_AGENT) or parser.request_rate("*")
            if rate and rate.requests > 0:
                delay = max(delay or 0, rate.seconds / rate.requests)
            if delay:
                self._crawl_delays[urlparse(origin).hostname] = max(1.0, float(delay))
            return parser
        raise AssertionError("Unreachable robots redirect state")

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(_public_url(url))
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        policy = self._robots[origin]
        return policy if isinstance(policy, bool) else policy.can_fetch(USER_AGENT, url)

    def fetch(self, url: str, *, accept: str = "html", headers: dict | None = None) -> dict:
        """Return a final-URL HTML result or a classified failure; never writes DB.

        ``accept="json"`` admits JSON bodies instead of HTML. ``headers`` (an
        API key, say) are sent only to the original host, never after a
        redirect to another one.
        """
        current = url
        try:
            current = _public_url(current)
            origin_host = urlparse(current).hostname
            self._request_budget()
            for redirects in range(6):
                if not self._allowed(current):
                    return {"url": current, "status": "robots_disallowed", "html": "", "content_type": "", "error": "robots_disallowed"}
                same_host = urlparse(current).hostname == origin_host
                result = self._get_with_retry(current, accept=accept, headers=headers if same_host else None)
                if result["status"] in _REDIRECTS:
                    if redirects == 5 or not result["location"]:
                        raise _FetchFailure("redirect_error")
                    current = _public_url(urljoin(current, result["location"]))
                    continue
                if result["status"] != 200:
                    result["error"] = f"http_{result['status']}"
                return result
        except BudgetExceeded:
            raise
        except _FetchFailure as exc:
            return {"url": current, "status": "error", "html": "", "content_type": "", "error": str(exc)}
        raise AssertionError("Unreachable redirect state")

    def fetch_json(self, url: str, *, headers: dict | None = None) -> dict:
        """Like ``fetch`` for a JSON API; a parsed body is under ``"json"``."""
        result = self.fetch(url, accept="json", headers=headers)
        if result["status"] == 200 and not result.get("error"):
            try:
                result["json"] = json.loads(result.pop("html") or "null")
            except ValueError:
                result.update(status="error", error="invalid_json")
            result.setdefault("html", "")
        return result


# The October 2026 trial verified these directory URLs and card selectors.
# Oxford and Toulouse are the candidate listings linked from their people pages;
# their general directories are JS-driven or do not expose personal sites.
# Re-checked November 2026: all eight URLs still answer 200 and allow our
# robots.txt user agent, but four had changed their markup and returned no
# links. Stanford labels links "<Surname> website", Oxford "Visit <First>'s
# personal website", Toulouse cards are `.card-job-market` divs, and Bonn's
# rows link profiles with icon-only anchors (its profiles name the person in
# `.contact-name .name` rather than a heading). Recorded fixtures live in
# tests/fixtures/mining/.
DIRECTORY_SEEDS = [
    {"name": "Stanford", "url": "https://economics.stanford.edu/people/faculty"},
    {"name": "MIT", "url": "https://economics.mit.edu/people/faculty"},
    {"name": "Berkeley", "url": "https://econ.berkeley.edu/people/faculty"},
    {"name": "Princeton", "url": "https://economics.princeton.edu/people/"},
    {"name": "LSE", "url": "https://www.lse.ac.uk/economics/people/faculty"},
    {"name": "Bonn", "url": "https://www.econ.uni-bonn.de/en/people/professors?set_language=en"},
    {"name": "Oxford", "url": "https://www.economics.ox.ac.uk/job-market-candidates"},
    {"name": "Toulouse", "url": "https://www.tse-fr.eu/job-market-candidates-tse"},
]

_WEBSITE_LABEL = re.compile(r"^(?:personal\s+(?:web\s*site|web\s*page)|home\s*page|visit\s+(?:personal\s+)?website|website|personal)$", re.I)
_NONPERSON = re.compile(r"\b(?:supervisor|supervisors|faculty|department|directory|search|contact|website|research|professors|emeritus|economics|students|candidates|read more|view profile)\b", re.I)
_NONWEBSITE_HOSTS = ("linkedin.com", "facebook.com", "twitter.com", "x.com", "youtube.com", "researchgate.net", "scholar.google.com", "orcid.org", "ideas.repec.org", "ssrn.com")
_FILE_LINK = re.compile(r"\.(?:pdf|docx?|png|jpe?g|gif|svg|zip|pptx?|xlsx?|bib|tex|csv)$", re.I)


def _text(tag: Tag | None) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip() if tag else ""


def _name(value: str) -> str | None:
    value = re.sub(r"^(?:(?:Prof\.?|Dr\.?|Professor|JProf\.?|em\.?|h\.c\.?|mult\.?|Sir|Lord)\s+)+", "", value.strip(), flags=re.I)
    value = re.sub(r"\s*(?:Ph\.?D\.?|Currently on leave)\s*$", "", value, flags=re.I).strip()
    if value.count(",") == 1:
        value = " ".join(reversed([part.strip() for part in value.split(",")]))
    if (not 2 <= len(value.split()) <= 7 or len(value) > 100 or _NONPERSON.search(value)
            or any(c.isdigit() for c in value) or not all(c.isalpha() or c in " .-'’" for c in value)):
        return None
    return value


def _name_key(value: str) -> str:
    value = _name(value) or value
    return "".join(c for c in unicodedata.normalize("NFKD", value).casefold() if c.isalpha())


def _link(anchor: Tag, base: str) -> str | None:
    try:
        target = _public_url(urljoin(base, anchor.get("href", "").strip()))
    except _FetchFailure:
        return None
    parsed = urlparse(target)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if (_FILE_LINK.search(parsed.path) or target.rstrip("/") == urldefrag(base)[0].rstrip("/")
            or (host == (urlparse(base).hostname or "").removeprefix("www.") and re.search(r"/[^/]+\.(?:com|org|net)$", parsed.path, re.I))
            or any(host == other or host.endswith("." + other) for other in _NONWEBSITE_HOSTS)):
        return None
    return target


def _supervisor_link(anchor: Tag, boundary: Tag) -> bool:
    for ancestor in (anchor, *anchor.parents):
        if ancestor is boundary:
            break
        classes = " ".join(ancestor.get("class", [])) if isinstance(ancestor, Tag) else ""
        if re.search(r"supervis|advis", classes, re.I):
            return True
        if isinstance(ancestor, Tag) and ancestor.name in {"p", "li", "dd"} and re.search(r"\b(?:supervisor|advisor)s?\b", _text(ancestor), re.I):
            return True
    return False


_OWNED_WEBSITE_LABEL = re.compile(
    r"^(?:visit\s+)?(?P<who>[^\W\d_][\w.'’ -]{0,60}?)(?:['’]s|['’])?\s+"
    r"(?:personal\s+)?(?:web\s*site|web\s*page|home\s*page)$", re.I)


def _explicit_website(anchor: Tag, name: str | None = None) -> bool:
    label = _text(anchor).strip(" :")
    if _WEBSITE_LABEL.fullmatch(label):
        return True
    # "Abramitzky website" (Stanford) or "Visit Carlos' personal website"
    # (Oxford): the label names the card's own person, never a lab or centre.
    match = _OWNED_WEBSITE_LABEL.fullmatch(label)
    if match and name:
        who = norm_name(match.group("who")).split()
        tokens = norm_name(name).split()
        # "Chris" on Christopher's own card: a prefix of at least 3 letters.
        if who and all(any(t == w or (len(w) >= 3 and t.startswith(w)) for t in tokens) for w in who):
            return True
    parent = anchor.find_parent(class_="contact-info__item")
    if parent and len(_text(parent)) < 350 and re.search(r"\bPersonal Website\b", _text(parent), re.I):
        return True
    return False


_GERMAN_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def _slug_names(href: str, name: str) -> bool:
    """True if the URL's last path segment is the person's name ("olga-balakina")."""
    cleaned = _name(name)
    slug = urlparse(href).path.rstrip("/").rsplit("/", 1)[-1]
    if not cleaned or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", slug, re.I):
        return False
    key = _name_key(slug.replace("-", " "))
    return key in {_name_key(cleaned), _name_key(cleaned.translate(_GERMAN_TRANSLIT))}


def directory_links(seed: dict | str, html: str, url: str) -> list[dict]:
    """Extract only labelled people and explicit profile/site links from a seed."""
    department = seed["name"] if isinstance(seed, dict) else seed
    registered = next((item for item in DIRECTORY_SEEDS if item["name"] == department), None)
    if not registered or (urlparse(url).hostname or "").removeprefix("www.") != (urlparse(registered["url"]).hostname or "").removeprefix("www."):
        return []
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    found: dict[tuple[str, str, str], dict] = {}

    def add(name_text: str, anchor: Tag, kind: str) -> None:
        name, target = _name(name_text), _link(anchor, url)
        if not name or not target:
            return
        if kind == "profile":
            # A named Bonn professor can link to an institute subdomain. Keep
            # traversal inside this adapter's explicitly known institution.
            institution = {"MIT": "mit.edu", "Berkeley": "berkeley.edu", "LSE": "lse.ac.uk", "Bonn": "uni-bonn.de"}.get(department)
            host = (urlparse(target).hostname or "").removeprefix("www.")
            if not institution or not (host == institution or host.endswith("." + institution)):
                return
        record = {"name": name, "url": target, "kind": kind}
        found[(name, target, kind)] = record

    if department in {"Stanford", "Princeton", "Oxford", "Toulouse"}:
        selectors = {"Stanford": ".hb-card", "Princeton": ".person", "Oxford": "article",
                     "Toulouse": "article, .card-job-market, .job-market-candidate, .candidate"}
        for card in main.select(selectors[department]):
            heading = card.select_one("h2,h3")
            if not heading:
                continue
            for anchor in card.select("a[href]"):
                if _explicit_website(anchor, _name(_text(heading))) and not _supervisor_link(anchor, card):
                    add(_text(heading), anchor, "website")
    elif department == "MIT":
        for anchor in main.select("a.profile-teaser[href]"):
            heading = anchor.select_one("h3")
            if heading:
                add(_text(heading), anchor, "profile")
    elif department == "Berkeley":
        for anchor in main.select('a[href*="/profile/"]'):
            add(_text(anchor), anchor, "profile")
    elif department == "LSE":
        for anchor in main.select("a[href]"):
            if re.search(r"/people/[^/]+/?$", urlparse(urljoin(url, anchor["href"])).path):
                add(_text(anchor), anchor, "profile")
    elif department == "Bonn":
        for card in main.select(".table-row.content-row"):
            heading = card.select_one(".fullname")
            if heading:
                # Prefer the named profile anchor, never a supervisor or generic
                # neighbouring link that happens to be first in the row.
                for anchor in card.select("a[href]"):
                    if _supervisor_link(anchor, card):
                        continue
                    # Since late 2026 the row links its profile with an
                    # icon-only "Show details" anchor whose slug is the name.
                    if (_name_key(_text(anchor)) == _name_key(_text(heading))
                            or (not _text(anchor) and _slug_names(anchor.get("href", ""), _text(heading)))):
                        add(_text(heading), anchor, "profile")
                        break
    return list(found.values())


def profile_websites(name: str, html: str, url: str) -> list[dict]:
    """Extract explicit personal links only from a profile headed by this person."""
    cleaned = _name(name)
    if not cleaned:
        return []
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    key = _name_key(cleaned)
    # Bonn's business-card profiles name the person in `.contact-name .name`.
    if not any(_name_key(_text(h)) == key for h in main.select("h1,h2,h3,.contact-name .name")):
        return []
    found = {}
    for anchor in main.select("a[href]"):
        if not _explicit_website(anchor, cleaned) or _supervisor_link(anchor, main):
            continue
        # Related-person cards inside a profile are independent people.
        wrong_card = False
        for parent in anchor.parents:
            if parent is main:
                break
            if isinstance(parent, Tag) and (parent.name in {"article", "section", "li"}
                    or re.search(r"card|person|teaser|profile|related|faculty", " ".join(parent.get("class", [])), re.I)):
                heading = parent.select_one("h1,h2,h3,h4")
                if heading and _name(_text(heading)) and _name_key(_text(heading)) != key:
                    wrong_card = True
                    break
        target = _link(anchor, url)
        if wrong_card or not target:
            continue
        found[target] = {
            "name": cleaned, "url": target, "source_url": url, "source_kind": "directory",
            "evidence": {"profile_name": cleaned, "anchor_text": _text(anchor), "explicit_personal_link": True},
        }
    return list(found.values())
