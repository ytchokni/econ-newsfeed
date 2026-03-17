"""FastAPI REST API for econ-newsfeed."""
import math
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from database import Database
import scheduler
from scheduler import (
    start_scheduler,
    shutdown_scheduler,
    create_scrape_log,
    run_scrape_job,
)

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")


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

@asynccontextmanager
async def lifespan(app: FastAPI):
    Database.create_tables()
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

# CORS — only allow the frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["*"],
    allow_headers=["*"],
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
    return response


# ---------------------------------------------------------------------------
# Exception handlers — standard error envelope
# ---------------------------------------------------------------------------

@app.exception_handler(400)
async def bad_request_handler(request: Request, exc):
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "bad_request", "message": str(exc.detail) if hasattr(exc, "detail") else str(exc)}},
    )


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    return JSONResponse(
        status_code=401,
        content={"error": {"code": "unauthorized", "message": str(exc.detail) if hasattr(exc, "detail") else str(exc)}},
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": str(exc.detail) if hasattr(exc, "detail") else str(exc)}},
    )


@app.exception_handler(409)
async def conflict_handler(request: Request, exc):
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "scrape_in_progress", "message": str(exc.detail) if hasattr(exc, "detail") else str(exc)}},
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
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": str(exc.detail) if hasattr(exc, "detail") else str(exc)}},
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_authors_for_publication(publication_id: int) -> list[dict]:
    """Fetch authors for a publication via the authorship table."""
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


def _format_publication(row, authors: list[dict]) -> dict:
    """Format a publication DB row + authors into the API response shape."""
    return {
        "id": row[0],
        "title": row[1],
        "authors": authors,
        "year": row[2],
        "venue": row[3],
        "source_url": row[4],
        "discovered_at": row[5].isoformat() + "Z" if row[5] else None,
    }


# ---------------------------------------------------------------------------
# Publication endpoints
# ---------------------------------------------------------------------------

@app.get("/api/publications")
async def list_publications(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    year: str | None = Query(None),
    researcher_id: int | None = Query(None),
):
    # Build WHERE clause
    conditions = []
    params: list = []
    if year:
        conditions.append("p.year = %s")
        params.append(year)
    if researcher_id:
        conditions.append("p.id IN (SELECT publication_id FROM authorship WHERE researcher_id = %s)")
        params.append(researcher_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Total count
    count_row = Database.fetch_one(
        f"SELECT COUNT(*) FROM publications p {where}", params or None
    )
    total = count_row[0] if count_row else 0
    pages = math.ceil(total / per_page) if total else 0

    # Paginated results
    offset = (page - 1) * per_page
    rows = Database.fetch_all(
        f"""
        SELECT p.id, p.title, p.year, p.venue, p.url, p.timestamp
        FROM publications p
        {where}
        ORDER BY p.timestamp DESC
        LIMIT %s OFFSET %s
        """,
        (*params, per_page, offset),
    )

    items = []
    for row in rows:
        authors = _get_authors_for_publication(row[0])
        items.append(_format_publication(row, authors))

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@app.get("/api/publications/{publication_id}")
async def get_publication(publication_id: int):
    row = Database.fetch_one(
        "SELECT id, title, year, venue, url, timestamp FROM publications WHERE id = %s",
        (publication_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Publication not found")

    authors = _get_authors_for_publication(publication_id)
    return _format_publication(row, authors)


# ---------------------------------------------------------------------------
# Researcher helpers
# ---------------------------------------------------------------------------

def _get_urls_for_researcher(researcher_id: int) -> list[dict]:
    rows = Database.fetch_all(
        "SELECT id, page_type, url FROM researcher_urls WHERE researcher_id = %s",
        (researcher_id,),
    )
    return [{"id": r[0], "page_type": r[1], "url": r[2]} for r in rows]


def _get_pub_count_for_researcher(researcher_id: int) -> int:
    row = Database.fetch_one(
        "SELECT COUNT(*) FROM authorship WHERE researcher_id = %s",
        (researcher_id,),
    )
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Researcher endpoints
# ---------------------------------------------------------------------------

@app.get("/api/researchers")
async def list_researchers():
    rows = Database.fetch_all(
        "SELECT id, first_name, last_name, position, affiliation FROM researchers"
    )
    items = []
    for r in rows:
        urls = _get_urls_for_researcher(r[0])
        pub_count = _get_pub_count_for_researcher(r[0])
        items.append({
            "id": r[0],
            "first_name": r[1],
            "last_name": r[2],
            "position": r[3],
            "affiliation": r[4],
            "urls": urls,
            "publication_count": pub_count,
        })
    return {"items": items}


@app.get("/api/researchers/{researcher_id}")
async def get_researcher(researcher_id: int):
    row = Database.fetch_one(
        "SELECT id, first_name, last_name, position, affiliation FROM researchers WHERE id = %s",
        (researcher_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Researcher not found")

    urls = _get_urls_for_researcher(researcher_id)
    pub_count = _get_pub_count_for_researcher(researcher_id)

    # Fetch this researcher's publications
    pub_rows = Database.fetch_all(
        """
        SELECT p.id, p.title, p.year, p.venue, p.url, p.timestamp
        FROM publications p
        JOIN authorship a ON a.publication_id = p.id
        WHERE a.researcher_id = %s
        ORDER BY p.timestamp DESC
        """,
        (researcher_id,),
    )
    publications = []
    for pr in pub_rows:
        authors = _get_authors_for_publication(pr[0])
        publications.append(_format_publication(pr, authors))

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "position": row[3],
        "affiliation": row[4],
        "urls": urls,
        "publication_count": pub_count,
        "publications": publications,
    }


# ---------------------------------------------------------------------------
# Scrape endpoints
# ---------------------------------------------------------------------------

@app.post("/api/scrape", status_code=201)
async def trigger_scrape(request: Request):
    # Authenticate
    api_key = request.headers.get("X-API-Key")
    scrape_api_key = os.environ.get("SCRAPE_API_KEY")
    if not api_key or api_key != scrape_api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")

    # Check if a scrape is already running
    if not scheduler._scrape_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="A scrape is already running. Wait for it to complete.",
        )
    # Release immediately — run_scrape_job will re-acquire
    scheduler._scrape_lock.release()

    log_id = create_scrape_log()
    started_at = datetime.now(timezone.utc)

    # Run scrape in background thread
    t = threading.Thread(target=run_scrape_job, daemon=True)
    t.start()

    return {
        "scrape_id": log_id,
        "status": "running",
        "started_at": started_at.isoformat() + "Z",
    }


@app.get("/api/scrape/status")
async def scrape_status():
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
