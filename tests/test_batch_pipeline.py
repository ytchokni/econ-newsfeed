"""Tests for batch pipeline data integrity — validation and snapshots."""
import unittest
import json as json_module
from unittest.mock import patch, MagicMock, ANY

from backend.pipeline.publication import validate_publication, PublicationExtraction


class TestBatchSubmitFileUpload(unittest.TestCase):
    """batch_submit must use genai SDK for file upload, OpenAI SDK for batch create."""

    @patch("backend.main.execute_query")
    @patch("backend.main.fetch_all", return_value=[])
    @patch("backend.main.Researcher")
    @patch("backend.main.HTMLFetcher")
    @patch("backend.main.Publication")
    def test_uses_genai_for_upload_and_openai_for_batch_create(
        self, mock_pub, mock_fetcher, mock_researcher, mock_fetch_all, mock_exec,
    ):
        mock_researcher.get_all_researcher_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "http://example.com", "page_type": "RESEARCH"},
        ]
        mock_fetcher.needs_extraction.return_value = True
        mock_fetcher.get_latest_text.return_value = "some text"
        mock_pub.build_extraction_prompt.return_value = "extract this"

        mock_genai = MagicMock()
        mock_uploaded = MagicMock()
        mock_uploaded.name = "files/abc123"
        mock_genai.files.upload.return_value = mock_uploaded

        mock_openai = MagicMock()
        mock_batch = MagicMock()
        mock_batch.id = "batch_xyz"
        mock_openai.batches.create.return_value = mock_batch

        with patch("backend.llm.client.get_genai_client", return_value=mock_genai), \
             patch("backend.llm.client.get_client", return_value=mock_openai), \
             patch("backend.llm.client.get_model", return_value="gemini-2.5-flash"):
            from backend.main import batch_submit
            batch_submit()

        # genai SDK used for file upload
        mock_genai.files.upload.assert_called_once()

        # OpenAI SDK used for batch create
        mock_openai.batches.create.assert_called_once_with(
            input_file_id="files/abc123",
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )


class TestBatchCheckFileDownload(unittest.TestCase):
    """batch_check must use genai SDK for file download."""

    @patch("backend.pipeline.extraction.persist_extraction")
    @patch("backend.main.HTMLFetcher")
    @patch("backend.main.log_llm_usage")
    @patch("backend.main.execute_query")
    @patch("backend.main.fetch_one")
    @patch("backend.main.fetch_all")
    def test_uses_genai_for_download(
        self, mock_fetch_all, mock_fetch_one, mock_exec, mock_log,
        mock_fetcher, mock_persist,
    ):
        # One pending batch
        mock_fetch_all.return_value = [
            {"id": 1, "openai_batch_id": "batch_abc"},
        ]

        # OpenAI SDK: batch is completed with an output file
        mock_openai = MagicMock()
        mock_batch_obj = MagicMock()
        mock_batch_obj.id = "batch_abc"
        mock_batch_obj.status = "completed"
        mock_batch_obj.output_file_id = "files/output123"
        mock_openai.batches.list.return_value = [mock_batch_obj]

        # genai SDK: file download returns JSONL with one valid result
        result_line = json_module.dumps({
            "custom_id": "url_1",
            "response": {
                "body": {
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                    "choices": [{
                        "message": {
                            "content": json_module.dumps({
                                "publications": [{
                                    "title": "Monetary Policy and Exchange Rates",
                                    "authors": [["John", "Smith"]],
                                    "year": "2024",
                                    "venue": "AER",
                                    "status": "published",
                                    "draft_url": None,
                                    "abstract": "We study monetary policy.",
                                }]
                            })
                        }
                    }],
                },
            },
        })
        mock_genai = MagicMock()
        mock_genai.files.download.return_value = result_line.encode("utf-8")

        mock_fetch_one.side_effect = [
            {"url": "http://example.com"},  # url lookup
            {"total_cost": 0.001},          # cost aggregation
        ]
        mock_fetcher.is_first_extraction.return_value = False

        with patch("backend.llm.client.get_genai_client", return_value=mock_genai), \
             patch("backend.llm.client.get_client", return_value=mock_openai), \
             patch("backend.llm.client.get_model", return_value="gemini-2.5-flash"):
            from backend.main import batch_check
            batch_check()

        # genai SDK used for download
        mock_genai.files.download.assert_called_once_with(file="files/output123")

        # OpenAI SDK used to list pending batches
        mock_openai.batches.list.assert_called_once_with(limit=100)


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
