"""Weekly digest email — query events, render HTML, send via Resend."""
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

import resend

from database import Database

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DIGEST_FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "digest@econ-newsfeed.com")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
NEXTAUTH_SECRET = os.environ.get("NEXTAUTH_SECRET", "")


def _render_digest_html(events: list[dict], user_name: str | None,
                        unsubscribe_url: str, since: datetime, until: datetime) -> str:
    """Render digest events as an HTML email body grouped by researcher."""
    by_researcher: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        key = f"{ev['first_name']} {ev['last_name']}"
        by_researcher[key].append(ev)

    since_str = since.strftime("%B %d")
    until_str = until.strftime("%B %d, %Y")
    greeting = f"Hi {user_name}" if user_name else "Hi"

    sections = []
    for researcher_name, researcher_events in sorted(by_researcher.items()):
        items = []
        seen_papers = set()
        for ev in researcher_events:
            if ev['paper_id'] in seen_papers:
                continue
            seen_papers.add(ev['paper_id'])
            status_label = (ev.get('status') or 'unknown').replace('_', ' ').title()
            paper_url = f"{FRONTEND_URL}/papers/{ev['paper_id']}"
            items.append(
                f'<li style="margin-bottom:8px;">'
                f'<a href="{paper_url}" style="color:#2563eb;text-decoration:none;">{ev["title"]}</a>'
                f' <span style="color:#6b7280;font-size:13px;">({status_label})</span>'
                f'</li>'
            )
        if items:
            sections.append(
                f'<h3 style="margin:16px 0 8px;color:#1a2332;font-size:16px;">{researcher_name}</h3>'
                f'<ul style="padding-left:20px;margin:0;">{"".join(items)}</ul>'
            )

    body_html = "".join(sections) if sections else "<p>No new activity this week.</p>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#1a2332;">
  <h1 style="font-size:20px;color:#1a2332;">Econ Newsfeed — Weekly Digest</h1>
  <p style="color:#6b7280;font-size:14px;">{since_str} – {until_str}</p>
  <p>{greeting},</p>
  <p>Here's what's new from the researchers you follow:</p>
  {body_html}
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
  <p style="font-size:12px;color:#9ca3af;">
    <a href="{FRONTEND_URL}" style="color:#2563eb;">Manage your follows</a> ·
    <a href="{unsubscribe_url}" style="color:#2563eb;">Unsubscribe</a>
  </p>
</body>
</html>"""


def _send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set, skipping email to %s", to)
        return False
    try:
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": DIGEST_FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        return True
    except Exception as e:
        logger.error("Failed to send digest to %s: %s: %s", to, type(e).__name__, e)
        return False


def run_weekly_digest() -> int:
    """Send weekly digest to all eligible users. Returns number of emails sent."""
    now = datetime.now(timezone.utc)
    recipients = Database.get_digest_recipients()
    if not recipients:
        logger.info("Digest: no eligible recipients")
        return 0

    sent = 0
    for user in recipients:
        since = user.get("last_digest_sent") or user["created_at"]
        if isinstance(since, str):
            since = datetime.fromisoformat(since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        events = Database.get_feed_events_for_researchers(
            user["researcher_ids"], since
        )
        if not events:
            continue

        unsubscribe_token = Database.generate_unsubscribe_token(
            user["id"], NEXTAUTH_SECRET
        )
        unsubscribe_url = (
            f"{FRONTEND_URL}/api/users/unsubscribe?token={unsubscribe_token}"
        )

        since_str = since.strftime("%B %d")
        until_str = now.strftime("%B %d")
        subject = f"Econ Newsfeed — Weekly Digest ({since_str} – {until_str})"

        html = _render_digest_html(
            events, user.get("name"), unsubscribe_url, since, now
        )

        if _send_email(user["email"], subject, html):
            Database.update_last_digest_sent(user["id"], now)
            sent += 1
            logger.info("Digest sent to %s (%d events)", user["email"], len(events))

    logger.info("Weekly digest complete: %d/%d emails sent", sent, len(recipients))
    return sent
