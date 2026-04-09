"""LLM usage logging and cost estimation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from database.connection import execute_query

# (prompt, completion) cost per 1M tokens. Gemma 4 31B rates are placeholders
# pending Parasail invoice confirmation (see docs/superpowers/plans/2026-04-09-migrate-llm-to-parasail-gemma.md).
_LLM_PRICING = {
    "google/gemma-4-31b-it": (0.14, 0.40),
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
            # Parasail does not offer a batch discount — cost multiplier is 1.0
            # regardless of is_batch. The is_batch flag still distinguishes
            # sync vs batch calls in the llm_usage table for reporting.
            multiplier = 1.0
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
