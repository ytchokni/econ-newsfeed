"""Tests for batch pipeline data integrity — validation and snapshots."""
import os

# Env vars must be set before any app imports
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "econ_app")
os.environ.setdefault("DB_PASSWORD", "secret")
os.environ.setdefault("DB_NAME", "econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

import unittest

from publication import validate_publication, PublicationExtraction


class TestBatchValidationGap(unittest.TestCase):
    """batch_check must run validate_publication() on each parsed result."""

    def test_garbage_publication_rejected_by_validate(self):
        """A software-package-like extraction should be rejected by validate_publication."""
        garbage = {
            "title": "react-dom",
            "authors": [["", ""]],
            "year": None,
            "venue": None,
            "status": None,
            "draft_url": None,
            "abstract": None,
        }
        self.assertFalse(validate_publication(garbage))

    def test_valid_publication_accepted_by_validate(self):
        """A real economics paper should pass validate_publication."""
        valid = {
            "title": "Monetary Policy Shocks and Exchange Rate Dynamics",
            "authors": [["John", "Smith"], ["Jane", "Doe"]],
            "year": "2024",
            "venue": "American Economic Review",
            "status": "published",
            "draft_url": None,
            "abstract": "We study the effect of monetary policy on exchange rates.",
        }
        self.assertTrue(validate_publication(valid))

    def test_pydantic_valid_but_content_invalid(self):
        """Pydantic accepts structurally valid garbage — validate_publication must catch it."""
        item = {
            "title": "x",
            "authors": [["A", "B"]],
        }
        pub = PublicationExtraction(**item)
        dumped = pub.model_dump()
        self.assertIsNotNone(dumped)
        self.assertFalse(validate_publication(dumped))


if __name__ == "__main__":
    unittest.main()
