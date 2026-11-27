"""Tests for incremental JEL classification (issue #271).

Covers bio cleaning, the richer prompt, the evidence hash, the budgeted batch
job and the OpenAlex author-topic fetch. Database and LLM are mocked.
"""
from unittest.mock import MagicMock, patch

import pytest
from openai import RateLimitError

from backend.enrichment import jel_job
from backend.enrichment.jel_classifier import (
    ClassificationOutcome,
    ResearcherProfile,
    build_classification_prompt,
    classify_profile,
    clean_bio,
    normalize_codes,
    rank_topic_names,
)


# ---- clean_bio ----

class TestCleanBio:
    def test_keeps_normal_bio(self):
        bio = "Jane Doe is a professor of economics at MIT. She studies labor markets."
        assert clean_bio(bio) == bio

    @pytest.mark.parametrize("text", [
        "<thought>* Input: Text from Esther Duflo's MIT homepage. * Task: Extract a professional",
        "<think>The user wants a description of the researcher, so I will",
        "* Input: Text from a homepage.\n* Task: Extract a professional description.",
        "- Goal: write a bio\n- Constraints: max 200 words",
        # Only caught by the shared description.looks_like_reasoning() check.
        "Draft: Jane Doe is an economist at MIT who studies trade policy.",
        "Let me summarise. Jane Doe is an economist at MIT who studies trade.",
    ])
    def test_rejects_reasoning_text(self, text):
        assert clean_bio(text) is None

    def test_strips_closed_thought_block_and_keeps_answer(self):
        text = ("<thought>Plan: summarise.</thought>\n"
                "Jane Doe is an assistant professor of economics working on trade.")
        assert clean_bio(text) == (
            "Jane Doe is an assistant professor of economics working on trade."
        )

    @pytest.mark.parametrize("text", [None, "", "   ", "Economist."])
    def test_empty_or_too_short(self, text):
        assert clean_bio(text) is None

    def test_truncates_long_bio(self):
        assert len(clean_bio("Economist studying trade. " * 200)) == 2000


# ---- helpers ----

def test_normalize_codes_filters_dedupes_and_caps():
    assert normalize_codes(["j", "F", "Y", "J", "Q1", "x", "A", "B", "C"]) == ["J", "F", "Q", "A", "B"]


def test_rank_topic_names_sums_scores():
    topics = [
        {"topic_name": "Labor markets", "score": 0.4},
        {"topic_name": "Trade", "score": 0.9},
        {"topic_name": "Labor markets", "score": 0.7},
        {"topic_name": "", "score": 1.0},
    ]
    assert rank_topic_names(topics) == ["Labor markets", "Trade"]


class TestInputHash:
    def _profile(self, **kw):
        base = dict(researcher_id=1, first_name="A", last_name="B", bio="Bio text here",
                    papers=[{"title": "Paper One"}, {"title": "Paper Two"}], topics=["T"])
        base.update(kw)
        return ResearcherProfile(**base)

    def test_order_insensitive(self):
        a = self._profile()
        b = self._profile(papers=[{"title": "paper two"}, {"title": "Paper One"}])
        assert a.input_hash() == b.input_hash()

    def test_changes_with_new_paper_or_bio(self):
        base = self._profile().input_hash()
        assert self._profile(papers=[{"title": "Paper Three"}]).input_hash() != base
        assert self._profile(bio="Another bio").input_hash() != base

    def test_classifiable(self):
        assert self._profile(bio=None).classifiable
        assert self._profile(papers=[]).classifiable
        assert not self._profile(bio=None, papers=[]).classifiable


class TestPrompt:
    def test_includes_papers_abstracts_and_topics(self):
        papers = [{"title": f"Paper {i}", "year": "2024",
                   "abstract": f"Abstract {i} " + "x" * 600} for i in range(5)]
        prompt = build_classification_prompt("Jane", "Doe", None, papers=papers,
                                             topics=["Labor Market Dynamics"])
        assert "(no bio available)" in prompt
        assert "- Paper 0 (2024)" in prompt
        assert "Abstract 2" in prompt and "Abstract 3" not in prompt  # 3 abstracts max
        assert "Labor Market Dynamics" in prompt
        assert "x" * 500 not in prompt  # abstracts truncated

    def test_backward_compatible_description_only(self):
        prompt = build_classification_prompt("Jane", "Doe", "I study labor markets.")
        assert "I study labor markets." in prompt
        assert "(no papers available)" in prompt


class TestClassifyProfile:
    @patch("backend.enrichment.jel_classifier.log_llm_usage")
    @patch("backend.llm.client.get_client")
    def test_rate_limit_is_reported(self, mock_get_client, _log):
        response = MagicMock(status_code=429, headers={})
        mock_get_client.return_value.chat.completions.create.side_effect = RateLimitError(
            "quota", response=response, body=None,
        )
        outcome = classify_profile(ResearcherProfile(1, "A", "B", "bio text " * 10))
        assert outcome.ok is False
        assert outcome.retry_after is not None


# ---- batch job ----

def _row(rid, *, description=None, input_hash=None, attempts=0, has_codes=0, oa=None):
    return {"id": rid, "first_name": "F", "last_name": f"L{rid}", "description": description,
            "openalex_author_id": oa, "input_hash": input_hash, "attempts": attempts,
            "has_codes": has_codes}


def _evidence(*titles, topics=()):
    return {"papers": [{"title": t, "abstract": None, "year": "2024"} for t in titles],
            "topics": [{"topic_name": n, "score": 0.9} for n in topics]}


class _JobHarness:
    """Patches the job's collaborators; exposes the mocks."""

    def __init__(self, candidates, inputs, outcomes=(), calls_today=0, author_topics=None):
        self.candidates = candidates
        self.inputs = inputs
        self.outcomes = list(outcomes)
        self.calls_today = calls_today
        self.author_topics = author_topics or {}

    def __enter__(self):
        self.patches = {
            "lock": patch.object(jel_job, "_acquire_lock", return_value=MagicMock()),
            "release": patch.object(jel_job, "_release_lock"),
            "count": patch.object(jel_job, "count_jel_llm_calls_since", return_value=self.calls_today),
            "cands": patch.object(jel_job, "get_jel_classification_candidates", return_value=self.candidates),
            "inputs": patch.object(jel_job, "get_jel_inputs_for_researchers", return_value=self.inputs),
            "classify": patch.object(jel_job, "classify_profile", side_effect=self.outcomes),
            "save_codes": patch.object(jel_job, "save_researcher_jel_codes"),
            "save_state": patch.object(jel_job, "save_jel_state"),
            "touch": patch.object(jel_job, "touch_jel_state"),
            "authors": patch.object(jel_job, "_fetch_author_topics", return_value=self.author_topics),
            "model": patch.object(jel_job, "get_model", return_value="test-model"),
        }
        self.m = {k: p.start() for k, p in self.patches.items()}
        return self

    def __exit__(self, *exc):
        for p in self.patches.values():
            p.stop()

    def run(self, **kw):
        kw.setdefault("delay_seconds", 0)
        return jel_job.run_jel_classification_batch(**kw)


def test_no_evidence_is_recorded_without_llm_call():
    with _JobHarness([_row(1)], {1: _evidence()}) as h:
        stats = h.run()
    assert stats["no_evidence"] == 1 and stats["llm_calls"] == 0
    h.m["classify"].assert_not_called()
    assert h.m["save_state"].call_args.kwargs["source"] == "none"


def test_contaminated_bio_is_ignored_and_papers_used():
    row = _row(1, description="<thought>* Input: homepage text * Task: extract")
    with _JobHarness([row], {1: _evidence("Minimum wages and employment")},
                     outcomes=[ClassificationOutcome(["J"], ok=True)]) as h:
        stats = h.run()
    profile = h.m["classify"].call_args.args[0]
    assert profile.bio is None
    assert profile.papers[0]["title"] == "Minimum wages and employment"
    assert stats["classified"] == 1
    h.m["save_codes"].assert_called_once_with(1, ["J"])
    state = h.m["save_state"].call_args.kwargs
    assert state["source"] == "llm" and state["used_bio"] is False and state["attempts"] == 0


def test_unchanged_evidence_skips_llm():
    evidence = _evidence("Paper A")
    digest = ResearcherProfile(1, "F", "L1", None, papers=evidence["papers"]).input_hash()
    with _JobHarness([_row(1, input_hash=digest)], {1: evidence}) as h:
        stats = h.run()
    assert stats["unchanged"] == 1
    h.m["classify"].assert_not_called()
    h.m["touch"].assert_called_once()


def test_empty_llm_answer_falls_back_to_topics():
    with _JobHarness([_row(1)], {1: _evidence("Paper", topics=["Monetary Policy and Inflation"])},
                     outcomes=[ClassificationOutcome([], ok=True)]) as h:
        stats = h.run()
    assert stats["topic_fallback"] == 1
    h.m["save_codes"].assert_called_once_with(1, ["E"])
    assert h.m["save_state"].call_args.kwargs["source"] == "topics"


def test_author_topics_join_prompt_and_fallback():
    row = _row(1, oa="A1")
    with _JobHarness([row], {1: _evidence("Paper")},
                     outcomes=[ClassificationOutcome([], ok=True)],
                     author_topics={"A1": [{"topic_name": "Urban housing markets", "score": 1.0}]}) as h:
        h.run()
    h.m["authors"].assert_called_once_with(["A1"])
    assert h.m["classify"].call_args.args[0].topics == ["Urban housing markets"]
    h.m["save_codes"].assert_called_once_with(1, ["R"])


def test_rate_limit_stops_run_without_recording():
    rows = [_row(1), _row(2)]
    with _JobHarness(rows, {1: _evidence("A"), 2: _evidence("B")},
                     outcomes=[ClassificationOutcome([], ok=False, retry_after=60.0)]) as h:
        stats = h.run()
    assert stats["stopped"] == "rate_limited"
    assert h.m["classify"].call_count == 1
    h.m["save_state"].assert_not_called()
    h.m["save_codes"].assert_not_called()


def test_daily_limit_blocks_llm_calls():
    with _JobHarness([_row(1)], {1: _evidence("A")}, calls_today=600) as h:
        stats = h.run(daily_limit=600)
    assert stats["stopped"] == "daily_limit"
    h.m["classify"].assert_not_called()


def test_call_budget_caps_calls_per_run():
    rows = [_row(i) for i in range(1, 6)]
    inputs = {i: _evidence(f"Paper {i}") for i in range(1, 6)}
    outcomes = [ClassificationOutcome(["D"], ok=True)] * 5
    with _JobHarness(rows, inputs, outcomes=outcomes, calls_today=8) as h:
        stats = h.run(max_calls=4, daily_limit=10)  # only 2 left today
    assert stats["llm_calls"] == 2
    assert stats["stopped"] == "call_budget"


def test_failure_keeps_existing_codes_and_counts_attempt():
    row = _row(1, has_codes=1, attempts=2)
    with _JobHarness([row], {1: _evidence("P", topics=["Monetary Policy"])},
                     outcomes=[ClassificationOutcome([], ok=False)]) as h:
        stats = h.run()
    assert stats["failed"] == 1
    h.m["save_codes"].assert_not_called()
    assert h.m["save_state"].call_args.kwargs["attempts"] == 3


def test_failure_fills_gap_with_topic_codes():
    with _JobHarness([_row(1)], {1: _evidence("P", topics=["Monetary Policy"])},
                     outcomes=[ClassificationOutcome([], ok=False)]) as h:
        h.run()
    h.m["save_codes"].assert_called_once_with(1, ["E"])
    assert h.m["save_state"].call_args.kwargs["attempts"] == 1


def test_consecutive_failures_stop_run():
    rows = [_row(i) for i in range(1, 9)]
    inputs = {i: _evidence(f"P{i}") for i in range(1, 9)}
    with _JobHarness(rows, inputs, outcomes=[ClassificationOutcome([], ok=False)] * 8) as h:
        stats = h.run()
    assert stats["stopped"] == "failures"
    assert h.m["classify"].call_count == jel_job._MAX_CONSECUTIVE_FAILURES


def test_lock_held_skips_run():
    with patch.object(jel_job, "_acquire_lock", return_value=None), \
         patch.object(jel_job, "get_jel_classification_candidates") as cands:
        stats = jel_job.run_jel_classification_batch()
    assert stats["stopped"] == "locked"
    cands.assert_not_called()


def test_nothing_due():
    with _JobHarness([], {}) as h:
        stats = h.run()
    assert stats["stopped"] == "nothing_due"


def test_scheduler_job_swallows_errors():
    from backend.pipeline import scheduler
    with patch("backend.enrichment.jel_job.run_jel_classification_batch", side_effect=RuntimeError("db down")):
        assert scheduler.run_jel_classification_job() == {"stopped": "error"}


# ---- candidate query ----

def test_candidate_query_params_and_force():
    from datetime import datetime
    from backend.database import jel
    now = datetime(2026, 11, 27, 12, 0)
    with patch.object(jel, "fetch_all", return_value=[]) as fa:
        jel.get_jel_classification_candidates(50, now, 30)
        sql, params = fa.call_args.args
        assert sql.count("%s") == len(params)
        assert params == (now, now, 30, 50)
        assert "researcher_jel_state" in sql and "is_active = 1" in sql

        jel.get_jel_classification_candidates(5, now, researcher_ids=[7, 8], force=True)
        sql, params = fa.call_args.args
        assert sql.count("%s") == len(params)
        assert params == (7, 8, 5)
        assert "INTERVAL" not in sql


def test_inputs_cap_papers_and_attach_topics():
    from backend.database import jel
    paper_rows = [{"researcher_id": 1, "id": i, "title": f"T{i}", "abstract": None, "year": "2024"}
                  for i in range(30)]
    topic_rows = [{"paper_id": 0, "topic_name": "Trade", "score": 0.8}]
    with patch.object(jel, "fetch_all", side_effect=[paper_rows, topic_rows]):
        result = jel.get_jel_inputs_for_researchers([1, 2], max_papers=20)
    assert len(result[1]["papers"]) == 20
    assert result[1]["topics"] == [{"topic_name": "Trade", "score": 0.8}]
    assert result[2] == {"papers": [], "topics": []}


# ---- OpenAlex author topics ----

def test_fetch_author_topics_batch_parses_counts():
    from backend.enrichment import openalex
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"results": [
        {"id": "https://openalex.org/A1", "topics": [
            {"display_name": "Labor markets", "count": 20},
            {"display_name": "Trade", "count": 5},
        ]},
        {"id": "https://openalex.org/A2", "topics": []},
    ]}
    with patch.object(openalex, "_get_session") as session, \
         patch.object(openalex, "_check_budget", return_value=True), \
         patch.object(openalex, "_reserve_budget", return_value=True):
        session.return_value.get.return_value = resp
        result = openalex.fetch_author_topics_batch(["A1", "A2", "A1"])
    assert result == {"A1": [{"topic_name": "Labor markets", "score": 1.0},
                             {"topic_name": "Trade", "score": 0.25}]}
    params = session.return_value.get.call_args.kwargs.get("params") or session.return_value.get.call_args.args[1]
    assert params["filter"] == "openalex:https://openalex.org/A1|https://openalex.org/A2"


def test_fetch_author_topics_batch_respects_budget():
    from backend.enrichment import openalex
    with patch.object(openalex, "_check_budget", return_value=False), \
         patch.object(openalex, "_get_session") as session:
        assert openalex.fetch_author_topics_batch(["A1"]) == {}
    session.assert_not_called()
