"""OpenAI Batch API integration for quality review of feed events.

Submits unreviewed events as a batch job (50% cheaper than real-time),
polls for completion, and processes results with auto-applied corrections.
"""
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backend.database import fetch_all, execute_query
from backend.enrichment.quality_review import (
    MODEL, get_unreviewed_events, build_review_payload,
    apply_corrections, save_review, _extract_json,
)

logger = logging.getLogger(__name__)


def _get_openai_client():
    from openai import OpenAI
    return OpenAI()


def submit_review_batch(limit: int = 100) -> str | None:
    """Build JSONL from unreviewed events, submit to OpenAI Batch API.

    Returns the openai_batch_id, or None if nothing to submit.
    """
    events = get_unreviewed_events(limit=limit)
    if not events:
        logger.info("No unreviewed events to submit")
        return None

    lines = []
    event_map = {}
    for event in events:
        prompt = build_review_payload(event)
        if prompt is None:
            continue
        custom_id = f"evt_{event['event_id']}"
        event_map[custom_id] = event
        lines.append(json.dumps({
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL,
                "max_completion_tokens": 512,
                "messages": [{"role": "user", "content": prompt}],
            },
        }))

    if not lines:
        logger.info("All events too large for review prompt")
        return None

    client = _get_openai_client()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        f.write("\n".join(lines))
        tmp_path = f.name

    try:
        uploaded = client.files.create(
            file=Path(tmp_path),
            purpose="batch",
        )

        batch = client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )

        execute_query(
            """INSERT INTO review_batch_jobs
               (openai_batch_id, input_file_id, status, event_count, model, created_at)
               VALUES (%s, %s, 'submitted', %s, %s, %s)""",
            (batch.id, uploaded.id, len(lines), MODEL,
             datetime.now(timezone.utc)),
        )

        logger.info("Review batch submitted: %s (%d events)", batch.id, len(lines))
        return batch.id

    finally:
        Path(tmp_path).unlink(missing_ok=True)


def check_review_batches() -> int:
    """Poll pending review batches and process completed results.

    Returns the number of events processed across all completed batches.
    """
    pending = fetch_all(
        """SELECT id, openai_batch_id FROM review_batch_jobs
           WHERE status IN ('submitted','validating','in_progress','finalizing')""",
    )
    if not pending:
        logger.info("No pending review batches")
        return 0

    client = _get_openai_client()
    total_processed = 0

    for row in pending:
        db_id = row["id"]
        batch_id = row["openai_batch_id"]

        try:
            batch = client.batches.retrieve(batch_id)
        except Exception as e:
            logger.error("Failed to retrieve batch %s: %s", batch_id, e)
            continue

        if batch.status in ("validating", "in_progress", "finalizing"):
            execute_query(
                "UPDATE review_batch_jobs SET status = %s WHERE id = %s",
                (batch.status, db_id),
            )
            logger.info("Batch %s: %s", batch_id, batch.status)
            continue

        if batch.status in ("failed", "expired", "cancelled"):
            error_msg = str(batch.errors) if batch.errors else batch.status
            execute_query(
                """UPDATE review_batch_jobs
                   SET status = %s, error_message = %s, completed_at = %s
                   WHERE id = %s""",
                (batch.status, error_msg, datetime.now(timezone.utc), db_id),
            )
            logger.warning("Batch %s: %s — %s", batch_id, batch.status, error_msg)
            continue

        if batch.status != "completed":
            logger.info("Batch %s: unexpected status %s", batch_id, batch.status)
            continue

        processed = _process_completed_batch(client, batch, db_id)
        total_processed += processed

    return total_processed


def _process_completed_batch(client, batch, db_id: int) -> int:
    """Download and process results from a completed batch."""
    if not batch.output_file_id:
        logger.warning("Batch %s completed but no output file", batch.id)
        execute_query(
            """UPDATE review_batch_jobs
               SET status = 'failed', error_message = 'no output file',
                   completed_at = %s WHERE id = %s""",
            (datetime.now(timezone.utc), db_id),
        )
        return 0

    content = client.files.content(batch.output_file_id)
    lines = content.text.strip().split("\n")

    events_by_id = _load_events_for_batch(lines)

    processed = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0

    for line in lines:
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed JSONL line")
            continue

        custom_id = result.get("custom_id", "")
        event_id = _parse_event_id(custom_id)
        if event_id is None:
            continue

        response = result.get("response", {})
        if response.get("status_code") != 200:
            logger.warning("Batch item %s failed: %s", custom_id, response.get("status_code"))
            continue

        body = response.get("body", {})
        usage = body.get("usage", {})
        total_prompt_tokens += usage.get("prompt_tokens", 0)
        total_completion_tokens += usage.get("completion_tokens", 0)

        choices = body.get("choices", [])
        if not choices:
            continue

        raw_text = choices[0].get("message", {}).get("content", "")
        review = _extract_json(raw_text)
        if review is None:
            logger.warning("Failed to parse review JSON for event %d", event_id)
            continue

        event = events_by_id.get(event_id)
        if event is None:
            logger.warning("Event %d not found in DB (may have been deleted)", event_id)
            save_review(event_id, review)
            processed += 1
            continue

        corrections = apply_corrections(event, review)
        save_review(event_id, review, corrections or None)
        processed += 1

        if corrections:
            logger.info("Event %d: %d corrections applied", event_id, len(corrections))

    cost = _estimate_cost(total_prompt_tokens, total_completion_tokens, batch=True)
    execute_query(
        """UPDATE review_batch_jobs
           SET status = 'completed', output_file_id = %s, completed_at = %s,
               prompt_tokens = %s, completion_tokens = %s, cost_usd = %s
           WHERE id = %s""",
        (batch.output_file_id, datetime.now(timezone.utc),
         total_prompt_tokens, total_completion_tokens, cost, db_id),
    )

    logger.info(
        "Batch %s completed: %d events processed, %d prompt tokens, %d completion tokens, $%.4f",
        batch.id, processed, total_prompt_tokens, total_completion_tokens, cost,
    )
    return processed


def _load_events_for_batch(lines: list[str]) -> dict[int, dict]:
    """Load event data from DB for all event IDs in batch results."""
    event_ids = []
    for line in lines:
        try:
            result = json.loads(line)
            eid = _parse_event_id(result.get("custom_id", ""))
            if eid is not None:
                event_ids.append(eid)
        except json.JSONDecodeError:
            continue

    if not event_ids:
        return {}

    placeholders = ",".join(["%s"] * len(event_ids))
    rows = fetch_all(
        f"""
        SELECT fe.id AS event_id, fe.event_type, fe.old_status, fe.new_status,
               fe.old_title, fe.new_title,
               p.id AS paper_id, p.title, p.year, p.venue, p.source_url,
               p.status, p.abstract
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        WHERE fe.id IN ({placeholders})
        """,
        tuple(event_ids),
    )
    return {r["event_id"]: r for r in rows}


def _parse_event_id(custom_id: str) -> int | None:
    if custom_id.startswith("evt_"):
        try:
            return int(custom_id[4:])
        except ValueError:
            pass
    return None


def _estimate_cost(prompt_tokens: int, completion_tokens: int,
                   batch: bool = True) -> float:
    """Estimate cost in USD for GPT 5.4 Mini."""
    input_rate = 0.375 if batch else 0.75  # per 1M tokens
    output_rate = 2.25 if batch else 4.50
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
