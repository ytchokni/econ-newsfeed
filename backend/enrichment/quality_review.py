"""LLM quality review for feed events — detects and corrects data quality issues.

Reviews feed events in batches using GPT 5.4 Mini, checking for misclassification,
wrong venues, bad author names, false new-paper events, and other issues.
Auto-applies high-severity corrections (status, venue, hide).
"""
import json
import logging
import re
from datetime import datetime, timezone

from backend.database import (
    fetch_all, execute_query,
    get_authors_for_papers,
)

logger = logging.getLogger(__name__)

MODEL = "gpt-5.4-mini"

REVIEW_PROMPT = """You are reviewing a feed event from an economics research paper tracker that monitors ~1,200 researchers' personal websites. An LLM extracted this publication data from the researcher's webpage. Check for data quality issues and suggest corrections.

EVENT:
Type: {event_type}
{event_details}

PAPER METADATA:
Title: {title}
Authors: {authors}
Year: {year}
Venue: {venue}
Status: {status}
Abstract: {abstract}
Source URL: {source_url}

{snapshot_section}

Check for these issues and suggest corrections:
1. MISCLASSIFICATION — is the status wrong? (e.g. paper with a journal name marked "working_paper", or a working paper marked "published"). If wrong, what should the correct status be? Valid statuses: published, accepted, revise_and_resubmit, working_paper, work_in_progress
2. WRONG_VENUE — is the journal/venue name garbled, concatenated with other text, or clearly wrong? If so, what is the correct venue name (or "none" to clear it)?
3. BAD_AUTHORS — are author names garbled, duplicated, or incomplete?
4. NOT_NEW — for new_paper events, does this look like it was already on the page (not actually new)?
5. OTHER — any other data quality issue (e.g. venue/year data lost compared to earlier snapshot)

Respond ONLY with JSON (no markdown fences):
{{"issues": [{{"type": "MISCLASSIFICATION|WRONG_VENUE|BAD_AUTHORS|NOT_NEW|OTHER", "severity": "high|medium|low", "description": "...", "correction": "suggested_value_or_null"}}], "notes": "one-line assessment"}}

For the correction field:
- MISCLASSIFICATION: the correct status (e.g. "published")
- WRONG_VENUE: the correct venue name, or "none" to clear
- NOT_NEW: "hide" to remove the feed event
- BAD_AUTHORS/OTHER: null (needs manual review)

If no issues: {{"issues": [], "notes": "No issues found"}}"""

MAX_ABSTRACT_CHARS = 500
MAX_PROMPT_CHARS = 8000
VALID_STATUSES = frozenset({
    "published", "accepted", "revise_and_resubmit",
    "reject_and_resubmit", "working_paper", "work_in_progress",
})


_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI()
    return _openai_client


def _call_model(prompt: str) -> dict | None:
    try:
        client = _get_openai_client()
        resp = client.chat.completions.create(
            model=MODEL,
            max_completion_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content
    except Exception as e:
        logger.error("Model call failed: %s", e)
        return None

    return _extract_json(raw)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def get_unreviewed_events(limit: int = 100) -> list[dict]:
    """Fetch feed events not yet reviewed."""
    rows = fetch_all(
        """
        SELECT fe.id AS event_id, fe.event_type, fe.old_status, fe.new_status,
               fe.old_title, fe.new_title, fe.created_at,
               p.id AS paper_id, p.title, p.year, p.venue, p.source_url,
               p.status, p.abstract
        FROM feed_events fe
        JOIN papers p ON p.id = fe.paper_id
        WHERE NOT EXISTS (
            SELECT 1 FROM feed_event_reviews r WHERE r.feed_event_id = fe.id
        )
        ORDER BY fe.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    if not rows:
        return []

    paper_ids = list({r["paper_id"] for r in rows})
    authors_map = get_authors_for_papers(paper_ids)
    snapshots_map = _get_snapshots_for_papers(paper_ids)

    for row in rows:
        row["authors"] = authors_map.get(row["paper_id"], [])
        row["snapshots"] = snapshots_map.get(row["paper_id"], [])

    return rows


def _get_snapshots_for_papers(paper_ids: list[int]) -> dict[int, list[dict]]:
    if not paper_ids:
        return {}
    placeholders = ",".join(["%s"] * len(paper_ids))
    rows = fetch_all(
        f"""
        SELECT paper_id, status, venue, year, scraped_at
        FROM paper_snapshots
        WHERE paper_id IN ({placeholders})
        ORDER BY paper_id, scraped_at DESC
        """,
        tuple(paper_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in paper_ids}
    for row in rows:
        if len(result[row["paper_id"]]) < 4:
            result[row["paper_id"]].append(row)
    return result


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _format_authors(authors: list[dict]) -> str:
    parts = []
    for a in authors:
        first = a.get("first_name", "")
        last = a.get("last_name", "")
        parts.append(f"{first} {last}".strip())
    return "; ".join(parts) if parts else "(none)"


def _format_snapshot_section(snapshots: list[dict]) -> str:
    if not snapshots or len(snapshots) < 2:
        return ""
    lines = ["SNAPSHOT HISTORY (most recent first):"]
    for i, snap in enumerate(snapshots[:4]):
        lines.append(
            f"  [{i}] status={snap.get('status')}, venue={snap.get('venue')}, "
            f"year={snap.get('year')}, scraped={snap.get('scraped_at')}"
        )
    return "\n".join(lines)


def build_review_payload(event: dict) -> str | None:
    event_type = event["event_type"]

    if event_type == "status_change":
        event_details = f"Status changed: {event.get('old_status')} -> {event.get('new_status')}"
    elif event_type == "title_change":
        old_t = event.get("old_title") or "(unknown)"
        new_t = event.get("new_title") or event.get("title", "(unknown)")
        event_details = f"Title changed:\n  Old: {old_t}\n  New: {new_t}"
    else:
        event_details = "This paper was newly discovered on the researcher's page."

    abstract = event.get("abstract") or ""
    if len(abstract) > MAX_ABSTRACT_CHARS:
        abstract = abstract[:MAX_ABSTRACT_CHARS] + "..."

    prompt = REVIEW_PROMPT.format(
        event_type=event_type,
        event_details=event_details,
        title=event.get("title", ""),
        authors=_format_authors(event.get("authors", [])),
        year=event.get("year") or "(none)",
        venue=event.get("venue") or "(none)",
        status=event.get("status") or "(none)",
        abstract=abstract or "(none)",
        source_url=event.get("source_url", ""),
        snapshot_section=_format_snapshot_section(event.get("snapshots", [])),
    )

    if len(prompt) > MAX_PROMPT_CHARS:
        return None
    return prompt


# ---------------------------------------------------------------------------
# Review persistence
# ---------------------------------------------------------------------------

def save_review(event_id: int, review: dict,
                corrections: list[dict] | None = None) -> None:
    execute_query(
        """INSERT INTO feed_event_reviews
           (feed_event_id, model, issues, corrections_applied, reviewed_at)
           VALUES (%s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE
             model = VALUES(model),
             issues = VALUES(issues),
             corrections_applied = VALUES(corrections_applied),
             reviewed_at = VALUES(reviewed_at)""",
        (
            event_id,
            MODEL,
            json.dumps(review.get("issues", [])),
            json.dumps(corrections) if corrections else None,
            datetime.now(timezone.utc),
        ),
    )


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------

def apply_corrections(event: dict, review: dict, dry_run: bool = False) -> list[dict]:
    """Apply high-severity corrections from a review. Returns actions taken."""
    actions = []

    for issue in review.get("issues", []):
        if issue.get("severity") != "high":
            continue

        correction = issue.get("correction")
        if not correction:
            continue

        issue_type = issue.get("type")
        paper_id = event["paper_id"]
        event_id = event["event_id"]

        if issue_type == "MISCLASSIFICATION" and correction in VALID_STATUSES:
            action = {
                "type": "update_status",
                "paper_id": paper_id,
                "old_value": event.get("status"),
                "new_value": correction,
            }
            actions.append(action)
            if not dry_run:
                execute_query(
                    "UPDATE papers SET status = %s WHERE id = %s",
                    (correction, paper_id),
                )
                logger.info("Corrected status: paper %d %s -> %s",
                            paper_id, event.get("status"), correction)

        elif issue_type == "WRONG_VENUE":
            new_venue = None if correction.lower() == "none" else correction
            action = {
                "type": "update_venue",
                "paper_id": paper_id,
                "old_value": event.get("venue"),
                "new_value": new_venue,
            }
            actions.append(action)
            if not dry_run:
                execute_query(
                    "UPDATE papers SET venue = %s WHERE id = %s",
                    (new_venue, paper_id),
                )
                logger.info("Corrected venue: paper %d '%s' -> '%s'",
                            paper_id, event.get("venue"), new_venue)

        elif issue_type == "NOT_NEW" and correction.lower() == "hide":
            action = {
                "type": "hide_event",
                "event_id": event_id,
                "paper_title": event.get("title"),
            }
            actions.append(action)
            if not dry_run:
                execute_query(
                    "DELETE FROM feed_events WHERE id = %s", (event_id,),
                )
                logger.info("Hid feed event %d: %s",
                            event_id, event.get("title", "")[:60])

    return actions


# ---------------------------------------------------------------------------
# Main entry point — real-time review (used for backfill and CLI)
# ---------------------------------------------------------------------------

def review_events(batch_size: int = 100, dry_run: bool = False,
                  limit: int | None = None) -> dict:
    """Review unreviewed feed events in batches. Returns summary stats."""
    total_reviewed = 0
    total_issues = 0
    total_corrections = 0
    effective_limit = limit or 10_000

    while total_reviewed < effective_limit:
        batch_limit = min(batch_size, effective_limit - total_reviewed)
        events = get_unreviewed_events(limit=batch_limit)
        if not events:
            break

        logger.info("Reviewing batch of %d events%s...",
                     len(events), " (dry-run)" if dry_run else "")

        for i, event in enumerate(events):
            prompt = build_review_payload(event)
            if prompt is None:
                logger.info("  [%d/%d] Skipped (too large): %s",
                            i + 1, len(events), event.get("title", "")[:50])
                continue

            review = _call_model(prompt)
            if review is None:
                logger.warning("  [%d/%d] Model error: %s",
                               i + 1, len(events), event.get("title", "")[:50])
                continue

            issues = review.get("issues", [])
            high_issues = [x for x in issues if x.get("severity") == "high"]

            corrections = apply_corrections(event, review, dry_run=dry_run)
            # hide_event deletes the feed_event row; save must tolerate that
            has_hide = any(c.get("type") == "hide_event" for c in corrections)
            if not has_hide:
                save_review(event["event_id"], review, corrections or None)

            total_reviewed += 1
            total_issues += len(issues)
            total_corrections += len(corrections)

            flag = " " if not issues else ("!" if high_issues else "~")
            corr_str = f" [{len(corrections)} corrections]" if corrections else ""
            logger.info(
                "  [%d/%d] %s issues=%d%s | %s",
                i + 1, len(events), flag, len(issues),
                corr_str, event.get("title", "")[:55],
            )

    summary = {
        "reviewed": total_reviewed,
        "issues": total_issues,
        "corrections": total_corrections,
        "dry_run": dry_run,
    }
    logger.info("Review complete: %s", summary)
    return summary
