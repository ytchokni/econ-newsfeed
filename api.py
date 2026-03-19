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

from database import Database
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
# Pydantic models — error envelope
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_SCRAPE_API_KEY = os.environ.get("SCRAPE_API_KEY", "")


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

def _escape_like(value: str) -> str:
    """Escape LIKE-special characters so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _get_authors_for_publication(publication_id: int) -> list[dict]:
    """Fetch authors for a single publication via the authorship table."""
    rows = Database.fetch_all(
        """
        SELECT r.id, r.first_name, r.last_name
        FROM authorship a
        JOIN researchers r ON r.id = a.researcher_id
        WHERE a.publication_id = %s
        ORDER BY a.author_order
        """,
        (publication_id,),
    )
    return [{"id": r[0], "first_name": r[1], "last_name": r[2]} for r in rows]


def _get_authors_for_publications(pub_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch authors for multiple publications. Returns {pub_id: [author, ...]}."""
    if not pub_ids:
        return {}
    placeholders = ",".join(["%s"] * len(pub_ids))
    rows = Database.fetch_all(
        f"""
        SELECT a.publication_id, r.id, r.first_name, r.last_name
        FROM authorship a
        JOIN researchers r ON r.id = a.researcher_id
        WHERE a.publication_id IN ({placeholders})
        ORDER BY a.publication_id, a.author_order
        """,
        tuple(pub_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in pub_ids}
    for row in rows:
        result[row[0]].append({"id": row[1], "first_name": row[2], "last_name": row[3]})
    return result


def _format_publication(row, authors: list[dict]) -> dict:
    """Format a publication DB row + authors into the API response shape.

    Expected row columns: id, title, year, venue, source_url, timestamp, status, draft_url, abstract, draft_url_status
    """
    draft_url = row[7] if len(row) > 7 else None
    abstract = row[8] if len(row) > 8 else None
    draft_url_status = row[9] if len(row) > 9 else None
    return {
        "id": row[0],
        "title": row[1],
        "authors": authors,
        "year": row[2],
        "venue": row[3],
        "source_url": row[4],
        "discovered_at": row[5].isoformat() + "Z" if row[5] else None,
        "status": row[6] if len(row) > 6 else None,
        "draft_url": draft_url,
        "draft_available": draft_url_status == 'valid',
        "abstract": abstract,
        "draft_url_status": draft_url_status,
    }


def _format_feed_event(row, authors: list[dict]) -> dict:
    """Format a feed_events + papers joined row into the API response shape.

    Expected row columns (positional):
        0: event_id, 1: event_type, 2: event_old_status, 3: event_new_status, 4: event_date,
        5: paper_id, 6: title, 7: year, 8: venue, 9: source_url, 10: timestamp,
        11: status, 12: draft_url, 13: abstract, 14: draft_url_status
    """
    return {
        "id": row[5],           # paper_id (used by frontend for detail links / React keys)
        "event_id": row[0],
        "event_type": row[1],
        "old_status": row[2],
        "new_status": row[3],
        "event_date": row[4].isoformat() + "Z" if row[4] else None,
        "title": row[6],
        "authors": authors,
        "year": row[7],
        "venue": row[8],
        "source_url": row[9],
        "discovered_at": row[10].isoformat() + "Z" if row[10] else None,
        "status": row[11],
        "draft_url": row[12],
        "draft_available": row[14] == 'valid' if row[14] else False,
        "abstract": row[13],
        "draft_url_status": row[14],
    }


# ---------------------------------------------------------------------------
# Publication endpoints
# ---------------------------------------------------------------------------

@app.get("/api/publications")
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
):
    """List feed events (new papers and status changes).

    Queries the feed_events table joined to papers. Only non-seed,
    non-published papers with known status generate events, so no
    include_seed parameter is needed.
    """
    valid_statuses = {"published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper"}
    valid_presets = {"top20"}

    # Parse comma-separated multi-values
    status_list = [s.strip() for s in status.split(",") if s.strip()] if status else []
    institution_list = [i.strip() for i in institution.split(",") if i.strip()] if institution else []

    for s in status_list:
        if s not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status value '{s}'. Must be one of: {', '.join(sorted(valid_statuses))}")
    if preset and preset not in valid_presets:
        raise HTTPException(status_code=400, detail=f"Invalid preset value. Must be one of: {', '.join(sorted(valid_presets))}")

    # Build WHERE clause
    conditions = []
    params: list = []
    if year:
        conditions.append("p.year = %s")
        params.append(year)
    if researcher_id:
        conditions.append("p.id IN (SELECT publication_id FROM authorship WHERE researcher_id = %s)")
        params.append(researcher_id)
    if status_list:
        if len(status_list) == 1:
            conditions.append("p.status = %s")
            params.append(status_list[0])
        else:
            placeholders = ",".join(["%s"] * len(status_list))
            conditions.append(f"p.status IN ({placeholders})")
            params.extend(status_list)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.rstrip("Z"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ?since= value; expected ISO8601 timestamp")
        # Filter on event time, not paper discovery time
        conditions.append("fe.created_at >= %s")
        params.append(since_dt)
    if institution_list and not preset:
        if len(institution_list) == 1:
            conditions.append(
                "p.id IN (SELECT a.publication_id FROM authorship a "
                "JOIN researchers r ON r.id = a.researcher_id "
                "WHERE r.affiliation LIKE %s)"
            )
            params.append(f"%{_escape_like(institution_list[0])}%")
        else:
            inst_likes = " OR ".join(["r.affiliation LIKE %s"] * len(institution_list))
            conditions.append(
                f"p.id IN (SELECT a.publication_id FROM authorship a "
                f"JOIN researchers r ON r.id = a.researcher_id "
                f"WHERE {inst_likes})"
            )
            params.extend(f"%{_escape_like(i)}%" for i in institution_list)
    if preset == "top20":
        dept_likes = " OR ".join(["r.affiliation LIKE %s"] * len(_TOP20_DEPT_KEYWORDS))
        conditions.append(
            f"p.id IN (SELECT a.publication_id FROM authorship a "
            f"JOIN researchers r ON r.id = a.researcher_id "
            f"WHERE {dept_likes})"
        )
        params.extend(f"%{_escape_like(kw)}%" for kw in _TOP20_DEPT_KEYWORDS)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Total count
    count_row = Database.fetch_one(
        f"""SELECT COUNT(*)
            FROM feed_events fe
            JOIN papers p ON p.id = fe.paper_id
            {where}""",
        params or None,
    )
    total = count_row[0] if count_row else 0
    pages = math.ceil(total / per_page) if total else 0

    # Paginated results
    offset = (page - 1) * per_page
    rows = Database.fetch_all(
        f"""
        SELECT fe.id, fe.event_type, fe.old_status, fe.new_status, fe.created_at,
               p.id, p.title, p.year, p.venue, p.source_url, p.timestamp,
               p.status, p.draft_url, p.abstract, p.draft_url_status
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        {where}
        ORDER BY fe.created_at DESC
        LIMIT %s OFFSET %s
        """,
        (*params, per_page, offset),
    )

    # row[5] is paper_id
    pub_ids = [row[5] for row in rows]
    authors_by_pub = _get_authors_for_publications(pub_ids)
    items = [_format_feed_event(row, authors_by_pub.get(row[5], [])) for row in rows]

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
    row = Database.fetch_one(
        "SELECT id, title, year, venue, source_url, timestamp, status, draft_url, abstract, draft_url_status FROM papers WHERE id = %s",
        (publication_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Publication not found")

    authors = _get_authors_for_publication(publication_id)
    result = _format_publication(row, authors)

    if include_history:
        snapshots = Database.get_paper_snapshots(publication_id)
        result["history"] = [
            {
                "status": s[0],
                "venue": s[1],
                "abstract": s[2],
                "draft_url": s[3],
                "draft_url_status": s[4],
                "year": s[5],
                "scraped_at": s[6].isoformat() + "Z" if s[6] else None,
                "source_url": s[7],
            }
            for s in snapshots
        ]

    return result


# ---------------------------------------------------------------------------
# Researcher helpers
# ---------------------------------------------------------------------------

def _get_urls_for_researcher(researcher_id: int) -> list[dict]:
    rows = Database.fetch_all(
        "SELECT id, page_type, url FROM researcher_urls WHERE researcher_id = %s",
        (researcher_id,),
    )
    return [{"id": r[0], "page_type": r[1], "url": r[2]} for r in rows]


def _get_website_url(urls: list[dict]) -> str | None:
    """Return the homepage URL from a researcher's URL list, or None."""
    for u in urls:
        if u["page_type"].upper() in ("HOME", "HOMEPAGE"):
            return u["url"]
    # Fallback: return first URL if no homepage found
    return urls[0]["url"] if urls else None


def _get_pub_count_for_researcher(researcher_id: int) -> int:
    row = Database.fetch_one(
        "SELECT COUNT(*) FROM authorship WHERE researcher_id = %s",
        (researcher_id,),
    )
    return row[0] if row else 0


def _get_fields_for_researcher(researcher_id: int) -> list[dict]:
    rows = Database.fetch_all(
        """
        SELECT rf.id, rf.name, rf.slug
        FROM researcher_fields rf_link
        JOIN research_fields rf ON rf.id = rf_link.field_id
        WHERE rf_link.researcher_id = %s
        ORDER BY rf.name
        """,
        (researcher_id,),
    )
    return [{"id": r[0], "name": r[1], "slug": r[2]} for r in rows]


def _get_urls_for_researchers(researcher_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch URLs for multiple researchers. Returns {researcher_id: [url, ...]}."""
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = Database.fetch_all(
        f"SELECT researcher_id, id, page_type, url FROM researcher_urls WHERE researcher_id IN ({placeholders})",
        tuple(researcher_ids),
    )
    result: dict[int, list[dict]] = {rid: [] for rid in researcher_ids}
    for row in rows:
        result[row[0]].append({"id": row[1], "page_type": row[2], "url": row[3]})
    return result


def _get_pub_counts_for_researchers(researcher_ids: list[int]) -> dict[int, int]:
    """Batch-fetch publication counts for multiple researchers. Returns {researcher_id: count}."""
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = Database.fetch_all(
        f"SELECT researcher_id, COUNT(*) FROM authorship WHERE researcher_id IN ({placeholders}) GROUP BY researcher_id",
        tuple(researcher_ids),
    )
    result: dict[int, int] = {rid: 0 for rid in researcher_ids}
    for row in rows:
        result[row[0]] = row[1]
    return result


def _get_fields_for_researchers(researcher_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch fields for multiple researchers. Returns {researcher_id: [field, ...]}."""
    if not researcher_ids:
        return {}
    placeholders = ",".join(["%s"] * len(researcher_ids))
    rows = Database.fetch_all(
        f"""SELECT rf_link.researcher_id, rf.id, rf.name, rf.slug
            FROM researcher_fields rf_link
            JOIN research_fields rf ON rf.id = rf_link.field_id
            WHERE rf_link.researcher_id IN ({placeholders})
            ORDER BY rf.name""",
        tuple(researcher_ids),
    )
    result: dict[int, list[dict]] = {rid: [] for rid in researcher_ids}
    for row in rows:
        result[row[0]].append({"id": row[1], "name": row[2], "slug": row[3]})
    return result


# Top-20 economics department keywords for preset filtering
_TOP20_DEPT_KEYWORDS = [
    "MIT", "Massachusetts Institute of Technology",
    "Harvard", "Princeton", "Stanford",
    "University of Chicago",
    "UC Berkeley", "University of California, Berkeley",
    "Columbia", "Yale", "Northwestern",
    "University of Pennsylvania",
    "New York University", "NYU",
    "Duke",
    "University of Michigan",
    "University of Minnesota",
    "Cornell",
    "UCLA", "University of California, Los Angeles",
    "UC San Diego", "University of California, San Diego",
    "University of Wisconsin",
    "Boston University",
    "Carnegie Mellon",
]


# ---------------------------------------------------------------------------
# Researcher endpoints
# ---------------------------------------------------------------------------

@app.get("/api/fields")
@limiter.limit("60/minute")
def list_fields(request: Request):
    rows = Database.fetch_all("SELECT id, name, slug FROM research_fields ORDER BY name")
    return {"items": [{"id": r[0], "name": r[1], "slug": r[2]} for r in rows]}


@app.get("/api/filter-options")
@limiter.limit("30/minute")
def get_filter_options(request: Request, response: Response):
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
    response.headers["Cache-Control"] = "public, max-age=600"
    return {
        "institutions": [r[0] for r in institutions],
        "positions": [r[0] for r in positions],
        "fields": [{"id": r[0], "name": r[1], "slug": r[2]} for r in fields],
    }


@app.get("/api/researchers")
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
):
    conditions = []
    params: list = []

    if institution:
        conditions.append("r.affiliation LIKE %s")
        params.append(f"%{_escape_like(institution)}%")
    if position:
        conditions.append("r.position LIKE %s")
        params.append(f"%{_escape_like(position)}%")
    if preset == "top20":
        dept_conditions = " OR ".join(["r.affiliation LIKE %s"] * len(_TOP20_DEPT_KEYWORDS))
        conditions.append(f"({dept_conditions})")
        params.extend(f"%{kw}%" for kw in _TOP20_DEPT_KEYWORDS)
    if field:
        field_slugs = [f.strip() for f in field.split(",") if f.strip()]
        if len(field_slugs) == 1:
            conditions.append(
                "r.id IN (SELECT rf.researcher_id FROM researcher_fields rf "
                "JOIN research_fields f ON f.id = rf.field_id "
                "WHERE f.slug = %s)"
            )
            params.append(field_slugs[0])
        else:
            placeholders = ",".join(["%s"] * len(field_slugs))
            conditions.append(
                f"r.id IN (SELECT rf.researcher_id FROM researcher_fields rf "
                f"JOIN research_fields f ON f.id = rf.field_id "
                f"WHERE f.slug IN ({placeholders}))"
            )
            params.extend(field_slugs)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Total count
    count_row = Database.fetch_one(
        f"SELECT COUNT(*) FROM researchers r {where}", params or None
    )
    total = count_row[0] if count_row else 0
    pages = math.ceil(total / per_page) if total else 0

    offset = (page - 1) * per_page
    rows = Database.fetch_all(
        f"""
        SELECT r.id, r.first_name, r.last_name, r.position, r.affiliation, r.description
        FROM researchers r
        {where}
        ORDER BY r.last_name, r.first_name
        LIMIT %s OFFSET %s
        """,
        (*params, per_page, offset),
    )

    researcher_ids = [r[0] for r in rows]
    urls_by_researcher = _get_urls_for_researchers(researcher_ids)
    pub_counts = _get_pub_counts_for_researchers(researcher_ids)
    fields_by_researcher = _get_fields_for_researchers(researcher_ids)
    items = [
        {
            "id": r[0],
            "first_name": r[1],
            "last_name": r[2],
            "position": r[3],
            "affiliation": r[4],
            "description": r[5],
            "urls": urls_by_researcher.get(r[0], []),
            "website_url": _get_website_url(urls_by_researcher.get(r[0], [])),
            "publication_count": pub_counts.get(r[0], 0),
            "fields": fields_by_researcher.get(r[0], []),
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
    row = Database.fetch_one(
        "SELECT id, first_name, last_name, position, affiliation, description FROM researchers WHERE id = %s",
        (researcher_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Researcher not found")

    urls = _get_urls_for_researcher(researcher_id)
    pub_count = _get_pub_count_for_researcher(researcher_id)
    fields = _get_fields_for_researcher(researcher_id)

    # Fetch this researcher's publications
    pub_rows = Database.fetch_all(
        """
        SELECT p.id, p.title, p.year, p.venue, p.source_url, p.timestamp, p.status, p.draft_url,
               p.abstract, p.draft_url_status
        FROM papers p
        JOIN authorship a ON a.publication_id = p.id
        WHERE a.researcher_id = %s
        ORDER BY p.timestamp DESC
        """,
        (researcher_id,),
    )
    pub_ids = [pr[0] for pr in pub_rows]
    authors_by_pub = _get_authors_for_publications(pub_ids)
    publications = [_format_publication(pr, authors_by_pub.get(pr[0], [])) for pr in pub_rows]

    result = {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "position": row[3],
        "affiliation": row[4],
        "description": row[5],
        "urls": urls,
        "website_url": _get_website_url(urls),
        "publication_count": pub_count,
        "fields": fields,
        "publications": publications,
    }

    if include_history:
        snapshots = Database.get_researcher_snapshots(researcher_id)
        result["history"] = [
            {
                "position": s[0],
                "affiliation": s[1],
                "description": s[2],
                "scraped_at": s[3].isoformat() + "Z" if s[3] else None,
                "source_url": s[4],
            }
            for s in snapshots
        ]

    return result


# ---------------------------------------------------------------------------
# Scrape endpoints
# ---------------------------------------------------------------------------

@app.post("/api/scrape", status_code=201)
async def trigger_scrape(request: Request):
    # Authenticate — use constant-time comparison to prevent timing attacks
    api_key = request.headers.get("X-API-Key", "")
    if not api_key or not hmac.compare_digest(api_key, _SCRAPE_API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")

    # Check if a scrape is already running (DB advisory lock, works across workers)
    if scheduler.is_scrape_running():
        raise HTTPException(
            status_code=409,
            detail="A scrape is already running. Wait for it to complete.",
        )

    log_id = create_scrape_log()
    started_at = datetime.now(timezone.utc)

    t = threading.Thread(
        target=run_scrape_job, daemon=True
    )
    t.start()

    return {
        "scrape_id": log_id,
        "status": "running",
        "started_at": started_at.isoformat() + "Z",
    }


@app.get("/api/scrape/status")
@limiter.limit("60/minute")
def scrape_status(request: Request):
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

    started_at = row[2]
    next_scrape = (started_at + timedelta(hours=interval)).isoformat() + "Z" if started_at else None

    return {
        "last_scrape": {
            "id": row[0],
            "status": row[1],
            "started_at": row[2].isoformat() + "Z" if row[2] else None,
            "finished_at": row[3].isoformat() + "Z" if row[3] else None,
            "urls_checked": row[4],
            "urls_changed": row[5],
            "pubs_extracted": row[6],
        },
        "next_scrape_at": next_scrape,
        "interval_hours": interval,
    }
