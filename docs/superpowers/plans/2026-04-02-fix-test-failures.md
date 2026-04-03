# Fix 6 Pre-Existing Test Failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 failing tests across 3 test files caused by stale mocks and missing test updates after production code changes.

**Architecture:** Three independent fixes — update snapshot hash tests to match title inclusion, fix mock return type in link validation test, add missing mock for `reconcile_title_renames` in seed detection tests.

**Tech Stack:** Python, pytest, unittest.mock

---

### Task 1: Fix snapshot hash tests (2 tests)

**Root cause:** Commit `07eb997` added `title` to `_compute_paper_content_hash` but didn't update the tests. The code now hashes `(title, status, venue, abstract, draft_url, year)` but two tests still expect the old 5-field hash.

**Files:**
- Modify: `tests/test_snapshots.py:116-125` (test_matches_manual_sha256)
- Modify: `tests/test_snapshots.py:157-161` (test_none_values_handled)

- [ ] **Step 1: Update `test_matches_manual_sha256` to include title=None in expected hash**

The test calls `_compute_paper_content_hash(status, venue, abstract, draft_url, year)` without `title`, so `title` defaults to `None`. The code hashes `(None, status, venue, ...)`. Update the expected hash and docstring to match:

```python
    def test_matches_manual_sha256(self):
        """Must equal SHA-256('title||status||venue||abstract||draft_url||year')."""
        status, venue, abstract, draft_url, year = (
            "published", "AER", "The abstract.", "https://ssrn.com/3", "2022"
        )
        expected = _sha256(None, status, venue, abstract, draft_url, year)
        assert (
            Database._compute_paper_content_hash(status, venue, abstract, draft_url, year)
            == expected
        )
```

- [ ] **Step 2: Update `test_none_values_handled` to include 6 fields in expected hash**

The test calls with 5 `None` args (title defaults to `None` too = 6 None fields in hash), but expected hash only has 5 `None` fields:

```python
    def test_none_values_handled(self):
        """All-None input must not raise."""
        h = Database._compute_paper_content_hash(None, None, None, None, None)
        expected = _sha256(None, None, None, None, None, None)
        assert h == expected
```

- [ ] **Step 3: Run tests to verify both pass**

Run: `poetry run pytest tests/test_snapshots.py::TestPaperContentHash -v`
Expected: 13 passed, 0 failed

- [ ] **Step 4: Commit**

```bash
git add tests/test_snapshots.py
git commit -m "fix(tests): update paper hash tests to include title field

The title field was added to _compute_paper_content_hash in 07eb997 but
the test expectations were not updated. Align expected hashes with the
6-field tuple (title, status, venue, abstract, draft_url, year).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Fix link validation mock return type (1 test)

**Root cause:** `validate_url` returns `bool`, but the test mocks it with `return_value=(False, None)`. A tuple `(False, None)` is truthy in Python, so `if not HTMLFetcher.validate_url(url)` doesn't trigger. The request proceeds to the real HTTP call, which times out on `192.168.1.1`.

**Files:**
- Modify: `tests/test_link_validation.py:107` (test_private_ip_url_returns_invalid)
- Modify: `tests/test_link_validation.py:112` (test_localhost_url_returns_invalid — wrong mock but passes by coincidence)
- Modify: `tests/test_link_validation.py:117` (test_non_http_scheme_returns_invalid — wrong mock but passes by coincidence)
- Modify: `tests/test_link_validation.py:130-135` (_patch_for_valid_ssrf helper)

- [ ] **Step 1: Fix all three SSRF test mocks to return `bool`**

Change `return_value=(False, None)` → `return_value=False` on lines 107, 112, 117:

```python
    def test_private_ip_url_returns_invalid(self):
        """SSRF-blocked URLs (private IP) must return 'invalid' without making HTTP call."""
        with patch("html_fetcher.HTMLFetcher.validate_url", return_value=False):
            result = HTMLFetcher.validate_draft_url("http://192.168.1.1/paper.pdf")
        assert result == "invalid"

    def test_localhost_url_returns_invalid(self):
        with patch("html_fetcher.HTMLFetcher.validate_url", return_value=False):
            result = HTMLFetcher.validate_draft_url("http://localhost/paper.pdf")
        assert result == "invalid"

    def test_non_http_scheme_returns_invalid(self):
        with patch("html_fetcher.HTMLFetcher.validate_url", return_value=False):
            result = HTMLFetcher.validate_draft_url("ftp://example.com/paper.pdf")
        assert result == "invalid"
```

- [ ] **Step 2: Fix `_patch_for_valid_ssrf` helper to return `bool`**

Change `return_value=(True, resolved_ip)` → `return_value=True` on line 132-134:

```python
    def _patch_for_valid_ssrf(self, resolved_ip="1.2.3.4"):
        """Context manager: SSRF validation passes."""
        return patch(
            "html_fetcher.HTMLFetcher.validate_url",
            return_value=True,
        )
```

Note: the `resolved_ip` parameter becomes unused. Remove it from the signature and update call sites. Check if any callers pass a custom `resolved_ip`:

Run: `grep -n "patch_for_valid_ssrf" tests/test_link_validation.py`

If no callers pass custom `resolved_ip`, simplify to:

```python
    def _patch_for_valid_ssrf(self):
        """Context manager: SSRF validation passes."""
        return patch(
            "html_fetcher.HTMLFetcher.validate_url",
            return_value=True,
        )
```

- [ ] **Step 3: Run tests to verify all pass**

Run: `poetry run pytest tests/test_link_validation.py::TestValidateDraftUrl -v`
Expected: All tests pass (no more 10s timeout on private IP test)

- [ ] **Step 4: Commit**

```bash
git add tests/test_link_validation.py
git commit -m "fix(tests): correct validate_url mock return type from tuple to bool

validate_url returns bool, not (bool, ip) tuple. The tuple (False, None)
is truthy in Python, so the SSRF guard was bypassed and the private IP
test hit a real network call that timed out.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Fix seed detection tests — add missing `reconcile_title_renames` mock (3 tests)

**Root cause:** `reconcile_title_renames` was added to `extract_data_from_htmls` (line 58) and `batch_check` (line 306) after these tests were written. Without a mock, it calls `Database.fetch_all` which tries to connect to MySQL. The `extract_data_from_htmls` function has no try/except around this call, so it propagates. The `batch_check` test's global `Database.fetch_all` mock returns batch data (no `title` key), causing a `KeyError`.

**Files:**
- Modify: `tests/test_feed_seed_detection.py:23` (TestExtractDataSeedDetection::test_first_extraction_passes_is_seed_true)
- Modify: `tests/test_feed_seed_detection.py:47` (TestExtractDataSeedDetection::test_subsequent_extraction_passes_is_seed_false)
- Modify: `tests/test_feed_seed_detection.py:97` (TestBatchCheckSeedDetection::test_batch_check_first_extraction_passes_is_seed_true)

- [ ] **Step 1: Add `@patch("main.reconcile_title_renames")` to both `TestExtractDataSeedDetection` tests**

Add the decorator to `test_first_extraction_passes_is_seed_true` and update its parameter list:

```python
    @patch("main.reconcile_title_renames")
    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction")
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    @patch("main.Researcher.get_all_researcher_urls")
    def test_first_extraction_passes_is_seed_true(
        self, mock_urls, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links, mock_reconcile,
    ):
        mock_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "https://example.com", "page_type": "personal"}
        ]
        mock_first.return_value = True

        from main import extract_data_from_htmls
        extract_data_from_htmls()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is True
```

Do the same for `test_subsequent_extraction_passes_is_seed_false`:

```python
    @patch("main.reconcile_title_renames")
    @patch("main.match_and_save_paper_links")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.Publication.save_publications")
    @patch("main.Publication.extract_publications", return_value=[{"title": "Paper"}])
    @patch("main.HTMLFetcher.get_latest_text", return_value="<html>content</html>")
    @patch("main.HTMLFetcher.is_first_extraction")
    @patch("main.HTMLFetcher.needs_extraction", return_value=True)
    @patch("main.Researcher.get_all_researcher_urls")
    def test_subsequent_extraction_passes_is_seed_false(
        self, mock_urls, mock_needs, mock_first, mock_text,
        mock_extract, mock_save, mock_mark, mock_links, mock_reconcile,
    ):
        mock_urls.return_value = [
            {"id": 1, "researcher_id": 10, "url": "https://example.com", "page_type": "personal"}
        ]
        mock_first.return_value = False

        from main import extract_data_from_htmls
        extract_data_from_htmls()

        mock_save.assert_called_once()
        _, kwargs = mock_save.call_args
        assert kwargs.get("is_seed") is False
```

- [ ] **Step 2: Add `@patch("main.reconcile_title_renames")` to `TestBatchCheckSeedDetection`**

```python
    @patch("main.reconcile_title_renames")
    @patch("main.HTMLFetcher.mark_extracted")
    @patch("main.match_and_save_paper_links")
    @patch("main.Publication.save_publications")
    @patch("main.HTMLFetcher.is_first_extraction", return_value=True)
    @patch("main.Database.log_llm_usage")
    @patch("main.Database.execute_query")
    @patch("main.Database.fetch_one")
    @patch("main.Database.fetch_all")
    def test_batch_check_first_extraction_passes_is_seed_true(
        self, mock_fetch_all, mock_fetch_one, mock_exec, mock_log,
        mock_first, mock_save, mock_links, mock_mark, mock_reconcile,
    ):
```

The rest of the test body stays the same.

- [ ] **Step 3: Run tests to verify all pass**

Run: `poetry run pytest tests/test_feed_seed_detection.py -v`
Expected: 4 passed, 0 failed

- [ ] **Step 4: Commit**

```bash
git add tests/test_feed_seed_detection.py
git commit -m "fix(tests): mock reconcile_title_renames in seed detection tests

reconcile_title_renames was added to extract_data_from_htmls and
batch_check after these tests were written. Without a mock it attempts
a real DB connection (extract_data) or hits a KeyError on the mocked
fetch_all return value (batch_check).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Full test suite verification

- [ ] **Step 1: Run full test suite**

Run: `poetry run pytest -v`
Expected: 660 passed, 0 failed

- [ ] **Step 2: Final commit (if any cleanup needed)**

No additional commit needed unless Task 4 reveals regressions.
