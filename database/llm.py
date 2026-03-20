"""LLM usage logging and cost estimation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from database.connection import execute_query

_LLM_PRICING = {  # (prompt, completion) cost per 1M tokens
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
}


def log_llm_usage(call_type: str, model: str, usage: object, context_url: str | None = None,
                  researcher_id: int | None = None, scrape_log_id: int | None = None,
                  is_batch: bool = False, batch_job_id: int | None = None) -> None:
    """Log an LLM API call with token counts and estimated cost. Failures are silenced."""
    try:
        prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
        completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
        total_tokens = getattr(usage, 'total_tokens', 0) or (prompt_tokens + completion_tokens)
        pricing = _LLM_PRICING.get(model)
        if pricing:
            prompt_rate, completion_rate = pricing
            multiplier = 0.5 if is_batch else 1.0
            estimated_cost = multiplier * (
                prompt_tokens * prompt_rate / 1_000_000
                + completion_tokens * completion_rate / 1_000_000
            )
        else:
            estimated_cost = None
            logging.warning("No pricing entry for model '%s' — cost will be NULL", model)
        execute_query(
            """INSERT INTO llm_usage
               (called_at, call_type, model, prompt_tokens, completion_tokens,
                total_tokens, estimated_cost_usd, is_batch, context_url,
                researcher_id, scrape_log_id, batch_job_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (datetime.now(timezone.utc), call_type, model, prompt_tokens,
             completion_tokens, total_tokens, estimated_cost, is_batch,
             context_url, researcher_id, scrape_log_id, batch_job_id),
        )
    except Exception as e:
        logging.warning(f"log_llm_usage failed (non-fatal): {e}")
