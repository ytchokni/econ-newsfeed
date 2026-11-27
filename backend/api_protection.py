"""Anti-scraping protection for the public API.

Who is calling
--------------
Requests reach the API on two paths:

* Through the Next.js frontend on Vercel (browser calls rewritten by
  ``app/src/proxy.ts`` and server-side rendering). The frontend proves itself
  with the shared ``INTERNAL_API_KEY`` in ``X-Internal-Api-Key`` and names the
  visitor in ``X-Visitor-IP``.
* Directly at the Caddy host (econ-newsfeed.duckdns.org).

The client IP is resolved as follows:

1. The TCP peer. In production that is the Docker gateway (Caddy -> published
   port), in local dev it is loopback.
2. If the peer is private or loopback, the *rightmost* ``X-Forwarded-For``
   entry, which Caddy writes from the connection it accepted (Caddy ignores
   client-supplied forwarding headers unless ``trusted_proxies`` is set, and
   even when it appends, the rightmost value is its own). This is the "edge"
   IP: the visitor for direct traffic, a Vercel egress IP for frontend
   traffic. Client-supplied ``X-Real-IP`` and the left part of
   ``X-Forwarded-For`` are never trusted.
3. Only with a valid ``X-Internal-Api-Key``: the frontend's ``X-Visitor-IP``.

Tiers
-----
``internal``  valid key, no visitor (frontend server-to-server calls): exempt.
``ssr``       valid key, visitor named, rendered page: per-visitor page limits.
``visitor``   valid key, visitor named, proxied browser call: per-visitor limits.
``user``      valid NextAuth JWT: higher limits, no paging caps.
``direct``    key configured, request did not carry it: ``API_DIRECT_ACCESS``
              decides (``open`` = the ``legacy`` limits below, ``limited`` =
              strict limits, ``deny`` = 403 on data endpoints).
``legacy``    ``INTERNAL_API_KEY`` not configured, or configured here but not
              sent while ``API_DIRECT_ACCESS=open``: fail open with per-edge-IP
              per-minute limits close to the old behaviour. All site traffic
              arrives from a few Vercel IPs when the frontend lacks the key, so
              anything stricter would rate-limit the whole site.

Limits are kept in process memory by slowapi, so each gunicorn worker counts
separately (effective ceiling = limit x WEB_CONCURRENCY).
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import math
import os
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("backend.api_protection")

INTERNAL_KEY_HEADER = "X-Internal-Api-Key"
VISITOR_IP_HEADER = "X-Visitor-IP"
VISITOR_SOURCE_HEADER = "X-Visitor-Source"

INTERNAL = "internal"
SSR = "ssr"
VISITOR = "visitor"
USER = "user"
DIRECT = "direct"
LEGACY = "legacy"

# Tiers that get no paging caps.
UNCAPPED_TIERS = frozenset({INTERNAL, USER})

# Endpoints that expose the dataset. UA blocking and API_DIRECT_ACCESS=deny
# apply to these only, so health/ready, the RSS feed, auth/user endpoints,
# unsubscribe links and admin keep working for every client.
DATA_PATHS = (
    "/api/publications",
    "/api/researchers",
    "/api/researcher-options",
    "/api/filter-options",
    "/api/fields",
    "/api/jel-codes",
)

# Substrings (lower-case) of User-Agents that are HTTP libraries, scraping
# frameworks or bulk crawlers. Browsers and search-engine renderers (which
# fetch /api/* when rendering frontend pages) are deliberately not listed, nor
# is Node's fetch, which the frontend's own server uses.
BLOCKED_USER_AGENTS = (
    "python-requests", "python-urllib", "python-httpx", "aiohttp", "httpx/",
    "scrapy", "curl/", "wget/", "go-http-client", "java/", "okhttp",
    "libwww-perl", "apache-httpclient", "httpclient", "headlesschrome",
    "phantomjs", "puppeteer", "playwright", "selenium",
    "gptbot", "ccbot", "bytespider", "claudebot", "anthropic-ai",
    "perplexitybot", "amazonbot", "petalbot", "ahrefsbot", "semrushbot",
    "mj12bot", "dotbot", "dataforseobot", "blexbot", "zoominfobot",
    "imagesiftbot", "diffbot", "omgili", "timpibot",
)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    try:
        value = int(_env(name, str(default)))
    except ValueError:
        logger.warning("%s is not an integer; using %d", name, default)
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Settings:
    internal_key: str
    direct_access: str
    max_per_page: int
    max_depth: int
    limits: dict

    @classmethod
    def from_env(cls) -> "Settings":
        internal_key = os.environ.get("INTERNAL_API_KEY", "").strip()
        if internal_key and len(internal_key) < 16:
            logger.warning("INTERNAL_API_KEY is shorter than 16 characters; ignoring it")
            internal_key = ""
        direct = _env("API_DIRECT_ACCESS", "open").lower()
        if direct not in ("open", "limited", "deny"):
            logger.warning("API_DIRECT_ACCESS=%r is not open/limited/deny; using 'open'", direct)
            direct = "open"
        return cls(
            internal_key=internal_key,
            direct_access=direct,
            max_per_page=_env_int("API_MAX_PER_PAGE", 50),
            max_depth=_env_int("API_MAX_RESULT_DEPTH", 2000),
            limits={
                # Shared across publication/researcher lists and details.
                (VISITOR, "data"): _env("RATE_LIMIT_DATA", "60/minute;600/hour;3000/day"),
                (DIRECT, "data"): _env("RATE_LIMIT_DATA_DIRECT", "20/minute;200/hour;1000/day"),
                (USER, "data"): _env("RATE_LIMIT_DATA_USER", "120/minute;3000/hour;20000/day"),
                (LEGACY, "data"): _env("RATE_LIMIT_DATA_LEGACY", "240/minute"),
                # Server-rendered paper/researcher pages, per visitor. Generous
                # so search-engine crawlers are not turned away.
                (SSR, "data"): _env("RATE_LIMIT_SSR", "120/minute;3000/hour"),
                # Typeahead search over researcher names.
                (VISITOR, "options"): _env("RATE_LIMIT_OPTIONS", "60/minute;600/hour"),
                (DIRECT, "options"): _env("RATE_LIMIT_OPTIONS_DIRECT", "20/minute;200/hour"),
                (USER, "options"): "120/minute;2000/hour",
                (LEGACY, "options"): "120/minute",
                (SSR, "options"): "120/minute",
            },
        )


settings = Settings.from_env()
if not settings.internal_key:
    logger.warning(
        "INTERNAL_API_KEY is not set: API rate limits fall back to per-edge-IP "
        "per-minute limits and cannot tell site visitors apart behind Vercel."
    )


def reload_settings() -> None:
    """Re-read the environment (tests)."""
    global settings
    settings = Settings.from_env()


@dataclass(frozen=True)
class ClientInfo:
    tier: str
    ip: str
    ident: str  # rate-limit identity within the tier


def _parse_ip(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _is_internal_peer(peer: str | None) -> bool:
    """Peers we accept forwarding headers from: proxies on this host."""
    if not peer:
        return True
    ip = _parse_ip(peer)
    if ip is None:
        # Non-IP peers only occur in-process (e.g. Starlette's TestClient).
        return True
    addr = ipaddress.ip_address(ip)
    return addr.is_loopback or addr.is_private


def edge_ip(request: Request) -> str:
    """IP of whoever connected to Caddy: the visitor, or Vercel's egress."""
    peer = request.client.host if request.client else None
    if _is_internal_peer(peer):
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p for p in (s.strip() for s in forwarded.split(",")) if p]
        if parts:
            ip = _parse_ip(parts[-1])
            if ip:
                return ip
    return _parse_ip(peer) or (peer or "unknown")


def _valid_key(request: Request) -> bool | None:
    """True/False for a key match, None when the request carries no key."""
    supplied = request.headers.get(INTERNAL_KEY_HEADER)
    if supplied is None or not settings.internal_key:
        # No key on this side yet (e.g. Vercel was configured first): ignore it.
        return None
    return hmac.compare_digest(supplied.encode(), settings.internal_key.encode())


def _user_subject(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        from backend.auth import _decode_jwt
        payload = _decode_jwt(auth[7:])
    except Exception:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


_warned: dict[str, float] = {}
_warn_lock = threading.Lock()


def _warn_once(kind: str, message: str, *args, every: float = 300.0) -> None:
    """Log a warning at most once per ``every`` seconds per kind."""
    now = time.monotonic()
    with _warn_lock:
        if now - _warned.get(kind, -every) < every:
            return
        _warned[kind] = now
    logger.warning(message, *args)


def resolve_client(request: Request) -> ClientInfo:
    """Classify the caller; cached on ``request.state``."""
    cached = getattr(request.state, "client_info", None)
    if cached is not None:
        return cached

    edge = edge_ip(request)
    key = _valid_key(request)
    if key is False:
        _warn_once(
            "invalid_key",
            "internal_key_invalid ip=%s path=%s: request carried a wrong %s",
            edge, request.url.path, INTERNAL_KEY_HEADER,
        )

    ip = edge
    trusted = key is True
    if trusted:
        raw_visitor = request.headers.get(VISITOR_IP_HEADER)
        if raw_visitor is not None:
            ip = _parse_ip(raw_visitor.split(",")[0]) or "unknown"

    sub = _user_subject(request)
    if sub:
        info = ClientInfo(USER, ip, sub)
    elif trusted:
        if request.headers.get(VISITOR_IP_HEADER) is None:
            info = ClientInfo(INTERNAL, ip, "frontend")
        elif request.headers.get(VISITOR_SOURCE_HEADER, "").lower() == "ssr":
            info = ClientInfo(SSR, ip, ip)
        else:
            info = ClientInfo(VISITOR, ip, ip)
    elif not settings.internal_key or settings.direct_access == "open":
        info = ClientInfo(LEGACY, ip, ip)
    else:
        info = ClientInfo(DIRECT, ip, ip)

    request.state.client_info = info
    return info


# ---------------------------------------------------------------------------
# slowapi hooks
# ---------------------------------------------------------------------------

def rate_limit_key(request: Request) -> str:
    info = resolve_client(request)
    return f"{info.tier}:{info.ident}"


def is_internal(request: Request) -> bool:
    return resolve_client(request).tier == INTERNAL


def _tier_of_key(key: str) -> str:
    return key.split(":", 1)[0]


def data_limit(key: str) -> str:
    tier = _tier_of_key(key)
    return settings.limits.get((tier, "data")) or settings.limits[(VISITOR, "data")]


def options_limit(key: str) -> str:
    tier = _tier_of_key(key)
    return settings.limits.get((tier, "options")) or settings.limits[(VISITOR, "options")]


# ---------------------------------------------------------------------------
# Paging caps
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Paging:
    per_page: int
    offset: int
    max_pages: int | None  # None = uncapped


def enforce_paging(request: Request, page: int, per_page: int) -> Paging:
    """Cap ``per_page`` and paging depth for anonymous callers.

    ``per_page`` above the cap is clamped rather than rejected, so an older
    frontend asking for 100 still gets a page. Pages beyond the depth cap get
    400: narrowing the filters (or signing in) reaches the rest.
    """
    tier = resolve_client(request).tier
    if tier in UNCAPPED_TIERS:
        return Paging(per_page, (page - 1) * per_page, None)
    per_page = min(per_page, settings.max_per_page)
    offset = (page - 1) * per_page
    max_pages = max(1, math.ceil(settings.max_depth / per_page))
    if page > max_pages:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Results are limited to the first {settings.max_depth} items; "
                "narrow the filters or sign in to see more."
            ),
        )
    return Paging(per_page, offset, max_pages)


def cap_pages(pages: int, paging: Paging) -> int:
    return pages if paging.max_pages is None else min(pages, paging.max_pages)


# ---------------------------------------------------------------------------
# Request gate (User-Agent, direct-access policy)
# ---------------------------------------------------------------------------

def is_data_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in DATA_PATHS)


def blocked_user_agent(user_agent: str | None) -> bool:
    ua = (user_agent or "").strip().lower()
    if not ua:
        return True
    return any(marker in ua for marker in BLOCKED_USER_AGENTS)


def _forbidden(message: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": message}})


def gate_request(request: Request) -> JSONResponse | None:
    """Return a 403 response for blocked requests to data endpoints, else None."""
    if not is_data_path(request.url.path):
        return None
    info = resolve_client(request)
    if info.tier in (INTERNAL, USER):
        return None
    if info.tier == DIRECT and settings.direct_access == "deny":
        logger.info("api_blocked reason=direct ip=%s path=%s", info.ip, request.url.path)
        return _forbidden("This API is only available through https://www.econnewsfeed.org.")
    ua = request.headers.get("user-agent")
    if blocked_user_agent(ua):
        logger.info(
            "api_blocked reason=user_agent ip=%s path=%s ua=%r",
            info.ip, request.url.path, (ua or "")[:120],
        )
        return _forbidden("Automated access to this API is not permitted.")
    return None


def retry_after_seconds(request: Request) -> int:
    """Seconds until the limit that was just exceeded resets (fallback 60)."""
    try:
        limit_item, args = request.state.view_rate_limit
        stats = request.app.state.limiter.limiter.get_window_stats(limit_item, *args)
        return max(1, math.ceil(stats.reset_time - time.time()))
    except Exception:
        return 60
