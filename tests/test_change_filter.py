"""Page changes that cannot change a publication skip the LLM."""
from unittest.mock import patch

import pytest

from backend.pipeline.change_filter import is_insignificant_change


@pytest.mark.parametrize("old,new", [
    ("Smith (Michael Jones) 2024", "Smith ( Michael Jones) 2024"),          # spacing
    ("Phone +431 7982601", "Phone +4317982601"),
    ("Cited by 35 | 308 views", "Cited by 36 | 309 views"),               # counters
    ("Downloads: (1,234)", "Downloads: (1,240)"),
    ("Same text", "Same text"),
])
def test_insignificant(old, new):
    assert is_insignificant_change(old, new)


@pytest.mark.parametrize("old,new", [
    ("Working paper 2025", "Working paper 2026"),                         # years are revisions
    ("Working paper, 2025.", "Working paper, 2026."),                     # year before a full stop
    ("Draft 03.2025", "Draft 11.2026"),                                   # dotted dates
    ("Version of 11.2025", "Version of 02.2026"),
    ("(2025)", "(2026)"),
    ("Revise and resubmit, JPE", "Accepted, JPE"),                        # status words
    ("Paper A. Paper B.", "Paper A. Paper C. Paper B."),                  # insertion
    ("Paper A. Paper B.", "Paper A."),                                    # removal
    ("Affects of trade", "Effects of trade"),                             # title fix
    ("Journal of X, 2026", "Journal of X, 2026, 14(2)"),                  # volume added
    ("Journal of X, Vol. 12, No. 3", "Journal of X, Vol. 13, No. 3"),     # volume changed
    ("Journal of X, Vol. 12, No. 3", "Journal of X, Vol. 12, No. 4"),     # issue changed
    ("Issue 5", "Issue 6"),
    ("pp. 12-30", "pp. 14-30"),
])
def test_significant(old, new):
    assert not is_insignificant_change(old, new)


def test_counter_change_marks_page_extracted_without_llm_call():
    from backend.pipeline.extraction import extract_one_url

    payload = {"content": "Paper A. Cited by 36", "raw_html": None, "content_hash": "new",
               "extracted_at": "2026-11-01", "timestamp": None}
    row = {"id": 7, "url": "https://example.org", "researcher_id": 1, "page_type": "PUBLICATIONS"}
    with patch("backend.pipeline.extraction.HTMLFetcher.get_extraction_payload", return_value=payload), \
         patch("backend.pipeline.extraction.HTMLFetcher.get_previous_text", return_value="Paper A. Cited by 35"), \
         patch("backend.pipeline.extraction.HTMLFetcher.mark_extracted") as mark, \
         patch("backend.pipeline.extraction.Publication.try_extract_changes") as llm:
        outcome = extract_one_url(row)
    assert outcome.status == "empty"
    llm.assert_not_called()
    mark.assert_called_once_with(7, "new")
