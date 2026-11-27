"""How our crawlers identify themselves to the sites they fetch.

Every request names the bot and links the contact page, where site owners and
researchers find how to ask for removal. Sites can also block the bot by name
in robots.txt (``User-agent: EconNewsfeedBot``).
"""
import os

BOT_NAME = "EconNewsfeedBot"
BOT_VERSION = "1.0"
_DEFAULT_SITE = "https://econ-newsfeed.vercel.app"


def contact_url() -> str:
    site = os.environ.get("FRONTEND_URL", "").strip().rstrip("/")
    if not site or "localhost" in site or "127.0.0.1" in site:
        site = _DEFAULT_SITE
    return f"{site}/contact"


def bot_token() -> str:
    """``EconNewsfeedBot/1.0 (+https://…/contact)``"""
    return f"{BOT_NAME}/{BOT_VERSION} (+{contact_url()})"


def with_bot_token(user_agent: str) -> str:
    """Append the bot token to a configured User-Agent unless it already names us."""
    user_agent = (user_agent or "").strip()
    if BOT_NAME.lower() in user_agent.lower():
        return user_agent
    return f"{user_agent} {bot_token()}".strip()
