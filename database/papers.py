"""Paper-related data access: title normalization, dedup hashing, draft URL operations."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from database.connection import execute_query, fetch_all


def normalize_title(title: str | None) -> str:
    """Normalize a title for dedup: lowercase, strip punctuation, collapse whitespace."""
    if not title:
        return ''
    t = title.lower().strip()
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def compute_title_hash(title: str | None) -> str:
    """SHA-256 hash of normalized title for cross-researcher dedup."""
    normalized = normalize_title(title)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def update_draft_url_status(paper_id: int, status: str) -> None:
    """Update draft URL validation status."""
    execute_query(
        "UPDATE papers SET draft_url_status = %s, draft_url_checked_at = %s WHERE id = %s",
        (status, datetime.now(timezone.utc), paper_id),
    )


def get_unchecked_draft_urls(limit: int = 100) -> list[dict]:
    """Get papers with unchecked draft URLs for validation."""
    return fetch_all(
        """SELECT id, draft_url FROM papers
           WHERE draft_url IS NOT NULL AND draft_url_status = 'unchecked'
           LIMIT %s""",
        (limit,),
    )
