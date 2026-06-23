"""Database configuration and text encoding guards.

db_config: validated MySQL connection dict from environment variables.
Encoding guards: mojibake detection and auto-fix for text fields (ftfy).
"""
import logging
import os
import re

import ftfy
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Database configuration (was db_config.py)
# ---------------------------------------------------------------------------

REQUIRED_ENV_VARS = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'GOOGLE_API_KEY']

_missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
if _missing:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(_missing)}\n"
        "Copy .env.example to .env and fill in all required values."
    )

_DB_NAME = os.environ['DB_NAME']
_DB_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$')
if not _DB_NAME_RE.match(_DB_NAME):
    raise EnvironmentError(
        f"DB_NAME '{_DB_NAME}' is invalid. "
        "Must match ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"
    )

db_config = {
    'host': os.environ['DB_HOST'],
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ['DB_USER'],
    'password': os.environ['DB_PASSWORD'],
    'database': _DB_NAME,
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci',
}

_ssl_ca = os.environ.get('DB_SSL_CA')
if _ssl_ca:
    db_config['ssl_ca'] = _ssl_ca
    db_config['ssl_verify_cert'] = True

# ---------------------------------------------------------------------------
# Encoding guards (was encoding_guard.py)
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_FTFY_CONFIG = dict(
    uncurl_quotes=False,
    fix_latin_ligatures=False,
    fix_character_width=False,
    unescape_html=False,
)


def has_mojibake(text: str) -> bool:
    """Return True if text contains mojibake (double-encoded UTF-8)."""
    if not text:
        return False
    return ftfy.fix_text(text, **_FTFY_CONFIG) != text


def fix_encoding(text: str) -> tuple[str, bool]:
    """Fix mojibake in text. Returns (fixed_text, was_changed)."""
    if not text:
        return text, False
    fixed = ftfy.fix_text(text, **_FTFY_CONFIG)
    return fixed, fixed != text


def guard_text_fields(row: dict, fields: list[str], context: str) -> dict:
    """Check and fix mojibake in specified text fields of a row dict."""
    for field in fields:
        value = row.get(field)
        if value is None:
            continue
        fixed, was_changed = fix_encoding(value)
        if was_changed:
            logger.warning(
                'Mojibake fixed in %s.%s: "%s" → "%s"',
                context, field, value, fixed,
            )
            row[field] = fixed
    return row
