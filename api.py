"""FastAPI REST API for econ-newsfeed."""
import asyncio
import hmac
import logging
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from database import Database, connection_scope
from publication import VALID_STATUSES
import scheduler
from scheduler import (
    start_scheduler,
    shutdown_scheduler,
    create_scrape_log,
    run_scrape_job,
)

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# Server-side TTL cache for /api/filter-options
# ---------------------------------------------------------------------------
class _TTLCache:
    """Thread-safe TTL cache for expensive-but-stable DB queries."""

    def __init__(self, ttl: float):
        self._data = None
        self._expires_at = 0.0
        self._lock = threading.Lock()
        self._ttl = ttl

    def get_or_set(self, factory):
        now = time.time()
        with self._lock:
            if self._data is not None and now < self._expires_at:
                return self._data
            self._data = factory()
            self._expires_at = now + self._ttl
            return self._data

    def clear(self):
        with self._lock:
            self._data = None
            self._expires_at = 0.0


_filter_options_cache = _TTLCache(600)   # 10 minutes
_fields_cache = _TTLCache(3600)          # 1 hour
_jel_codes_cache = _TTLCache(3600)       # 1 hour


# ---------------------------------------------------------------------------
# Pydantic models — error envelope
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Pydantic response models — OpenAPI docs
# ---------------------------------------------------------------------------

class AuthorResponse(BaseModel):
    id: int
    first_name: str
    last_name: str

class CoAuthorResponse(BaseModel):
    display_name: str
    openalex_author_id: str | None = None

class PaperLinkResponse(BaseModel):
    url: str
    link_type: str | None

class PublicationResponse(BaseModel):
    id: int
    title: str
    authors: list[AuthorResponse]
    year: str | None
    venue: str | None
    source_url: str | None
    discovered_at: str | None
    status: str | None
    draft_url: str | None
    draft_available: bool
    abstract: str | None
    draft_url_status: str | None
    doi: str | None = None
    coauthors: list[CoAuthorResponse] = []
    event_id: int | None = None
    event_type: str | None = None
    old_status: str | None = None
    new_status: str | None = None
    event_date: str | None = None
    links: list[PaperLinkResponse] = []

class PaginatedPublications(BaseModel):
    items: list[PublicationResponse]
    total: int
    page: int
    per_page: int
    pages: int

class ResearcherUrlResponse(BaseModel):
    id: int
    page_type: str
    url: str

class ResearchFieldResponse(BaseModel):
    id: int
    name: str
    slug: str

class JelCodeResponse(BaseModel):
    code: str
    name: str

class ResearcherResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    position: str | None
    affiliation: str | None
    description: str | None
    urls: list[ResearcherUrlResponse]
    website_url: str | None
    publication_count: int
    fields: list[ResearchFieldResponse]
    jel_codes: list[JelCodeResponse]

class PaginatedResearchers(BaseModel):
    items: list[ResearcherResponse]
    total: int
    page: int
    per_page: int
    pages: int

class HealthResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_SCRAPE_API_KEY = os.environ.get("SCRAPE_API_KEY", "")


def _require_api_key(request: Request) -> None:
    """Constant-time API key check to prevent timing oracle attacks."""
    api_key = request.headers.get("X-API-Key", "")
    if not api_key or not hmac.compare_digest(api_key, _SCRAPE_API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


VALID_EVENT_TYPES = frozenset({"new_paper", "status_change"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(_SCRAPE_API_KEY) < 16:
        raise RuntimeError(
            "SCRAPE_API_KEY env var is missing or too short (min 16 chars). "
            "Set a strong key before starting the API."
        )

    max_attempts = 10
    for attempt in range(1, max_attempts + 1):
        try:
            Database.create_tables()
            break
        except Exception as e:
            if attempt == max_attempts:
                raise
            wait = 2 ** (attempt - 1)
            logging.warning(
                f"create_tables failed (attempt {attempt}/{max_attempts}), retrying in {wait}s: {e}"
            )
            await asyncio.sleep(wait)
    start_scheduler()
    yield
    shutdown_scheduler()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="econ-newsfeed API",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": {"code": "rate_limit_exceeded", "message": str(exc.detail)}},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# CORS — only allow the frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# ---------------------------------------------------------------------------
# Exception handlers — standard error envelope
# ---------------------------------------------------------------------------

@app.exception_handler(400)
async def bad_request_handler(request: Request, exc):
    msg = exc.detail if isinstance(exc, HTTPException) else "Bad request"
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "bad_request", "message": str(msg)}},
    )


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    msg = exc.detail if isinstance(exc, HTTPException) else "Unauthorized"
    return JSONResponse(
        status_code=401,
        content={"error": {"code": "unauthorized", "message": str(msg)}},
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    msg = exc.detail if isinstance(exc, HTTPException) else "Not found"
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": str(msg)}},
    )


@app.exception_handler(409)
async def conflict_handler(request: Request, exc):
    msg = exc.detail if isinstance(exc, HTTPException) else "Conflict"
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "scrape_in_progress", "message": str(msg)}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    messages = "; ".join(
        f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "bad_request", "message": messages}},
    )


@app.exception_handler(422)
async def unprocessable_handler(request: Request, exc):
    msg = exc.detail if isinstance(exc, HTTPException) else "Unprocessable entity"
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": str(msg)}},
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch-all handler to prevent stack traces leaking in API responses."""
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_z(dt: datetime | str | None) -> str | None:
    """Format a datetime as ISO 8601 with trailing Z, or None."""
    if not dt:
        return None
    if isinstance(dt, str):
        return dt if dt.endswith("Z") else dt + "Z"
    return dt.isoformat() + "Z"



def _format_publication(row: dict, authors: list[dict], coauthors: list[dict] | None = None, links: list[dict] | None = None) -> dict:
    """Format a publication DB row + authors into the API response shape."""
    return {
        "id": row['id'],
        "title": row['title'],
        "authors": authors,
        "year": row['year'],
        "venue": row['venue'],
        "source_url": row['source_url'],
        "discovered_at": _iso_z(row.get('discovered_at')),
        "status": row.get('status'),
        "draft_url": row.get('draft_url'),
        "draft_available": row.get('draft_url_status') == 'valid',
        "abstract": row.get('abstract'),
        "draft_url_status": row.get('draft_url_status'),
        "doi": row.get('doi'),
        "coauthors": coauthors or [],
        "links": links or [],
    }


def _format_feed_event(row: dict, authors: list[dict], coauthors: list[dict] | None = None, links: list[dict] | None = None) -> dict:
    """Format a feed_events + papers joined row into the API response shape."""
    # Remap column names to match _format_publication expectations
    pub_row = {**row, "id": row["paper_id"]}
    result = _format_publication(pub_row, authors, coauthors, links)
    result.update({
        "id": row['paper_id'],
        "event_id": row['event_id'],
        "event_type": row['event_type'],
        "old_status": row.get('old_status'),
        "new_status": row.get('new_status'),
        "old_title": row.get('old_title'),
        "new_title": row.get('new_title'),
        "event_date": _iso_z(row.get('created_at')),
    })
    return result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health", response_model=HealthResponse)
def health_check():
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "ok"}


@app.get("/api/metrics")
def metrics(request: Request, response: Response):
    """Basic application metrics for monitoring."""
    _require_api_key(request)
    row = Database.fetch_one(
        "SELECT "
        "(SELECT COUNT(*) FROM papers) AS publications, "
        "(SELECT COUNT(*) FROM researchers) AS researchers, "
        "(SELECT COUNT(*) FROM scrape_log) AS scrapes"
    )
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "publications": row['publications'] if row else 0,
        "researchers": row['researchers'] if row else 0,
        "scrapes": row['scrapes'] if row else 0,
    }


@app.get("/api/admin/dashboard")
def admin_dashboard(request: Request):
    """Admin dashboard metrics — all stats in one response."""
    _require_api_key(request)
    return Database.get_admin_dashboard_stats()


# ---------------------------------------------------------------------------
# Publication endpoints
# ---------------------------------------------------------------------------

@app.get("/api/publications", response_model=PaginatedPublications)
@limiter.limit("60/minute")
def list_publications(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    year: str | None = Query(None),
    researcher_id: int | None = Query(None),
    status: str | None = Query(None),
    since: str | None = Query(None),
    institution: str | None = Query(None),
    preset: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    event_type: str | None = Query(None),
    jel_code: str | None = Query(None),
):
    """List feed events (new papers and status changes).

    Queries the feed_events table joined to papers. Only non-seed,
    non-published papers with known status generate events, so no
    include_seed parameter is needed.
    """
    valid_statuses = VALID_STATUSES
    valid_presets = {"top20"}

    # Parse comma-separated multi-values
    status_list = [s.strip() for s in status.split(",") if s.strip()] if status else []
    institution_list = [i.strip() for i in institution.split(",") if i.strip()] if institution else []

    for s in status_list:
        if s not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status value '{s}'. Must be one of: {', '.join(sorted(valid_statuses))}")
    if preset and preset not in valid_presets:
        raise HTTPException(status_code=400, detail=f"Invalid preset value. Must be one of: {', '.join(sorted(valid_presets))}")
    if event_type and event_type not in VALID_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid event_type value '{event_type}'. Must be one of: {', '.join(sorted(VALID_EVENT_TYPES))}")
    if since:
        try:
            datetime.fromisoformat(since.rstrip("Z"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ?since= value; expected ISO8601 timestamp")

    offset = (page - 1) * per_page
    with connection_scope():
        rows, total = Database.search_feed_events(
            year=year, researcher_id=researcher_id,
            status_list=status_list or None,
            since=since, institution_list=institution_list or None,
            preset=preset, search=search, event_type=event_type,
            jel_code=jel_code, offset=offset, limit=per_page,
        )
        pages = math.ceil(total / per_page) if total else 0

        pub_ids = [row['paper_id'] for row in rows]
        authors_by_pub = Database.get_authors_for_papers(pub_ids)
        coauthors_by_pub = Database.get_coauthors_for_papers(pub_ids)
        links_by_pub = Database.get_links_for_papers(pub_ids)
    items = [
        _format_feed_event(row, authors_by_pub.get(row['paper_id'], []),
                           coauthors_by_pub.get(row['paper_id'], []),
                           links_by_pub.get(row['paper_id'], []))
        for row in rows
    ]

    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@app.get("/api/publications/{publication_id}")
@limiter.limit("60/minute")
def get_publication(
    request: Request,
    publication_id: int,
    include_history: bool = Query(False),
):
    row = Database.get_paper_detail(publication_id)
    if not row:
        raise HTTPException(status_code=404, detail="Publication not found")

    authors_map = Database.get_authors_for_papers([publication_id])
    coauthors_map = Database.get_coauthors_for_papers([publication_id])
    links_map = Database.get_links_for_papers([publication_id])
    result = _format_publication(
        row, authors_map.get(publication_id, []),
        coauthors_map.get(publication_id, []),
        links_map.get(publication_id, []),
    )

    if include_history:
        snapshots = Database.get_paper_snapshots(publication_id)
        result["history"] = [
            {
                "status": s['status'],
                "venue": s['venue'],
                "abstract": s['abstract'],
                "draft_url": s['draft_url'],
                "draft_url_status": s['draft_url_status'],
                "year": s['year'],
                "scraped_at": _iso_z(s['scraped_at']),
                "source_url": s['source_url'],
            }
            for s in snapshots
        ]

        feed_events = Database.get_paper_history(publication_id)
        result["feed_events"] = [
            {
                "id": fe['id'],
                "event_type": fe['event_type'],
                "old_status": fe['old_status'],
                "new_status": fe['new_status'],
                "created_at": _iso_z(fe['created_at']),
            }
            for fe in feed_events
        ]

        result["is_seed"] = bool(row.get('is_seed'))
        result["title_hash"] = row.get('title_hash')
        result["openalex_id"] = row.get('openalex_id')

    return result


# ---------------------------------------------------------------------------
# Researcher helpers
# ---------------------------------------------------------------------------


def _get_website_url(urls: list[dict]) -> str | None:
    """Return the homepage URL from a researcher's URL list, or None."""
    for u in urls:
        if u["page_type"].upper() in ("HOME", "HOMEPAGE"):
            return u["url"]
    # Fallback: return first URL if no homepage found
    return urls[0]["url"] if urls else None



# ---------------------------------------------------------------------------
# Researcher endpoints
# ---------------------------------------------------------------------------

@app.get("/api/fields")
@limiter.limit("60/minute")
def list_fields(request: Request, response: Response):
    def _fetch():
        rows = Database.fetch_all("SELECT id, name, slug FROM research_fields ORDER BY name")
        return {"items": [{"id": r['id'], "name": r['name'], "slug": r['slug']} for r in rows]}

    response.headers["Cache-Control"] = "public, max-age=3600"
    return _fields_cache.get_or_set(_fetch)


@app.get("/api/jel-codes")
@limiter.limit("60/minute")
def list_jel_codes(request: Request, response: Response):
    def _fetch():
        return {"items": Database.get_all_jel_codes()}

    response.headers["Cache-Control"] = "public, max-age=3600"
    return _jel_codes_cache.get_or_set(_fetch)


@app.get("/api/filter-options")
@limiter.limit("30/minute")
def get_filter_options(request: Request, response: Response):
    def _fetch():
        institutions = Database.fetch_all(
            "SELECT DISTINCT affiliation FROM researchers "
            "WHERE affiliation IS NOT NULL AND affiliation != '' "
            "ORDER BY affiliation"
        )
        positions = Database.fetch_all(
            "SELECT DISTINCT position FROM researchers "
            "WHERE position IS NOT NULL AND position != '' "
            "ORDER BY position"
        )
        fields = Database.fetch_all(
            "SELECT id, name, slug FROM research_fields ORDER BY name"
        )
        return {
            "institutions": [r['affiliation'] for r in institutions],
            "positions": [r['position'] for r in positions],
            "fields": [{"id": r['id'], "name": r['name'], "slug": r['slug']} for r in fields],
        }

    response.headers["Cache-Control"] = "public, max-age=600"
    return _filter_options_cache.get_or_set(_fetch)


@app.get("/api/researchers", response_model=PaginatedResearchers)
@limiter.limit("60/minute")
def list_researchers(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    institution: str | None = Query(None),
    field: str | None = Query(None),
    position: str | None = Query(None),
    preset: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
):
    offset = (page - 1) * per_page
    with connection_scope():
        rows, total = Database.search_researchers(
            search=search, institution=institution, field_slug=field,
            position=position, preset=preset,
            offset=offset, limit=per_page,
        )
        pages = math.ceil(total / per_page) if total else 0

        researcher_ids = [r['id'] for r in rows]
        urls_by_researcher = Database.get_urls_for_researchers(researcher_ids)
        pub_counts = Database.get_pub_counts_for_researchers(researcher_ids)
        fields_by_researcher = Database.get_fields_for_researchers(researcher_ids)
        jel_map = Database.get_jel_codes_for_researchers(researcher_ids)
    items = [
        {
            "id": r['id'],
            "first_name": r['first_name'],
            "last_name": r['last_name'],
            "position": r['position'],
            "affiliation": r['affiliation'],
            "description": r['description'],
            "urls": urls_by_researcher.get(r['id'], []),
            "website_url": _get_website_url(urls_by_researcher.get(r['id'], [])),
            "publication_count": pub_counts.get(r['id'], 0),
            "fields": fields_by_researcher.get(r['id'], []),
            "jel_codes": jel_map.get(r['id'], []),
        }
        for r in rows
    ]
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@app.get("/api/researchers/{researcher_id}")
@limiter.limit("60/minute")
def get_researcher(
    request: Request,
    researcher_id: int,
    include_history: bool = Query(False),
):
    row = Database.get_researcher_detail(researcher_id)
    if not row:
        raise HTTPException(status_code=404, detail="Researcher not found")

    urls_map = Database.get_urls_for_researchers([researcher_id])
    urls = urls_map.get(researcher_id, [])
    pub_counts = Database.get_pub_counts_for_researchers([researcher_id])
    pub_count = pub_counts.get(researcher_id, 0)
    fields_map = Database.get_fields_for_researchers([researcher_id])
    fields = fields_map.get(researcher_id, [])
    jel_codes = Database.get_jel_codes_for_researcher(researcher_id)

    # Fetch this researcher's publications
    pub_rows = Database.get_researcher_papers(researcher_id)
    pub_ids = [pr['id'] for pr in pub_rows]
    authors_by_pub = Database.get_authors_for_papers(pub_ids)
    coauthors_by_pub = Database.get_coauthors_for_papers(pub_ids)
    links_by_pub = Database.get_links_for_papers(pub_ids)
    publications = [
        _format_publication(pr, authors_by_pub.get(pr['id'], []),
                           coauthors_by_pub.get(pr['id'], []),
                           links_by_pub.get(pr['id'], []))
        for pr in pub_rows
    ]

    result = {
        "id": row['id'],
        "first_name": row['first_name'],
        "last_name": row['last_name'],
        "position": row['position'],
        "affiliation": row['affiliation'],
        "description": row['description'],
        "urls": urls,
        "website_url": _get_website_url(urls),
        "publication_count": pub_count,
        "fields": fields,
        "jel_codes": jel_codes,
        "publications": publications,
    }

    if include_history:
        snapshots = Database.get_researcher_snapshots(researcher_id)
        result["history"] = [
            {
                "position": s['position'],
                "affiliation": s['affiliation'],
                "description": s['description'],
                "scraped_at": _iso_z(s['scraped_at']),
                "source_url": s['source_url'],
            }
            for s in snapshots
        ]

    return result


# ---------------------------------------------------------------------------
# Scrape endpoints
# ---------------------------------------------------------------------------

@app.post("/api/scrape", status_code=201)
async def trigger_scrape(request: Request):
    _require_api_key(request)

    # Check if a scrape is already running (DB advisory lock, works across workers)
    if scheduler.is_scrape_running():
        raise HTTPException(
            status_code=409,
            detail="A scrape is already running. Wait for it to complete.",
        )

    log_id = create_scrape_log()
    started_at = datetime.now(timezone.utc)

    t = threading.Thread(
        target=run_scrape_job, daemon=False, name="manual-scrape"
    )
    t.start()

    return {
        "scrape_id": log_id,
        "status": "running",
        "started_at": _iso_z(started_at),
    }


@app.get("/api/scrape/status")
@limiter.limit("60/minute")
def scrape_status(request: Request):
    _require_api_key(request)
    row = Database.fetch_one(
        """
        SELECT id, status, started_at, finished_at,
               urls_checked, urls_changed, pubs_extracted
        FROM scrape_log
        ORDER BY id DESC
        LIMIT 1
        """
    )

    interval = scheduler.SCRAPE_INTERVAL_HOURS

    if not row:
        return {"last_scrape": None, "next_scrape_at": None, "interval_hours": interval}

    started_at = row['started_at']
    next_scrape = _iso_z(started_at + timedelta(hours=interval)) if started_at else None

    return {
        "last_scrape": {
            "id": row['id'],
            "status": row['status'],
            "started_at": _iso_z(row['started_at']),
            "finished_at": _iso_z(row['finished_at']),
            "urls_checked": row['urls_checked'],
            "urls_changed": row['urls_changed'],
            "pubs_extracted": row['pubs_extracted'],
        },
        "next_scrape_at": next_scrape,
        "interval_hours": interval,
    }
