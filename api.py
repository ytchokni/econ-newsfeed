"""FastAPI REST API for econ-newsfeed."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from database import Database
from scheduler import start_scheduler, shutdown_scheduler

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
# Placeholder routes (will be implemented in later phases)
# ---------------------------------------------------------------------------

@app.get("/api/publications")
async def list_publications(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    year: str | None = Query(None),
    researcher_id: int | None = Query(None),
):
    return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}


@app.get("/api/publications/{publication_id}")
async def get_publication(publication_id: int):
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Publication not found")


@app.get("/api/researchers")
async def list_researchers():
    return {"items": []}


@app.get("/api/researchers/{researcher_id}")
async def get_researcher(researcher_id: int):
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Researcher not found")


@app.post("/api/scrape", status_code=201)
async def trigger_scrape(request: Request):
    from fastapi import HTTPException
    api_key = request.headers.get("X-API-Key")
    scrape_api_key = os.environ.get("SCRAPE_API_KEY")
    if not api_key or api_key != scrape_api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return {"scrape_id": 0, "status": "running", "started_at": None}


@app.get("/api/scrape/status")
async def scrape_status():
    return {"last_scrape": None, "next_scrape_at": None, "interval_hours": 24}
