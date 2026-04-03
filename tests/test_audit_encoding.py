"""Tests for the encoding audit script's scan_table function."""
import os
import sys

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from unittest.mock import MagicMock

# Ensure project root is on the path for script imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.audit_encoding import scan_table


class TestAuditScanTable:
    """Test the scan_table function used by the audit script."""

    def test_finds_mojibake_in_rows(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "title": "fÃ¼r die Wirtschaft", "abstract": None, "venue": "Clean Venue"},
            {"id": 2, "title": "Clean Title", "abstract": "Ã©conomie mondiale", "venue": None},
            {"id": 3, "title": "All Clean", "abstract": "No issues", "venue": "Fine"},
        ]

        findings = scan_table(mock_cursor, "papers", "id", ["title", "abstract", "venue"])

        assert len(findings) == 2
        assert findings[0]["table"] == "papers"
        assert findings[0]["column"] == "title"
        assert findings[0]["row_id"] == 1
        assert findings[0]["fixed"] == "für die Wirtschaft"
        assert findings[1]["column"] == "abstract"
        assert findings[1]["row_id"] == 2
        assert findings[1]["fixed"] == "économie mondiale"

    def test_no_findings_for_clean_data(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "title": "Clean Title", "abstract": "No issues", "venue": "Fine"},
        ]

        findings = scan_table(mock_cursor, "papers", "id", ["title", "abstract", "venue"])
        assert len(findings) == 0

    def test_handles_all_null_values(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "title": None, "abstract": None, "venue": None},
        ]

        findings = scan_table(mock_cursor, "papers", "id", ["title", "abstract", "venue"])
        assert len(findings) == 0
