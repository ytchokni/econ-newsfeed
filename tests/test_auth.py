"""Tests for auth.py JWT verification dependencies."""
import os
from unittest.mock import patch, MagicMock

import pytest
from jose import jwt

NEXTAUTH_SECRET = os.environ.get("NEXTAUTH_SECRET", "test-nextauth-secret-for-ci")

from auth import _extract_token, _decode_jwt, get_current_user, get_optional_user


def _make_token(payload: dict) -> str:
    return jwt.encode(payload, NEXTAUTH_SECRET, algorithm="HS256")


class FakeRequest:
    def __init__(self, token: str | None = None):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self.headers = headers


def test_extract_token_present():
    req = FakeRequest("abc123")
    assert _extract_token(req) == "abc123"


def test_extract_token_missing():
    req = FakeRequest()
    assert _extract_token(req) is None


def test_decode_jwt_valid():
    token = _make_token({"sub": "google123", "email": "a@b.com"})
    payload = _decode_jwt(token)
    assert payload["sub"] == "google123"


def test_decode_jwt_invalid():
    from jose import JWTError
    with pytest.raises(JWTError):
        _decode_jwt("not.a.valid.token")


@pytest.mark.asyncio
async def test_get_current_user_no_token():
    from fastapi import HTTPException
    req = FakeRequest()
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_valid():
    token = _make_token({"sub": "g123", "email": "test@example.com", "name": "Test"})
    req = FakeRequest(token)
    fake_user = {"id": 1, "google_id": "g123", "email": "test@example.com", "name": "Test"}

    with patch("auth.Database") as mock_db, \
         patch("auth.connection_scope", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())):
        mock_db.get_or_create_user.return_value = fake_user
        user = await get_current_user(req)
        assert user["id"] == 1
        mock_db.get_or_create_user.assert_called_once()


@pytest.mark.asyncio
async def test_get_optional_user_no_token():
    req = FakeRequest()
    user = await get_optional_user(req)
    assert user is None


@pytest.mark.asyncio
async def test_get_optional_user_invalid_token():
    req = FakeRequest("bad.token")
    user = await get_optional_user(req)
    assert user is None
