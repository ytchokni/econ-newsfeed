"""FastAPI authentication dependencies for NextAuth.js JWT verification."""
import logging
import os

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from backend.database import Database, connection_scope

logger = logging.getLogger(__name__)

NEXTAUTH_SECRET = os.environ.get("NEXTAUTH_SECRET", "")
ALGORITHM = "HS256"


def _extract_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _decode_jwt(token: str) -> dict:
    """Decode and verify a NextAuth.js JWT."""
    return jwt.decode(token, NEXTAUTH_SECRET, algorithms=[ALGORITHM])


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency: require a valid JWT and return the user record.

    On first valid JWT, lazily creates the user row in MySQL.
    Raises 401 if no token or invalid token.
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = _decode_jwt(token)
    except JWTError as e:
        logger.debug("JWT verification failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")

    google_id = payload.get("sub")
    email = payload.get("email", "")
    name = payload.get("name")
    picture = payload.get("picture")

    if not google_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    with connection_scope():
        user = Database.get_or_create_user(
            google_id=google_id, email=email, name=name, picture_url=picture,
        )
    return user


async def get_optional_user(request: Request) -> dict | None:
    """FastAPI dependency: return user if valid JWT present, None otherwise.

    Never raises 401 — used for endpoints that behave differently when
    authenticated but don't require it.
    """
    token = _extract_token(request)
    if not token:
        return None

    try:
        payload = _decode_jwt(token)
    except JWTError:
        return None

    google_id = payload.get("sub")
    if not google_id:
        return None

    with connection_scope():
        user = Database.get_user_by_google_id(google_id)
    return user
