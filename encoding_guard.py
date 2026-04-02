"""Centralized mojibake detection, auto-fix, and logging for text fields.

Uses ftfy to detect and repair double-encoded UTF-8 (e.g. "fÃ¼r" → "für").
Designed to be called before database writes and by the audit script.
"""
import logging

import ftfy

logger = logging.getLogger(__name__)

# Only fix actual encoding errors. Do NOT normalize smart quotes,
# ligatures, fullwidth chars, or HTML entities — those are intentional.
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
    """Check and fix mojibake in specified text fields of a row dict.

    Args:
        row: dict of column values (modified in place and returned)
        fields: list of field names to check
        context: human-readable context for log messages (e.g. "papers (id=42)")

    Returns:
        The row dict with any mojibake fields fixed.
    """
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
