"""LLM usage logging and cost estimation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.database.connection import execute_query, fetch_all

# (prompt, completion) cost per 1M tokens — Google AI Studio Gemini 2.5 Flash.
# Source: https://ai.google.dev/gemini-api/docs/pricing (May 2026).
_LLM_PRICING = {
    "gemini-2.5-flash": (0.30, 2.50),
}


def log_llm_usage(call_type: str, model: str, usage: object, context_url: str | None = None,
                  researcher_id: int | None = None, scrape_log_id: int | None = None) -> None:
    """Log an LLM API call with token counts and estimated cost. Failures are silenced."""
    try:
        prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
        completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
        total_tokens = getattr(usage, 'total_tokens', 0) or (prompt_tokens + completion_tokens)
        pricing = _LLM_PRICING.get(model)
        if pricing:
            prompt_rate, completion_rate = pricing
            estimated_cost = (
                prompt_tokens * prompt_rate / 1_000_000
                + completion_tokens * completion_rate / 1_000_000
            )
        else:
            estimated_cost = None
            logging.warning("No pricing entry for model '%s' — cost will be NULL", model)
        execute_query(
            """INSERT INTO llm_usage
               (called_at, call_type, model, prompt_tokens, completion_tokens,
                total_tokens, estimated_cost_usd, context_url,
                researcher_id, scrape_log_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (datetime.now(timezone.utc), call_type, model, prompt_tokens,
             completion_tokens, total_tokens, estimated_cost,
             context_url, researcher_id, scrape_log_id),
        )
    except Exception as e:
        logging.warning(f"log_llm_usage failed (non-fatal): {e}")


def record_llm_quota_limit(source: str, retry_after: float | None = None) -> None:
    """Remember that ``source`` was refused by the provider just now. Never raises."""
    try:
        execute_query(
            """INSERT INTO llm_quota_signals (source, limited_at, retry_after_seconds)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE limited_at = VALUES(limited_at),
                                       retry_after_seconds = VALUES(retry_after_seconds)""",
            (source, datetime.now(timezone.utc).replace(tzinfo=None),
             int(retry_after) if retry_after is not None else None),
        )
    except Exception as e:
        logging.warning("record_llm_quota_limit failed (non-fatal): %s", e)


def get_last_llm_quota_limit(source: str) -> datetime | None:
    """When ``source`` was last refused by the provider (naive UTC), or None."""
    try:
        rows = fetch_all(
            "SELECT limited_at FROM llm_quota_signals WHERE source = %s", (source,),
        )
    except Exception as e:
        if getattr(e, "errno", None) == 1146:  # table not created yet
            return None
        raise
    return rows[0]["limited_at"] if rows else None


def count_extraction_backlog() -> int:
    """Pages waiting for LLM extraction (same predicate as the extraction queue).

    Reads only indexed html_content columns, so MySQL answers from
    idx_extraction_state instead of loading stored pages.
    """
    rows = fetch_all(
        """SELECT COUNT(*) AS n
           FROM researcher_urls ru
           JOIN html_content hc ON hc.url_id = ru.id
           WHERE ru.is_active = TRUE
             AND hc.content_hash IS NOT NULL
             AND hc.has_stored_content = TRUE
             AND (hc.extracted_hash IS NULL OR hc.extracted_hash != hc.content_hash)"""
    )
    return int(rows[0]["n"]) if rows else 0
