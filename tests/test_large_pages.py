"""Large researcher pages: fetch-size handling, chunked extraction, merging.

Covers the two places big pages used to lose information: the 1 MB fetch cap
(pages rejected and deactivated) and the CONTENT_MAX_CHARS text cut (papers
at the bottom of long lists never extracted).
"""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")
os.environ.setdefault("CONTENT_MAX_CHARS", "20000")

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.pipeline import chunking
from backend.pipeline.chunking import Chunk, chunk_page, html_to_blocks, pack_blocks
from backend.pipeline.html_fetcher import (
    CONTENT_MAX_BYTES, FETCH_MAX_BYTES, HTMLFetcher, ResponseTooLarge, compact_html,
)
from backend.pipeline.publication import (
    ExtractionLLMResult, Publication, PublicationChange, PublicationChangeList,
    PublicationExtraction, PublicationExtractionList, merge_publications,
)

BUDGET = 20000


def _page(body: str) -> str:
    return f"<html><head><title>T</title></head><body><main>{body}</main></body></html>"


def _entry(i: int, pad: int = 0) -> str:
    extra = " lorem" * pad
    return (f"<li>Paper number {i} on trade and labor markets{extra}. "
            f"With Jane Coauthor{i}. Journal of Things {2000 + i % 25}.</li>")


def _text(html: str) -> str:
    return HTMLFetcher.normalize_text(HTMLFetcher.extract_text_content(html))


# ---------------------------------------------------------------------------
# Fetch side: compaction and size caps
# ---------------------------------------------------------------------------

class TestCompactHtml:
    def test_page_within_limit_is_returned_unchanged(self):
        html = _page("<style>p{color:red}</style><svg><path d='M0 0'/></svg><p>Hi</p>")
        assert compact_html(html) is html

    def test_strips_heavy_markup_without_changing_text(self):
        css = "<style>" + ".a{color:red}" * 50_000 + "</style>"
        svg = "<svg viewBox='0 0 1 1'><path d='" + "M0 0 L1 1 " * 20_000 + "'/></svg>"
        img = "<img src='data:image/png;base64," + "A" * 200_000 + "'>"
        attr = "<div data-current-styles='" + "{&quot;x&quot;:1}" * 20_000 + "'>Working Papers</div>"
        body = (css + svg + img + attr + "<!-- " + "c" * 100_000 + " -->"
                + "<p style='" + "color:red;" * 100 + "'><a href='https://x.org/p.pdf'>A Paper</a></p>")
        html = _page(body)
        assert len(html) > CONTENT_MAX_BYTES

        compacted = compact_html(html)

        assert len(compacted.encode()) < 10_000
        assert _text(compacted) == _text(html)
        assert "https://x.org/p.pdf" in compacted
        assert "data:," in compacted

    def test_scripts_kept_when_other_stripping_suffices(self):
        """Google Sites JSON in scripts feeds the feed-event snapshot guard."""
        html = _page("<style>" + "x" * 1_100_000 + "</style><script>var t='A Title';</script><p>Hi</p>")
        compacted = compact_html(html)
        assert "<script>var t='A Title';</script>" in compacted

    def test_scripts_dropped_when_still_too_large(self):
        html = _page("<script>" + "x" * 1_100_000 + "</script><noscript>no</noscript><p>Hi</p>")
        compacted = compact_html(html)
        assert "<script" not in compacted and "<noscript" not in compacted
        assert _text(compacted) == _text(html)

    def test_data_uri_strip_does_not_eat_following_text(self):
        html = _page("<p>data:image/png;base64,AAAA and the title</p>" + "<style>" + "x" * 1_100_000 + "</style>")
        assert "and the title" in _text(compact_html(html))


def _stream_response(body: bytes, headers=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = headers or {}
    resp.iter_content.return_value = iter([body[i:i + 65536] for i in range(0, len(body), 65536)])
    resp.apparent_encoding = "utf-8"
    type(resp).text = property(lambda self: self._content.decode("utf-8"))
    return resp


def _fetch(resp):
    session = MagicMock()
    session.get.return_value = resp
    with patch.object(HTMLFetcher, "_rate_limit"), \
         patch.object(HTMLFetcher, "_get_session", return_value=session):
        result = HTMLFetcher.fetch_html("https://example.com")
    return result, session


class TestFetchHtmlSizes:
    def test_streams_with_stream_true_and_closes_response(self):
        resp = _stream_response(b"<html><p>Hello</p></html>")
        result, session = _fetch(resp)
        assert result == "<html><p>Hello</p></html>"
        assert session.get.call_args.kwargs["stream"] is True
        resp.close.assert_called_once()

    def test_bloated_page_over_old_cap_is_compacted_and_accepted(self):
        """1.2 MB of inline CSS used to deactivate the URL; now it is stored."""
        html = _page("<style>" + ".a{b:c}" * 200_000 + "</style><ul>" + _entry(1) + "</ul>")
        assert len(html) > CONTENT_MAX_BYTES
        result, _ = _fetch(_stream_response(html.encode()))
        assert result is not None
        assert len(result.encode()) <= CONTENT_MAX_BYTES
        assert "Paper number 1" in result

    def test_declared_length_over_download_cap_rejected_without_reading(self):
        resp = _stream_response(b"", headers={"Content-Length": str(FETCH_MAX_BYTES + 1)})
        with pytest.raises(ResponseTooLarge):
            _fetch(resp)
        resp.iter_content.assert_not_called()
        resp.close.assert_called_once()

    def test_text_still_too_large_after_compaction_rejected(self):
        html = _page("<p>" + "word " * 250_000 + "</p>")
        with pytest.raises(ResponseTooLarge):
            _fetch(_stream_response(html.encode()))

    def test_curl_filesize_exceeded_raises_too_large(self):
        completed = SimpleNamespace(returncode=63, stdout=b"", stderr=b"Maximum file size exceeded")
        with patch("backend.pipeline.html_fetcher.subprocess.run", return_value=completed):
            with pytest.raises(ResponseTooLarge):
                HTMLFetcher._fetch_with_curl("https://example.com")

    def test_curl_body_is_compacted(self):
        html = _page("<style>" + "x" * 1_100_000 + "</style><p>Body</p>")
        completed = SimpleNamespace(returncode=0, stdout=html.encode(), stderr=b"")
        with patch("backend.pipeline.html_fetcher.subprocess.run", return_value=completed):
            result = HTMLFetcher._fetch_with_curl("https://example.com")
        assert "<style" not in result and "Body" in result


class TestFullTextStored:
    def test_text_beyond_content_max_chars_is_stored_and_hashed(self):
        html = _page("<ul>" + "".join(_entry(i) for i in range(400)) + "</ul>")
        full = _text(html)
        assert len(full) > BUDGET
        with patch.object(HTMLFetcher, "_was_fetched_recently", return_value=False), \
             patch.object(HTMLFetcher, "validate_url_with_pin", return_value=(True, "1.2.3.4")), \
             patch.object(HTMLFetcher, "is_allowed_by_robots", return_value=True), \
             patch.object(HTMLFetcher, "fetch_html", return_value=html), \
             patch("backend.pipeline.html_fetcher.record_url_fetch_success"), \
             patch.object(HTMLFetcher, "has_text_changed", return_value=True), \
             patch.object(HTMLFetcher, "save_text") as save:
            assert HTMLFetcher.fetch_and_save_if_changed(1, "https://example.com", 2)
        _, text, text_hash, _ = save.call_args.args[:4]
        assert text == full
        assert text_hash == HTMLFetcher.hash_text_content(full)
        assert "Paper number 399" in text


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

class TestChunkPage:
    def test_page_within_budget_is_one_unchanged_chunk(self):
        assert chunk_page("short text", "<p>ignored</p>", BUDGET) == [Chunk("short text")]

    def test_long_list_splits_between_entries_only(self):
        html = _page("<h2>Working Papers</h2><ul>" + "".join(_entry(i) for i in range(500)) + "</ul>")
        text = _text(html)
        chunks = chunk_page(text, html, BUDGET)

        assert len(chunks) > 1
        assert all(len(c.text) <= BUDGET for c in chunks)
        joined = " ".join(c.text for c in chunks)
        for i in range(500):
            entry = HTMLFetcher.normalize_text(re.sub(r"<[^>]+>", "", _entry(i)))
            assert sum(entry in c.text for c in chunks) == 1, i
        assert joined == text  # nothing lost, nothing reordered

    def test_chunks_carry_section_heading(self):
        html = _page(
            "<h2>Publications</h2><ul>" + "".join(_entry(i) for i in range(150)) + "</ul>"
            "<h2>Working Papers</h2><ul>" + "".join(_entry(i) for i in range(150, 450)) + "</ul>"
            "<h2>Teaching</h2><p>" + "Course notes. " * 1500 + "</p>")
        chunks = chunk_page(_text(html), html, BUDGET)
        mid_working = [c for c in chunks if "Paper number 400" in c.text][0]
        assert mid_working.section == "Working Papers"
        # The non-publication heading "Teaching" ends the Working Papers section.
        assert "Course notes" in chunks[-1].text and chunks[-1].section is None
        assert chunks[0].section is None

    def test_breaks_early_before_section_title_not_paper_title(self):
        papers = "".join(f"<h3>Paper title {i} about things</h3><p>Abstract {'x' * 300}</p>" for i in range(40))
        html = _page("<h2>Working Papers</h2>" + papers + "<h2>Work in Progress</h2>" + papers)
        chunks = chunk_page(_text(html), html, BUDGET)
        assert any(c.text.startswith("Work in Progress") for c in chunks)
        # Paper-title headings never become the section context.
        assert all(c.section in (None, "Working Papers", "Work in Progress") for c in chunks)

    def test_bold_paragraph_counts_as_section_title(self):
        html = _page("<p><b>Working Papers</b></p><ul>" + "".join(_entry(i) for i in range(400)) + "</ul>")
        chunks = chunk_page(_text(html), html, BUDGET)
        assert chunks[-1].section == "Working Papers"

    def test_oversized_block_splits_between_lines_with_overlap(self):
        lines = [f"Title {i} of a long paper. Authors {i}. Venue {i}." for i in range(1200)]
        html = _page("<p>" + "<br>".join(lines) + "</p>")
        chunks = chunk_page(_text(html), html, BUDGET)
        assert len(chunks) > 1
        assert all(len(c.text) <= BUDGET for c in chunks)
        for line in lines:
            assert any(line in c.text for c in chunks), line
        # The seam repeats the last lines of the previous piece.
        tail = chunks[0].text[-200:].split(". ")[-1]
        assert tail in chunks[1].text

    def test_single_huge_line_is_hard_split(self):
        line = " ".join(f"w{i}" for i in range(20000))
        chunks = chunk_page(line, None, BUDGET)
        assert all(len(c.text) <= BUDGET for c in chunks)
        words = set(" ".join(c.text for c in chunks).split())
        assert words == set(line.split())

    def test_text_only_page_splits_on_sentences(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(2000))
        chunks = chunk_page(text, None, BUDGET)
        assert len(chunks) > 1 and all(len(c.text) <= BUDGET for c in chunks)
        assert " ".join(c.text for c in chunks) == text

    def test_chunk_limit_drops_and_logs_the_rest(self, caplog):
        html = _page("<ul>" + "".join(_entry(i, pad=50) for i in range(400)) + "</ul>")
        with caplog.at_level("WARNING"):
            chunks = chunk_page(_text(html), html, BUDGET, max_chunks=2)
        assert len(chunks) == 2
        assert "extraction chunks" in caplog.text

    def test_blocks_exclude_site_chrome(self):
        html = ("<html><body><nav>Home Research CV</nav><main><p>Paper A</p></main>"
                "<footer>Copyright</footer><script>x</script></body></html>")
        assert [b.text for b in html_to_blocks(html)] == ["Paper A"]

    def test_pack_blocks_empty(self):
        assert pack_blocks([], BUDGET) == []


# ---------------------------------------------------------------------------
# Extraction and merging
# ---------------------------------------------------------------------------

def _pub(title, **kw):
    base = dict(title=title, authors=[["Jane", "Doe"]], year=None, venue=None, status=None,
                draft_url=None, abstract=None, status_evidence=None, authors_evidence=None)
    base.update(kw)
    return base


class TestMergePublications:
    def test_duplicate_titles_merge_to_fuller_record_in_page_order(self):
        pubs = [_pub("Trade and Jobs"), _pub("Other Paper"),
                _pub("Trade and jobs.", year="2020", venue="AER")]
        merged = merge_publications(pubs)
        assert [p["title"] for p in merged] == ["Trade and jobs.", "Other Paper"]
        assert merged[0]["venue"] == "AER"

    def test_missing_fields_filled_but_status_keeps_its_evidence(self):
        a = _pub("Trade and Jobs", year="2020", venue="AER", abstract="Long abstract")
        b = _pub("Trade and Jobs", status="revise_and_resubmit", status_evidence="Trade and Jobs R&R")
        merged = merge_publications([a, b])
        assert len(merged) == 1
        assert merged[0]["abstract"] == "Long abstract"
        assert merged[0]["status"] == "revise_and_resubmit"
        assert merged[0]["status_evidence"] == "Trade and Jobs R&R"

    def test_key_fields_keep_change_types_apart(self):
        changes = [dict(_pub("Paper"), change_type="new_paper"),
                   dict(_pub("Paper"), change_type="status_change")]
        assert len(merge_publications(changes, key_fields=("change_type",))) == 2


def _llm_result(titles):
    pubs = [PublicationExtraction(title=t, authors=[["Jane", "Doe"]], year="2020",
                                  venue=None, status="working_paper", draft_url=None, abstract=None)
            for t in titles]
    return SimpleNamespace(parsed=PublicationExtractionList(publications=pubs), usage=None, retry_after=None, truncated=False)


class TestChunkedExtraction:
    def _chunks(self):
        return [Chunk("Working Papers Alpha Paper on Trade. Beta Paper on Labor.", None),
                Chunk("Beta Paper on Labor. Gamma Paper on Growth.", "Working Papers")]

    def test_one_call_per_chunk_merged_and_paced(self):
        results = [_llm_result(["Alpha Paper on Trade", "Beta Paper on Labor"]),
                   _llm_result(["Beta Paper on Labor", "Gamma Paper on Growth"])]
        with patch("backend.pipeline.publication.extract_json", side_effect=results) as ej, \
             patch("backend.pipeline.publication.time.sleep") as sleep, \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_publications("unused", "https://x.org", chunks=self._chunks())

        assert ej.call_count == 2
        assert sleep.call_count == 1
        assert [p["title"] for p in result.pubs] == [
            "Alpha Paper on Trade", "Beta Paper on Labor", "Gamma Paper on Growth"]
        second_prompt = ej.call_args_list[1].args[0]
        assert "This is part 2 of 2" in second_prompt
        assert '"Working Papers"' in second_prompt

    def test_titles_verified_against_their_own_chunk(self):
        """A title the model invents from another part of the page is dropped."""
        results = [_llm_result(["Alpha Paper on Trade"]), _llm_result(["Alpha Paper on Trade Again"])]
        with patch("backend.pipeline.publication.extract_json", side_effect=results), \
             patch("backend.pipeline.publication.time.sleep"), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_publications("unused", "https://x.org", chunks=self._chunks())
        assert [p["title"] for p in result.pubs] == ["Alpha Paper on Trade"]

    def test_failed_chunk_fails_page(self):
        failed = SimpleNamespace(parsed=None, usage=None, retry_after=None, truncated=False)
        with patch("backend.pipeline.publication.extract_json",
                   side_effect=[_llm_result(["Alpha Paper on Trade"]), failed]), \
             patch("backend.pipeline.publication.time.sleep"), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_publications("unused", "https://x.org", chunks=self._chunks())
        assert result.pubs is None

    def test_rate_limited_chunk_is_retried_once_in_page(self):
        limited = SimpleNamespace(parsed=None, usage=None, retry_after=11.0, truncated=False)
        results = [_llm_result(["Alpha Paper on Trade"]), limited, _llm_result(["Gamma Paper on Growth"])]
        with patch("backend.pipeline.publication.extract_json", side_effect=results) as ej, \
             patch("backend.pipeline.publication.time.sleep") as sleep, \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_publications("unused", "https://x.org", chunks=self._chunks())
        assert ej.call_count == 3
        assert 11.0 in [c.args[0] for c in sleep.call_args_list]
        assert [p["title"] for p in result.pubs] == ["Alpha Paper on Trade", "Gamma Paper on Growth"]

    def test_long_rate_limit_fails_page_for_worker_backoff(self):
        limited = SimpleNamespace(parsed=None, usage=None, retry_after=600.0, truncated=False)
        with patch("backend.pipeline.publication.extract_json",
                   side_effect=[_llm_result(["Alpha Paper on Trade"]), limited]) as ej, \
             patch("backend.pipeline.publication.time.sleep"), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_publications("unused", "https://x.org", chunks=self._chunks())
        assert ej.call_count == 2
        assert result.pubs is None and result.retry_after == 600.0

    def test_single_chunk_prompt_is_unchanged(self):
        prompt = Publication.build_extraction_prompt("Some text", "https://x.org")
        assert "part" not in prompt.split("For each publication")[0]
        with patch("backend.pipeline.publication.extract_json", return_value=_llm_result([])) as ej, \
             patch("backend.pipeline.publication.log_llm_usage"):
            Publication.try_extract_publications("Some text", "https://x.org", chunks=[Chunk("Some text")])
        assert ej.call_args.args[0] == prompt


class TestDiffOnLongPages:
    def _long(self, n, changed=None):
        entries = [f"Paper number {i} on trade and labor markets. With Jane Coauthor{i}." for i in range(n)]
        if changed is not None:
            entries[changed] = f"Brand new paper {changed} about growth. With New Coauthor."
        return " ".join(entries)

    def test_short_pages_keep_single_legacy_prompt(self):
        old, new = "Paper A. Paper B.", "Paper A. Paper B. Paper C."
        assert Publication._diff_prompts(old, new, "u") == [
            Publication.build_diff_extraction_prompt(old, new, "u")]

    def test_long_page_sends_only_changed_sections(self):
        old, new = self._long(600), self._long(600, changed=599)
        prompts = Publication._diff_prompts(old, new, "u")
        assert len(prompts) == 1
        assert "Brand new paper 599" in prompts[0]
        assert len(prompts[0]) < 20000

    def test_long_rewrite_requests_full_extraction(self):
        old = self._long(600)
        new = " ".join(f"Completely different entry {i} here." for i in range(1500))
        assert Publication._diff_prompts(old, new, "u") is None
        with patch("backend.pipeline.publication.extract_json") as ej:
            result = Publication.try_extract_changes(old, new, "u")
        assert result.needs_full_extraction and result.pubs is None
        ej.assert_not_called()

    def test_large_diff_split_into_parts_within_budget(self):
        hunks = "".join(f"@@ -{i},1 +{i},1 @@\n-old line {i} {'x' * 300}\n+new line {i} {'y' * 300}\n"
                        for i in range(200))
        diff = "--- OLD\n+++ NEW\n" + hunks
        parts = Publication._split_diff(diff, 20000)
        assert len(parts) > 1
        assert all(len(p) <= 20000 and p.startswith("--- OLD\n+++ NEW\n") for p in parts)
        body = "".join(p[len("--- OLD\n+++ NEW\n"):] for p in parts)
        assert body == hunks

    def test_over_long_diff_line_is_split_not_cut(self):
        diff = "--- OLD\n+++ NEW\n@@ -1 +1 @@\n+" + "z" * 50_000 + "\n"
        parts = Publication._split_diff(diff, 20000)
        assert all(len(p) <= 20000 for p in parts)
        assert sum(p.count("z") for p in parts) == 50_000
        assert all("\n@@ -1 +1 @@\n+" in p for p in parts)

    def test_multi_part_diff_results_are_merged(self):
        change = PublicationChange(change_type="new_paper", title="Brand new paper about growth",
                                   authors=[["New", "Coauthor"]], year=None, venue=None,
                                   status="working_paper", draft_url=None, abstract=None)
        reply = SimpleNamespace(parsed=PublicationChangeList(changes=[change]), usage=None, retry_after=None, truncated=False)
        with patch.object(Publication, "_diff_prompts", return_value=["p1", "p2"]), \
             patch("backend.pipeline.publication.extract_json", side_effect=[reply, reply]) as ej, \
             patch("backend.pipeline.publication.time.sleep"):
            result = Publication.try_extract_changes(
                "old", "Brand new paper about growth. With New Coauthor.", "u")
        assert ej.call_count == 2
        assert len(result.pubs) == 1


def _extraction_patches(text, previous_text, raw_html):
    payload = {"content": None, "raw_html": raw_html, "content_hash": "h1",
               "timestamp": None, "extracted_at": "2026-01-01"}
    return {
        "payload": patch("backend.pipeline.extraction.HTMLFetcher.get_extraction_payload", return_value=payload),
        "prev": patch("backend.pipeline.extraction.HTMLFetcher.get_previous_text", return_value=previous_text),
        "mark": patch("backend.pipeline.extraction.HTMLFetcher.mark_extracted"),
        "persist": patch("backend.pipeline.extraction.persist_extraction"),
    }


class TestExtractOneUrlLongPages:
    HTML = _page("<h2>Working Papers</h2><ul>" + "".join(_entry(i) for i in range(500)) + "</ul>")

    def _run(self, previous_text, try_extract=None, try_changes=None):
        from backend.pipeline.extraction import extract_one_url
        patches = _extraction_patches(_text(self.HTML), previous_text, self.HTML)
        patches["try_extract"] = patch(
            "backend.pipeline.extraction.Publication.try_extract_publications",
            return_value=try_extract or ExtractionLLMResult(pubs=[_pub("Paper number 499 on trade")]))
        patches["try_changes"] = patch(
            "backend.pipeline.extraction.Publication.try_extract_changes",
            return_value=try_changes or ExtractionLLMResult(pubs=[]))
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url({"id": 1, "researcher_id": 2, "url": "https://x.org", "page_type": "PUBLICATIONS"})
        finally:
            for p in patches.values():
                p.stop()
        return outcome, mocks

    def test_unchanged_long_page_is_backfilled_as_seed_in_chunks(self):
        """The hash of a long page changes once (full text is now hashed);
        its never-extracted tail is saved without feed events."""
        outcome, mocks = self._run(previous_text=_text(self.HTML))
        assert outcome.status == "extracted"
        chunks = mocks["try_extract"].call_args.kwargs["chunks"]
        assert len(chunks) > 1
        assert any("Paper number 499" in c.text for c in chunks)
        assert mocks["persist"].call_args.kwargs["is_seed"] is True
        mocks["try_changes"].assert_not_called()
        mocks["mark"].assert_called_once_with(1, "h1")

    def test_first_extraction_of_long_page_is_not_truncated(self):
        outcome, mocks = self._run(previous_text=None)
        text_arg = mocks["try_extract"].call_args.args[0]
        assert "Paper number 499" in text_arg
        assert mocks["persist"].call_args.kwargs["is_seed"] is False

    def test_long_rewrite_falls_back_to_non_seed_chunked_extraction(self):
        outcome, mocks = self._run(
            previous_text="An entirely different old page.",
            try_changes=ExtractionLLMResult(pubs=None, needs_full_extraction=True))
        assert outcome.status == "extracted"
        assert len(mocks["try_extract"].call_args.kwargs["chunks"]) > 1
        assert mocks["persist"].call_args.kwargs["is_seed"] is False

    def test_unchanged_short_page_still_skips_llm(self):
        from backend.pipeline.extraction import extract_one_url
        html = _page("<p>Short page</p>")
        patches = _extraction_patches(_text(html), _text(html), html)
        patches["try_extract"] = patch("backend.pipeline.extraction.Publication.try_extract_publications")
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url({"id": 1, "researcher_id": 2, "url": "https://x.org", "page_type": "PUBLICATIONS"})
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "empty"
        mocks["try_extract"].assert_not_called()


def test_chunk_limit_default_covers_sampled_pages():
    """The longest sampled page (141k chars) needs 8 chunks."""
    assert chunking.EXTRACTION_MAX_CHUNKS >= 8


# ---------------------------------------------------------------------------
# Output cut off at max_tokens (audit: pages retried 50-60 times)
# ---------------------------------------------------------------------------

_CUT_OFF = SimpleNamespace(parsed=None, usage=None, retry_after=None, truncated=True)


class TestTruncatedOutput:
    TEXT = " ".join(f"Paper title number {i} about growth and trade." for i in range(200))

    def test_cut_off_page_is_split_and_merged(self):
        results = [_CUT_OFF, _llm_result(["Paper title number 1 about growth and trade"]),
                   _llm_result(["Paper title number 150 about growth and trade"])]
        with patch("backend.pipeline.publication.extract_json", side_effect=results) as ej, \
             patch("backend.pipeline.publication.time.sleep"), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_publications(self.TEXT, "https://x.org")
        assert ej.call_count == 3
        halves = [c.args[0].split("Content:\n", 1)[1] for c in ej.call_args_list[1:]]
        assert all(len(h) < len(self.TEXT) * 0.6 for h in halves)
        assert " ".join(halves) == self.TEXT
        assert [p["title"] for p in result.pubs] == [
            "Paper title number 1 about growth and trade", "Paper title number 150 about growth and trade"]

    def test_splitting_stops_after_two_levels(self):
        with patch("backend.pipeline.publication.extract_json", return_value=_CUT_OFF) as ej, \
             patch("backend.pipeline.publication.time.sleep"), \
             patch("backend.pipeline.publication.log_llm_usage"):
            result = Publication.try_extract_publications(self.TEXT, "https://x.org")
        assert result.pubs is None and result.truncated
        assert ej.call_count == 3  # whole page, first half (cut off), its first quarter (cut off)

    def test_diff_uses_full_output_budget(self):
        reply = SimpleNamespace(parsed=PublicationChangeList(changes=[]), usage=None,
                                retry_after=None, truncated=False)
        with patch("backend.pipeline.publication.extract_json", return_value=reply) as ej:
            Publication.try_extract_changes("Paper A.", "Paper A. Paper B.", "u")
        assert ej.call_args.kwargs["max_tokens"] == 32000

    def test_cut_off_diff_falls_back_to_full_extraction(self):
        with patch("backend.pipeline.publication.extract_json", return_value=_CUT_OFF) as ej:
            result = Publication.try_extract_changes("Paper A.", "Paper A. Paper B.", "u")
        assert result.needs_full_extraction and result.pubs is None
        assert ej.call_count == 1

    def test_extract_one_url_runs_full_extraction_after_cut_off_diff(self):
        from backend.pipeline.extraction import extract_one_url
        html = _page("<p>Paper A. Paper B.</p>")
        patches = _extraction_patches(_text(html), "Paper A.", html)
        patches["changes"] = patch("backend.pipeline.extraction.Publication.try_extract_changes",
                                   return_value=ExtractionLLMResult(pubs=None, needs_full_extraction=True))
        patches["full"] = patch("backend.pipeline.extraction.Publication.try_extract_publications",
                                return_value=ExtractionLLMResult(pubs=[_pub("Paper B")]))
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            outcome = extract_one_url({"id": 1, "researcher_id": 2, "url": "https://x.org",
                                       "page_type": "PUBLICATIONS"})
        finally:
            for p in patches.values():
                p.stop()
        assert outcome.status == "extracted"
        mocks["full"].assert_called_once()
        assert mocks["persist"].call_args.kwargs["is_seed"] is False


# ---------------------------------------------------------------------------
# Persisted extraction poison-pill guard
# ---------------------------------------------------------------------------

class TestPersistedExtractionFailures:
    def test_failure_count_is_per_version_and_cooldown_escalates(self):
        from backend.database import researchers as r
        with patch.object(r, "execute_query") as ex, patch.object(r, "fetch_one", return_value=None):
            r.record_extraction_failure(7, "hash1")
        sql, params = ex.call_args.args
        flat = " ".join(sql.split())
        assert "extraction_failures = IF(extraction_failed_hash <=> %s, extraction_failures + 1, 1)" in flat
        assert "POW(2, extraction_failures - %s)" in flat
        # The count is assigned before retry_at reads it (MySQL SET is left to right).
        assert flat.index("extraction_failures = IF") < flat.index("extraction_retry_at = IF")
        assert params == ("hash1", "hash1", r.EXTRACTION_MAX_FAILURES, r.EXTRACTION_COOLDOWN_SECONDS,
                          r.EXTRACTION_MAX_FAILURES, r.EXTRACTION_MAX_COOLDOWN_SECONDS, 7)

    def test_queue_skips_cooled_down_version_only(self):
        from backend.database import researchers as r
        with patch.object(r, "fetch_all", return_value=[]) as fa:
            r.get_urls_needing_extraction()
        flat = " ".join(fa.call_args.args[0].split())
        assert ("(hc.extraction_retry_at IS NULL OR hc.extraction_retry_at <= NOW() "
                "OR NOT (hc.extraction_failed_hash <=> hc.content_hash))") in flat

    @pytest.mark.parametrize("extracted_hash", [None, "h1"])
    def test_successful_extraction_clears_failure_state(self, extracted_hash):
        with patch("backend.pipeline.html_fetcher.execute_query") as ex:
            HTMLFetcher.mark_extracted(1, extracted_hash)
        sql = ex.call_args.args[0]
        for clause in ("extraction_failures = 0", "extraction_failed_hash = NULL", "extraction_retry_at = NULL"):
            assert clause in sql

    def test_worker_persists_page_failures_not_rate_limits(self):
        from backend.pipeline import scheduler
        from backend.pipeline.extraction import ExtractionOutcome
        outcomes = [ExtractionOutcome("failed"), ExtractionOutcome("failed", retry_after=30)]
        scheduler._extraction_stop_event.clear()

        def extract(row):
            outcome = outcomes.pop(0)
            if not outcomes:
                scheduler._extraction_stop_event.set()
            return outcome

        rows = [{"id": 1, "url": "https://x.org/1", "content_hash": "h1"},
                {"id": 2, "url": "https://x.org/2", "content_hash": "h2"}]
        try:
            with patch.object(scheduler, "get_urls_needing_extraction", return_value=rows), \
                 patch("backend.pipeline.extraction.extract_one_url", side_effect=extract), \
                 patch.object(scheduler, "record_extraction_failure", return_value=None) as rec, \
                 patch.object(scheduler._extraction_stop_event, "wait"):
                scheduler._extraction_worker_loop()
        finally:
            scheduler._extraction_stop_event.clear()
        rec.assert_called_once_with(1, "h1")

    def test_worker_survives_failure_persistence_errors(self):
        from backend.pipeline import scheduler
        with patch.object(scheduler, "record_extraction_failure", side_effect=RuntimeError("db down")):
            scheduler._persist_extraction_failure({"id": 1, "url": "u", "content_hash": "h"})

    def test_ready_check_requires_failure_columns(self):
        from backend.database.schema import REQUIRED_SCHEMA_QUERY
        assert "extraction_retry_at" in REQUIRED_SCHEMA_QUERY
