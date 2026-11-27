"""LLM author disambiguation runs before the save transaction, not inside it."""
from contextlib import contextmanager
from unittest.mock import patch

from backend.database import researchers
from backend.pipeline.paper_saver import PaperSaver

CANDIDATES = [{"id": 1, "first_name": "John", "last_name": "Smith"},
              {"id": 2, "first_name": "Jane", "last_name": "Smith"}]


def _fake_lookup(exact=None):
    def fetch_one(sql, params=None):
        return exact
    def fetch_all(sql, params=None):
        return CANDIDATES
    return fetch_one, fetch_all


def test_prefetched_answer_is_reused_without_a_second_call():
    fetch_one, fetch_all = _fake_lookup()
    with patch.object(researchers, "fetch_one", side_effect=fetch_one), \
         patch.object(researchers, "fetch_all", side_effect=fetch_all), \
         patch.object(researchers, "_ask_disambiguation", return_value=2) as ask:
        with researchers.disambiguation_prefetch():
            researchers.prefetch_disambiguation("J.", "Smith")
            assert ask.call_count == 1
            assert researchers._disambiguate_researcher("J.", "Smith", CANDIDATES) == 2
            assert ask.call_count == 1  # served from the prefetch
        # Outside the prefetch scope the model is asked again.
        researchers._disambiguate_researcher("J.", "Smith", CANDIDATES)
        assert ask.call_count == 2


def test_changed_candidates_miss_the_prefetch():
    fetch_one, fetch_all = _fake_lookup()
    with patch.object(researchers, "fetch_one", side_effect=fetch_one), \
         patch.object(researchers, "fetch_all", side_effect=fetch_all), \
         patch.object(researchers, "_ask_disambiguation", return_value=None) as ask:
        with researchers.disambiguation_prefetch():
            researchers.prefetch_disambiguation("J.", "Smith")
            researchers._disambiguate_researcher("J.", "Smith", CANDIDATES + [{"id": 3, "first_name": "Joe", "last_name": "Smith"}])
        assert ask.call_count == 2


def test_exact_match_needs_no_model_call():
    fetch_one, fetch_all = _fake_lookup(exact={"id": 9})
    with patch.object(researchers, "fetch_one", side_effect=fetch_one), \
         patch.object(researchers, "fetch_all", side_effect=fetch_all), \
         patch.object(researchers, "_ask_disambiguation") as ask:
        with researchers.disambiguation_prefetch():
            researchers.prefetch_disambiguation("John", "Smith")
    ask.assert_not_called()


def test_save_publications_prefetches_before_opening_its_transaction():
    order = []

    @contextmanager
    def fake_connection():
        order.append("transaction")
        raise RuntimeError("stop here")
        yield

    pubs = [{"title": "A paper", "authors": [["Jo", "Smith"], ["Ann", "Lee"]]}]
    with patch("backend.pipeline.paper_saver.prefetch_disambiguation",
               side_effect=lambda f, l: order.append(f"prefetch {f} {l}")), \
         patch("backend.pipeline.paper_saver.get_connection", side_effect=fake_connection):
        try:
            PaperSaver.save_publications("https://x.org", pubs)
        except RuntimeError:
            pass
    assert order == ["prefetch Jo Smith", "prefetch Ann Lee", "transaction"]
