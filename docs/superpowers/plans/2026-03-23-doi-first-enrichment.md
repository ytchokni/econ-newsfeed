# DOI-First Enrichment Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fuzzy OpenAlex title search with deterministic DOI resolution from publisher URLs, improving enrichment accuracy from ~50% to ~100% and coverage from 3% to 30%+.

**Architecture:** New `doi_resolver.py` extracts DOIs from publisher URLs via regex/Crossref. Enhanced `link_extractor.py` uses canonical titles from DOI lookups for matching. Updated `openalex.py` adds DOI-based lookup and author ID matching. Schema migrations add `doi` to `paper_links` and `openalex_author_id` to `researchers`.

**Tech Stack:** Python, requests, Crossref API, OpenAlex API, MySQL

**Pre-existing test failures:** 5 failures in `tests/test_api_search.py` — unrelated, ignore.

---

### Task 1: DOI Resolver — URL Identifier Extraction

**Files:**
- Create: `doi_resolver.py`
- Create: `tests/test_doi_resolver.py`

- [ ] **Step 1: Write failing tests for DOI extraction from URLs**

```python
# tests/test_doi_resolver.py
"""Tests for DOI resolution from publisher URLs."""
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from doi_resolver import extract_doi_from_url


class TestExtractDoiFromUrl:
    """Pure regex extraction — no API calls."""

    def test_doi_in_springer_path(self):
        assert extract_doi_from_url(
            "https://link.springer.com/article/10.1007/s40641-016-0032-z"
        ) == "10.1007/s40641-016-0032-z"

    def test_doi_in_aea_query(self):
        assert extract_doi_from_url(
            "https://www.aeaweb.org/articles?id=10.1257/aer.20250278"
        ) == "10.1257/aer.20250278"

    def test_doi_in_uchicago_path(self):
        assert extract_doi_from_url(
            "https://www.journals.uchicago.edu/doi/10.1086/713733"
        ) == "10.1086/713733"

    def test_doi_org_direct(self):
        assert extract_doi_from_url(
            "https://doi.org/10.1093/qje/qjac020"
        ) == "10.1093/qje/qjac020"

    def test_doi_in_wiley_path(self):
        assert extract_doi_from_url(
            "https://onlinelibrary.wiley.com/doi/10.1111/ecpo.12149"
        ) == "10.1111/ecpo.12149"

    def test_pii_not_extracted_as_doi(self):
        """PII URLs should NOT return a DOI — they need Crossref resolution."""
        assert extract_doi_from_url(
            "https://www.sciencedirect.com/science/article/pii/S0959378013002410"
        ) is None

    def test_no_doi_in_oup_path(self):
        assert extract_doi_from_url(
            "https://academic.oup.com/restud/article/83/1/87/2461318"
        ) is None

    def test_no_doi_in_jstor(self):
        assert extract_doi_from_url(
            "https://www.jstor.org/stable/41969212"
        ) is None

    def test_strips_query_params(self):
        assert extract_doi_from_url(
            "https://doi.org/10.1016/j.jebo.2024.106753?via=ihub"
        ) == "10.1016/j.jebo.2024.106753"

    def test_strips_trailing_slash(self):
        assert extract_doi_from_url(
            "https://link.springer.com/article/10.1007/s40641-016-0032-z/"
        ) == "10.1007/s40641-016-0032-z"

    def test_none_for_non_article_url(self):
        assert extract_doi_from_url("https://ssrn.com/abstract=12345") is None

    def test_none_for_empty_string(self):
        assert extract_doi_from_url("") is None

    def test_strips_doi_from_wiley_asset_url(self):
        """Wiley supplementary material URLs contain DOI but aren't articles."""
        result = extract_doi_from_url(
            "https://onlinelibrary.wiley.com/store/10.1111/jeea.12174/asset/supinfo/jeea12174-sup-0001-SuppMat.zip"
        )
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_doi_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'doi_resolver'`

- [ ] **Step 3: Implement extract_doi_from_url**

```python
# doi_resolver.py
"""Resolve DOIs from publisher URLs via regex extraction and Crossref API."""
import re


def extract_doi_from_url(url: str) -> str | None:
    """Extract a DOI from a publisher URL using regex. No API calls.

    Returns the DOI string (e.g. '10.1257/aer.20181234') or None.
    Only extracts DOIs that appear to be article-level identifiers.
    """
    if not url:
        return None

    # Reject supplementary material / asset URLs
    if '/asset/' in url or '/supinfo/' in url or '/supp/' in url:
        return None

    # Strip fragment
    url_clean = url.split('#')[0]

    # Match DOI pattern: 10.NNNN/anything-except-whitespace-?-#
    # Must appear after a path separator or query param, not embedded in random text
    match = re.search(r'(?:^|[/=])(10\.\d{4,}/[^\s?#]+)', url_clean)
    if not match:
        return None

    doi = match.group(1).rstrip('/')
    return doi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_doi_resolver.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add doi_resolver.py tests/test_doi_resolver.py
git commit -m "feat: add DOI extraction from publisher URLs via regex"
```

---

### Task 2: DOI Resolver — PII Extraction

**Files:**
- Modify: `doi_resolver.py`
- Modify: `tests/test_doi_resolver.py`

- [ ] **Step 1: Write failing tests for PII extraction**

Add to `tests/test_doi_resolver.py`:

```python
from doi_resolver import extract_pii_from_url


class TestExtractPiiFromUrl:
    def test_sciencedirect_pii(self):
        assert extract_pii_from_url(
            "https://www.sciencedirect.com/science/article/pii/S0959378013002410"
        ) == "S0959378013002410"

    def test_sciencedirect_with_query_params(self):
        assert extract_pii_from_url(
            "https://www.sciencedirect.com/science/article/pii/S0927537125000715?via=ihub"
        ) == "S0927537125000715"

    def test_no_pii_in_non_sciencedirect(self):
        assert extract_pii_from_url(
            "https://link.springer.com/article/10.1007/s40641-016-0032-z"
        ) is None

    def test_no_pii_in_empty(self):
        assert extract_pii_from_url("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_doi_resolver.py::TestExtractPiiFromUrl -v`
Expected: FAIL — `cannot import name 'extract_pii_from_url'`

- [ ] **Step 3: Implement extract_pii_from_url**

Add to `doi_resolver.py`:

```python
def extract_pii_from_url(url: str) -> str | None:
    """Extract a ScienceDirect PII from a URL. Returns PII string or None."""
    if not url:
        return None
    match = re.search(r'/pii/([A-Z0-9]+)', url)
    return match.group(1) if match else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_doi_resolver.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add doi_resolver.py tests/test_doi_resolver.py
git commit -m "feat: add PII extraction from ScienceDirect URLs"
```

---

### Task 3: DOI Resolver — Crossref PII-to-DOI Resolution

**Files:**
- Modify: `doi_resolver.py`
- Modify: `tests/test_doi_resolver.py`

- [ ] **Step 1: Write failing tests for Crossref resolution**

Add to `tests/test_doi_resolver.py`:

```python
from unittest.mock import patch, MagicMock
from doi_resolver import resolve_pii_via_crossref


class TestResolvePiiViaCrossref:
    def test_resolves_pii_to_doi(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "items": [{"DOI": "10.1016/j.gloenvcha.2013.12.011", "title": ["Smallholder farmer"]}]
            }
        }
        with patch("doi_resolver.requests.get", return_value=mock_resp) as mock_get:
            result = resolve_pii_via_crossref("S0959378013002410")

        assert result == "10.1016/j.gloenvcha.2013.12.011"
        mock_get.assert_called_once()
        assert "alternative-id:S0959378013002410" in str(mock_get.call_args)

    def test_returns_none_on_no_results(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"message": {"items": []}}
        with patch("doi_resolver.requests.get", return_value=mock_resp):
            assert resolve_pii_via_crossref("S0000000000000000") is None

    def test_returns_none_on_network_error(self):
        import requests as req
        with patch("doi_resolver.requests.get", side_effect=req.RequestException("timeout")):
            assert resolve_pii_via_crossref("S0959378013002410") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_doi_resolver.py::TestResolvePiiViaCrossref -v`
Expected: FAIL — `cannot import name 'resolve_pii_via_crossref'`

- [ ] **Step 3: Implement resolve_pii_via_crossref**

Add to `doi_resolver.py`:

```python
import logging
import requests

logger = logging.getLogger(__name__)

_CROSSREF_BASE = "https://api.crossref.org"


def resolve_pii_via_crossref(pii: str) -> str | None:
    """Resolve a ScienceDirect PII to a DOI via Crossref alternative-id filter.

    Returns DOI string or None.
    """
    try:
        resp = requests.get(
            f"{_CROSSREF_BASE}/works",
            params={"filter": f"alternative-id:{pii}", "rows": 1},
            headers={"User-Agent": "econ-newsfeed/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
        if items:
            return items[0].get("DOI")
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("Crossref PII lookup failed for %s: %s", pii, e)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_doi_resolver.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add doi_resolver.py tests/test_doi_resolver.py
git commit -m "feat: add Crossref PII-to-DOI resolution"
```

---

### Task 4: DOI Resolver — Top-Level resolve_doi_from_url

**Files:**
- Modify: `doi_resolver.py`
- Modify: `tests/test_doi_resolver.py`

- [ ] **Step 1: Write failing tests for the unified resolver**

Add to `tests/test_doi_resolver.py`:

```python
from doi_resolver import resolve_doi


class TestResolveDoi:
    """Top-level function: tries regex first, then PII→Crossref."""

    def test_returns_doi_from_regex(self):
        """DOI in URL → returns immediately, no API call."""
        with patch("doi_resolver.resolve_pii_via_crossref") as mock_cr:
            result = resolve_doi("https://link.springer.com/article/10.1007/s40641-016-0032-z")
        assert result == "10.1007/s40641-016-0032-z"
        mock_cr.assert_not_called()

    def test_resolves_pii_via_crossref(self):
        """ScienceDirect PII → calls Crossref."""
        with patch("doi_resolver.resolve_pii_via_crossref", return_value="10.1016/j.gloenvcha.2013.12.011"):
            result = resolve_doi("https://www.sciencedirect.com/science/article/pii/S0959378013002410")
        assert result == "10.1016/j.gloenvcha.2013.12.011"

    def test_returns_none_for_unresolvable(self):
        """URL with no extractable identifier → None."""
        result = resolve_doi("https://academic.oup.com/restud/article/83/1/87/2461318")
        assert result is None

    def test_returns_none_for_empty(self):
        assert resolve_doi("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_doi_resolver.py::TestResolveDoi -v`
Expected: FAIL — `cannot import name 'resolve_doi'`

- [ ] **Step 3: Implement resolve_doi**

Add to `doi_resolver.py`:

```python
def resolve_doi(url: str) -> str | None:
    """Resolve a DOI from a URL. Tries regex first, then PII→Crossref.

    Returns DOI string or None. Only makes API calls when necessary.
    """
    # 1. Try regex extraction (free, instant)
    doi = extract_doi_from_url(url)
    if doi:
        return doi

    # 2. Try PII → Crossref (1 API call)
    pii = extract_pii_from_url(url)
    if pii:
        return resolve_pii_via_crossref(pii)

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_doi_resolver.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add doi_resolver.py tests/test_doi_resolver.py
git commit -m "feat: add unified resolve_doi with regex-first, Crossref fallback"
```

---

### Task 5: OpenAlex DOI Lookup

**Files:**
- Modify: `openalex.py` (add `lookup_by_doi` function after `search_work` at line 110)
- Modify: `tests/test_openalex.py`

- [ ] **Step 1: Write failing tests for DOI-based lookup**

Add to `tests/test_openalex.py`:

```python
SAMPLE_OPENALEX_WORK = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.1257/aer.20181234",
    "title": "Trade and Wages",
    "authorships": [
        {
            "author": {
                "id": "https://openalex.org/A5023888391",
                "display_name": "Max Friedrich Steinhardt",
            },
        },
    ],
    "abstract_inverted_index": {"This": [0], "paper": [1], "studies": [2], "trade.": [3]},
}


class TestLookupByDoi:
    """Tests for openalex.lookup_by_doi — exact DOI lookup."""

    def test_returns_parsed_result(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = SAMPLE_OPENALEX_WORK

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import lookup_by_doi
            result = lookup_by_doi("10.1257/aer.20181234")

        assert result is not None
        assert result["doi"] == "10.1257/aer.20181234"
        assert result["openalex_id"] == "W2741809807"
        assert result["title"] == "Trade and Wages"
        assert len(result["coauthors"]) == 1

    def test_returns_none_on_404(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import lookup_by_doi
            result = lookup_by_doi("10.9999/nonexistent")

        assert result is None

    def test_returns_none_on_network_error(self):
        import requests as req
        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.side_effect = req.RequestException("timeout")
            from openalex import lookup_by_doi
            result = lookup_by_doi("10.1257/aer.20181234")

        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_openalex.py::TestLookupByDoi -v`
Expected: FAIL — `cannot import name 'lookup_by_doi'`

- [ ] **Step 3: Implement lookup_by_doi in openalex.py**

Add after `search_work` function (after line 110 in `openalex.py`):

```python
def lookup_by_doi(doi: str) -> dict | None:
    """Look up a work in OpenAlex by exact DOI.

    Returns a dict with keys: doi, openalex_id, coauthors, abstract, title
    or None if not found. Does not consume the daily search budget.
    """
    session = _get_session()
    try:
        resp = session.get(f"{OPENALEX_BASE_URL}/works/doi:{doi}", timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        work = resp.json()
        result = _parse_work(work)
        result["title"] = work.get("title", "")
        return result
    except (requests.RequestException, ValueError) as e:
        logger.warning("OpenAlex DOI lookup failed for '%s': %s", doi, e)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_openalex.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add openalex.py tests/test_openalex.py
git commit -m "feat: add OpenAlex DOI-based lookup (exact match)"
```

---

### Task 6: Schema Migration — Add doi to paper_links and openalex_author_id to researchers

**Files:**
- Modify: `database/schema.py` — update `_TABLE_DEFINITIONS` and add migration in `create_tables()`

- [ ] **Step 1: Update paper_links table definition**

In `database/schema.py`, modify the `paper_links` entry in `_TABLE_DEFINITIONS` (line 298-309) to add `doi` column:

```python
    "paper_links": """
        CREATE TABLE IF NOT EXISTS paper_links (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_id INT NOT NULL,
            url VARCHAR(2048) NOT NULL,
            link_type ENUM('pdf', 'ssrn', 'nber', 'arxiv', 'doi', 'journal',
                            'drive', 'dropbox', 'repository', 'other') DEFAULT NULL,
            doi VARCHAR(255) DEFAULT NULL,
            discovered_at DATETIME NOT NULL,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
            UNIQUE KEY uq_paper_link (paper_id, url(500))
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
```

- [ ] **Step 2: Add migration for existing databases**

In `database/schema.py`, inside `create_tables()` in the migrations block (after the `raw_html` migration around line 458), add:

```python
                    # Add doi column to paper_links
                    try:
                        cursor.execute("""
                            ALTER TABLE paper_links
                            ADD COLUMN doi VARCHAR(255) DEFAULT NULL AFTER link_type
                        """)
                        conn.commit()
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: paper_links.doi: %s", e)

                    # Add openalex_author_id to researchers
                    try:
                        cursor.execute("""
                            ALTER TABLE researchers
                            ADD COLUMN openalex_author_id VARCHAR(255) DEFAULT NULL
                        """)
                        conn.commit()
                    except Exception as e:
                        if "Duplicate column name" not in str(e):
                            logging.warning("Migration: researchers.openalex_author_id: %s", e)
```

- [ ] **Step 3: Run migration locally to verify**

Run: `poetry run python -c "from database import Database; Database.create_tables()"`
Expected: no errors. Verify with:
```bash
poetry run python -c "
from database import Database
cols = Database.fetch_all('SHOW COLUMNS FROM paper_links')
print([c['Field'] for c in cols])
cols2 = Database.fetch_all('SHOW COLUMNS FROM researchers')
print([c['Field'] for c in cols2])
"
```
Expected: `paper_links` has `doi`, `researchers` has `openalex_author_id`

- [ ] **Step 4: Commit**

```bash
git add database/schema.py
git commit -m "feat: add doi column to paper_links, openalex_author_id to researchers"
```

---

### Task 7: Enhanced Link-to-Paper Matching with DOI

**Files:**
- Modify: `link_extractor.py` — update `match_and_save_paper_links` (line 403-448)
- Modify: `tests/test_link_extractor.py`

- [ ] **Step 1: Write failing tests for DOI-based link matching**

Add to `tests/test_link_extractor.py`:

```python
class TestMatchAndSavePaperLinksWithDoi:
    """DOI-based matching: resolve DOI from URL, get canonical title, match to paper."""

    @patch("link_extractor.Database.execute_query")
    @patch("link_extractor.Database.fetch_all")
    @patch("link_extractor.Database.fetch_one")
    @patch("link_extractor.Database.compute_title_hash")
    @patch("link_extractor.HTMLFetcher.get_raw_html")
    def test_doi_link_matched_by_canonical_title(self, mock_get_raw, mock_hash,
                                                  mock_fetch_one, mock_fetch_all, mock_execute):
        """Link with DOI in URL → resolve DOI → get canonical title → match to paper."""
        html = '<div><a href="https://link.springer.com/article/10.1007/s40641-016-0032-z">Extreme Air Pollution</a></div>'
        mock_get_raw.return_value = html

        # resolve_doi returns DOI, lookup_by_doi returns canonical title
        with patch("link_extractor.resolve_doi", return_value="10.1007/s40641-016-0032-z"), \
             patch("link_extractor.lookup_by_doi", return_value={
                 "doi": "10.1007/s40641-016-0032-z",
                 "title": "Extreme Air Pollution in Global Megacities",
                 "openalex_id": "W123", "coauthors": [], "abstract": None,
             }):
            mock_hash.return_value = "abc123"
            mock_fetch_one.return_value = {"id": 10}
            mock_fetch_all.return_value = []  # no publications passed

            match_and_save_paper_links(url_id=1, publications=[])

        link_calls = [c for c in mock_execute.call_args_list if 'paper_links' in c[0][0]]
        assert len(link_calls) == 1
        params = link_calls[0][0][1]
        assert params[0] == 10  # paper_id
        assert "10.1007/s40641-016-0032-z" in params  # doi stored

    @patch("link_extractor.Database.execute_query")
    @patch("link_extractor.Database.fetch_all")
    @patch("link_extractor.Database.compute_title_hash")
    @patch("link_extractor.HTMLFetcher.get_raw_html")
    def test_falls_back_to_anchor_text_when_no_doi(self, mock_get_raw, mock_hash,
                                                     mock_fetch_all, mock_execute):
        """Link without DOI → falls back to anchor text matching."""
        html = '<div><a href="https://academic.oup.com/restud/article/83/1/87/2461318">Trade and Innovation</a></div>'
        mock_get_raw.return_value = html

        with patch("link_extractor.resolve_doi", return_value=None):
            mock_hash.return_value = "abc123"
            mock_fetch_all.return_value = [{'id': 10, 'title_hash': 'abc123'}]

            match_and_save_paper_links(url_id=1, publications=[{'title': 'Trade and Innovation'}])

        link_calls = [c for c in mock_execute.call_args_list if 'paper_links' in c[0][0]]
        assert len(link_calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_link_extractor.py::TestMatchAndSavePaperLinksWithDoi -v`
Expected: FAIL

- [ ] **Step 3: Update match_and_save_paper_links in link_extractor.py**

Replace `match_and_save_paper_links` function (line 403-448 in `link_extractor.py`) with:

```python
def match_and_save_paper_links(url_id, publications):
    """Match page links to papers, save to paper_links.

    Two matching strategies:
    1. DOI-based: resolve DOI from URL → get canonical title → match by title_hash
    2. Anchor text: fall back to fuzzy matching (existing approach)

    Called after save_publications().
    """
    import time
    from doi_resolver import resolve_doi
    from openalex import lookup_by_doi

    raw_html = HTMLFetcher.get_raw_html(url_id)
    if not raw_html:
        return

    page_links = extract_trusted_links(raw_html)
    if not page_links:
        return

    # Build title lookup from passed publications (for anchor text fallback)
    hash_to_title = {}
    for pub in publications:
        title = (pub.get('title') or '').strip()
        if title:
            hash_to_title[Database.compute_title_hash(title)] = title

    if hash_to_title:
        placeholders = ",".join(["%s"] * len(hash_to_title))
        rows = Database.fetch_all(
            f"SELECT id, title_hash FROM papers WHERE title_hash IN ({placeholders})",
            tuple(hash_to_title.keys()),
        )
        paper_ids_by_title = {hash_to_title[r['title_hash']]: r['id'] for r in rows}
    else:
        paper_ids_by_title = {}

    for link in page_links:
        paper_id = None
        link_doi = None

        # Strategy 1: DOI-based matching
        link_doi = resolve_doi(link['url'])
        if link_doi:
            # Try to find paper by canonical title from OpenAlex
            openalex_data = lookup_by_doi(link_doi)
            if openalex_data and openalex_data.get('title'):
                canonical_hash = Database.compute_title_hash(openalex_data['title'])
                paper_row = Database.fetch_one(
                    "SELECT id FROM papers WHERE title_hash = %s", (canonical_hash,)
                )
                if paper_row:
                    paper_id = paper_row['id']
            time.sleep(0.2)  # Rate limit OpenAlex calls

            # Also try matching DOI directly against papers.doi
            if not paper_id:
                paper_row = Database.fetch_one(
                    "SELECT id FROM papers WHERE doi = %s", (link_doi,)
                )
                if paper_row:
                    paper_id = paper_row['id']

        # Strategy 2: Anchor text fallback
        if not paper_id and paper_ids_by_title:
            matched_title, _ = match_link_to_paper(link['anchor_text'], list(paper_ids_by_title.keys()))
            if matched_title:
                paper_id = paper_ids_by_title[matched_title]

        if paper_id:
            try:
                Database.execute_query(
                    """INSERT IGNORE INTO paper_links (paper_id, url, link_type, doi, discovered_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (paper_id, link['url'], link['link_type'], link_doi,
                     datetime.now(timezone.utc)))
            except Exception as e:
                logging.warning("Error saving paper link: %s", e)
```

- [ ] **Step 4: Run all link_extractor tests to verify**

Run: `poetry run pytest tests/test_link_extractor.py -v`
Expected: all PASS (existing tests + new ones)

- [ ] **Step 5: Commit**

```bash
git add link_extractor.py tests/test_link_extractor.py
git commit -m "feat: DOI-based link-to-paper matching with anchor text fallback"
```

---

### Task 8: Researcher Disambiguation via OpenAlex Author ID

**Files:**
- Modify: `database/researchers.py` (lines 57-115)
- Create: `tests/test_researcher_disambiguation.py`

- [ ] **Step 1: Write failing tests for OpenAlex author ID matching**

```python
# tests/test_researcher_disambiguation.py
"""Tests for researcher disambiguation with OpenAlex author ID."""
import os
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import patch, MagicMock
from database.researchers import get_researcher_id


class TestOpenalexAuthorIdMatching:
    """get_researcher_id should match by openalex_author_id before LLM."""

    def _mock_db(self):
        """Return mock _fetch_one, _fetch_all, _execute functions."""
        return MagicMock(), MagicMock(), MagicMock()

    @patch("database.researchers._disambiguate_researcher")
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    def test_matches_by_openalex_id_skips_llm(self, mock_fetch_one, mock_fetch_all, mock_disambig):
        # No exact name match
        mock_fetch_one.side_effect = [
            None,  # exact name match fails
            {"id": 42},  # openalex_author_id match succeeds
        ]

        result = get_researcher_id("M.", "Steinhardt", openalex_author_id="A5023888391")

        assert result == 42
        mock_disambig.assert_not_called()

    @patch("database.researchers._disambiguate_researcher", return_value=99)
    @patch("database.researchers.fetch_all")
    @patch("database.researchers.fetch_one")
    def test_falls_back_to_llm_when_no_openalex_id(self, mock_fetch_one, mock_fetch_all, mock_disambig):
        mock_fetch_one.return_value = None  # no exact match, no openalex match
        mock_fetch_all.return_value = [{"id": 99, "first_name": "Max", "last_name": "Steinhardt"}]

        result = get_researcher_id("M.", "Steinhardt")

        assert result == 99
        mock_disambig.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_researcher_disambiguation.py -v`
Expected: FAIL — signature mismatch or missing `openalex_author_id` parameter

- [ ] **Step 3: Update get_researcher_id in database/researchers.py**

Modify `get_researcher_id` (line 57-115) to accept and use `openalex_author_id`:

```python
def get_researcher_id(first_name: str, last_name: str, position: str | None = None,
                      affiliation: str | None = None,
                      openalex_author_id: str | None = None,
                      conn: "mysql.connector.connection.MySQLConnection | None" = None) -> int:
    """Get the researcher ID based on name.

    Matching priority:
    1. Exact first_name + last_name match
    2. OpenAlex author ID match (deterministic, free)
    3. LLM disambiguation for same-last-name candidates
    4. Insert new researcher
    """
    def _fetch_one(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True)
            c.execute(query, params)
            row = c.fetchone()
            c.close()
            return row
        return fetch_one(query, params)

    def _fetch_all(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True)
            c.execute(query, params)
            rows = c.fetchall()
            c.close()
            return rows
        return fetch_all(query, params)

    def _execute(query, params):
        if conn is not None:
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            lid = c.lastrowid
            c.close()
            return lid
        return execute_query(query, params)

    # 1. Exact match
    result = _fetch_one(
        "SELECT id FROM researchers WHERE first_name = %s AND last_name = %s",
        (first_name, last_name),
    )
    if result:
        return result['id']

    # 2. OpenAlex author ID match
    if openalex_author_id:
        result = _fetch_one(
            "SELECT id FROM researchers WHERE openalex_author_id = %s",
            (openalex_author_id,),
        )
        if result:
            logging.info(
                f"OpenAlex ID matched '{first_name} {last_name}' to researcher id={result['id']}"
            )
            return result['id']

    # 3. Same-last-name candidates — let LLM decide if any is the same person
    candidates = _fetch_all(
        "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
        (last_name,),
    )
    if candidates:
        match_id = _disambiguate_researcher(first_name, last_name, candidates)
        if match_id is not None:
            logging.info(
                f"LLM matched '{first_name} {last_name}' to existing researcher id={match_id}"
            )
            # Backfill openalex_author_id if we have it
            if openalex_author_id:
                _execute(
                    "UPDATE researchers SET openalex_author_id = %s WHERE id = %s AND openalex_author_id IS NULL",
                    (openalex_author_id, match_id),
                )
            return match_id

    # 4. No match found — insert new researcher
    new_id = _execute(
        "INSERT INTO researchers (first_name, last_name, position, affiliation, openalex_author_id) VALUES (%s, %s, %s, %s, %s)",
        (first_name, last_name, position, affiliation, openalex_author_id),
    )
    return new_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_researcher_disambiguation.py -v`
Expected: all PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `poetry run pytest tests/ -v --ignore=tests/test_api_search.py`
Expected: all PASS (excluding pre-existing failures)

- [ ] **Step 6: Commit**

```bash
git add database/researchers.py tests/test_researcher_disambiguation.py
git commit -m "feat: researcher disambiguation via OpenAlex author ID before LLM"
```

---

### Task 9: Update Enrichment to Use DOI-First Strategy

**Files:**
- Modify: `openalex.py` — update `enrich_new_publications` (line 166-199)
- Modify: `database/papers.py` — update `get_unenriched_papers` (line 70-88)
- Modify: `tests/test_openalex.py`

- [ ] **Step 1: Write failing test for DOI-first enrichment**

Add to `tests/test_openalex.py`:

```python
class TestEnrichWithDoiFirst:
    """enrich_publication should try DOI lookup before title search."""

    def test_uses_doi_from_paper_links(self):
        """If paper has a DOI in paper_links, use it instead of title search."""
        openalex_result = {
            "doi": "10.1007/s40641-016-0032-z",
            "openalex_id": "W123",
            "title": "Extreme Air Pollution",
            "coauthors": [{"display_name": "A. Author", "openalex_author_id": "A111"}],
            "abstract": "Abstract text",
        }
        with (
            patch("openalex.lookup_by_doi", return_value=openalex_result) as mock_lookup,
            patch("openalex.search_work") as mock_search,
            patch("openalex.Database.update_openalex_data") as mock_update,
        ):
            from openalex import enrich_publication
            result = enrich_publication(
                paper_id=1,
                title="Extreme Air Pollution",
                author_name="Author",
                existing_abstract=None,
                doi="10.1007/s40641-016-0032-z",
            )

        assert result is True
        mock_lookup.assert_called_once_with("10.1007/s40641-016-0032-z")
        mock_search.assert_not_called()


class TestBackfillResearcherOpenalexIds:
    """_backfill_researcher_openalex_ids populates openalex_author_id on researchers."""

    def test_updates_researcher_openalex_id(self):
        coauthors = [
            {"display_name": "Max Steinhardt", "openalex_author_id": "A5023888391"},
            {"display_name": "Jane Doe", "openalex_author_id": "A5000000001"},
        ]
        with (
            patch("openalex.Database.fetch_all", return_value=[
                {"id": 1, "first_name": "Max", "last_name": "Steinhardt", "openalex_author_id": None},
            ]),
            patch("openalex.Database.execute_query") as mock_exec,
        ):
            from openalex import _backfill_researcher_openalex_ids
            _backfill_researcher_openalex_ids(paper_id=10, coauthors=coauthors)

        mock_exec.assert_called_once()
        assert mock_exec.call_args[0][1] == ("A5023888391", 1)

    def test_skips_when_no_openalex_ids(self):
        coauthors = [{"display_name": "Author", "openalex_author_id": None}]
        with patch("openalex.Database.fetch_all") as mock_fetch:
            from openalex import _backfill_researcher_openalex_ids
            _backfill_researcher_openalex_ids(paper_id=10, coauthors=coauthors)
        mock_fetch.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_openalex.py::TestEnrichWithDoiFirst -v`
Expected: FAIL

- [ ] **Step 3: Update enrich_publication in openalex.py**

Modify `enrich_publication` (line 144-163 in `openalex.py`) to accept and use DOI:

```python
def enrich_publication(paper_id, title, author_name, existing_abstract=None, doi=None):
    """Enrich a single publication with OpenAlex data.

    If doi is provided, uses exact DOI lookup (no budget cost).
    Otherwise falls back to title+author search.
    Returns True if enrichment data was found and stored, False otherwise.
    """
    result = None

    # Strategy 1: DOI lookup (exact, no budget cost)
    if doi:
        result = lookup_by_doi(doi)

    # Strategy 2: Title search (fuzzy, costs budget)
    if not result:
        result = search_work(title, author_name)

    if not result:
        return False

    # Only use OpenAlex abstract as fallback
    abstract = result["abstract"] if not existing_abstract else None

    Database.update_openalex_data(
        paper_id=paper_id,
        doi=result["doi"],
        openalex_id=result["openalex_id"],
        coauthors=result["coauthors"],
        abstract=abstract,
    )

    # Backfill openalex_author_id on researchers from coauthor data
    _backfill_researcher_openalex_ids(paper_id, result["coauthors"])

    return True


def _backfill_researcher_openalex_ids(paper_id, coauthors):
    """Populate openalex_author_id on researchers matched to this paper's authors."""
    if not coauthors:
        return
    # Build lookup: openalex_author_id -> display_name from coauthors
    oa_ids = {ca["openalex_author_id"]: ca["display_name"]
              for ca in coauthors if ca.get("openalex_author_id")}
    if not oa_ids:
        return
    # Get researchers linked to this paper
    rows = Database.fetch_all(
        """SELECT r.id, r.first_name, r.last_name, r.openalex_author_id
           FROM researchers r
           JOIN authorship a ON a.researcher_id = r.id
           WHERE a.publication_id = %s AND r.openalex_author_id IS NULL""",
        (paper_id,),
    )
    for r in rows:
        full_name = f"{r['first_name']} {r['last_name']}".lower()
        last_name = r['last_name'].lower()
        for oa_id, display_name in oa_ids.items():
            # Match by last name appearing in OpenAlex display name
            if last_name in display_name.lower().split():
                Database.execute_query(
                    "UPDATE researchers SET openalex_author_id = %s WHERE id = %s",
                    (oa_id, r['id']),
                )
                break
```

- [ ] **Step 4: Update get_unenriched_papers to include DOI from paper_links**

Modify `get_unenriched_papers` in `database/papers.py` (line 70-88):

```python
def get_unenriched_papers(limit=50):
    """Get papers that haven't been enriched via OpenAlex yet.

    Returns list of dicts with keys: id, title, abstract, author_name, status, link_doi.
    Papers with links get priority. Only published papers without links are included.
    """
    return fetch_all(
        """
        SELECT p.id, p.title, p.abstract, p.status,
               MIN(CONCAT(r.first_name, ' ', r.last_name)) AS author_name,
               (SELECT pl.doi FROM paper_links pl
                WHERE pl.paper_id = p.id AND pl.doi IS NOT NULL
                LIMIT 1) AS link_doi
        FROM papers p
        JOIN authorship a ON a.publication_id = p.id
        JOIN researchers r ON r.id = a.researcher_id
        WHERE p.openalex_id IS NULL
          AND (
            EXISTS (SELECT 1 FROM paper_links pl WHERE pl.paper_id = p.id)
            OR p.status = 'published'
          )
        GROUP BY p.id, p.title, p.abstract, p.status
        ORDER BY link_doi IS NOT NULL DESC, p.id
        LIMIT %s
        """,
        (limit,),
    )
```

- [ ] **Step 5: Update enrich_new_publications to pass DOI**

Modify `enrich_new_publications` in `openalex.py` (line 166-199) to pass `link_doi`:

```python
def enrich_new_publications(limit=50):
    """Enrich unenriched publications with OpenAlex data.

    Papers with DOIs from paper_links are enriched first (exact match).
    Papers without links are only enriched if published (title search fallback).
    """
    if not _check_budget():
        logger.info("OpenAlex daily budget exhausted (%d/%d), skipping", _daily_counter["count"], DAILY_BUDGET)
        return 0

    papers = Database.get_unenriched_papers(limit=limit)
    if not papers:
        logger.info("No unenriched papers found")
        return 0

    logger.info("Enriching %d papers via OpenAlex (budget: %d/%d used today)",
                len(papers), _daily_counter["count"], DAILY_BUDGET)
    enriched = 0
    for paper in papers:
        doi = paper.get("link_doi")
        if not doi and not _check_budget():
            logger.info("OpenAlex daily budget reached, stopping enrichment")
            break
        success = enrich_publication(
            paper_id=paper["id"],
            title=paper["title"],
            author_name=paper["author_name"],
            existing_abstract=paper.get("abstract"),
            doi=doi,
        )
        if success:
            enriched += 1
        if not doi:
            time.sleep(0.5)  # Only rate-limit title searches

    logger.info("OpenAlex enrichment: %d/%d papers matched", enriched, len(papers))
    return len(papers)
```

- [ ] **Step 6: Run tests to verify**

Run: `poetry run pytest tests/test_openalex.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add openalex.py database/papers.py tests/test_openalex.py
git commit -m "feat: DOI-first enrichment strategy with title search fallback for published papers"
```

---

### Task 10: Update Database Facade

**Files:**
- Modify: `database/__init__.py`

- [ ] **Step 1: No new exports needed — verify existing facade**

The `Database` facade already exports `get_unenriched_papers`, `update_openalex_data`, `compute_title_hash`, and `fetch_one`. The updated `get_unenriched_papers` query change is transparent. No facade changes needed.

- [ ] **Step 2: Run full test suite**

Run: `poetry run pytest tests/ -v --ignore=tests/test_api_search.py`
Expected: all PASS

- [ ] **Step 3: Commit (if any changes were needed)**

No commit expected — this is a verification step.

---

### Task 11: Backfill Script — Populate paper_links and Enrich

**Files:**
- Create: `scripts/backfill_paper_links.py`

- [ ] **Step 1: Write the backfill script**

```python
# scripts/backfill_paper_links.py
"""One-time backfill: extract links from stored HTML, resolve DOIs, enrich papers.

Processes all 535 stored HTML pages to populate the paper_links table,
then enriches papers that gained DOIs.

Run: poetry run python scripts/backfill_paper_links.py
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from database import Database
from html_fetcher import HTMLFetcher
from link_extractor import extract_trusted_links, match_link_to_paper
from doi_resolver import resolve_doi
from openalex import lookup_by_doi


def backfill_links():
    """Extract links from all stored HTML and save to paper_links."""
    all_urls = Database.fetch_all(
        "SELECT hc.url_id, ru.url, ru.researcher_id "
        "FROM html_content hc "
        "JOIN researcher_urls ru ON ru.id = hc.url_id "
        "WHERE hc.raw_html IS NOT NULL"
    )
    logger.info("Processing %d HTML pages for link extraction", len(all_urls))

    total_links = 0
    total_matched = 0
    total_doi_resolved = 0

    for i, row in enumerate(all_urls):
        raw_html = HTMLFetcher.get_raw_html(row['url_id'])
        if not raw_html:
            continue

        page_links = extract_trusted_links(raw_html)
        if not page_links:
            continue

        # Get all papers for this researcher
        papers = Database.fetch_all(
            "SELECT p.id, p.title, p.title_hash FROM papers p "
            "JOIN authorship a ON a.publication_id = p.id "
            "WHERE a.researcher_id = %s",
            (row['researcher_id'],),
        )
        paper_titles = {p['title']: p['id'] for p in papers}

        for link in page_links:
            if link['link_type'] not in ('journal', 'doi', 'ssrn', 'nber', 'arxiv', 'repository'):
                continue

            total_links += 1
            paper_id = None
            link_doi = None

            # Try DOI resolution
            link_doi = resolve_doi(link['url'])
            if link_doi:
                total_doi_resolved += 1
                # Match by canonical title
                openalex_data = lookup_by_doi(link_doi)
                if openalex_data and openalex_data.get('title'):
                    canonical_hash = Database.compute_title_hash(openalex_data['title'])
                    paper_row = Database.fetch_one(
                        "SELECT id FROM papers WHERE title_hash = %s", (canonical_hash,)
                    )
                    if paper_row:
                        paper_id = paper_row['id']
                time.sleep(0.2)  # Rate limit OpenAlex

            # Fallback: anchor text matching
            if not paper_id and paper_titles:
                matched_title, _ = match_link_to_paper(link['anchor_text'], list(paper_titles.keys()))
                if matched_title:
                    paper_id = paper_titles[matched_title]

            if paper_id:
                total_matched += 1
                try:
                    from datetime import datetime, timezone
                    Database.execute_query(
                        """INSERT IGNORE INTO paper_links (paper_id, url, link_type, doi, discovered_at)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (paper_id, link['url'], link['link_type'], link_doi,
                         datetime.now(timezone.utc)),
                    )
                except Exception as e:
                    logger.warning("Error saving link: %s", e)

        if (i + 1) % 50 == 0:
            logger.info("Progress: %d/%d pages, %d links found, %d matched, %d DOIs resolved",
                        i + 1, len(all_urls), total_links, total_matched, total_doi_resolved)

    logger.info("Backfill complete: %d links found, %d matched to papers, %d DOIs resolved",
                total_links, total_matched, total_doi_resolved)


def enrich_from_links():
    """Enrich papers that now have DOIs from paper_links."""
    from openalex import enrich_new_publications
    count = enrich_new_publications(limit=500)
    logger.info("Enriched %d papers from DOI-resolved links", count)


if __name__ == "__main__":
    print("=== Phase 1: Backfill paper_links from stored HTML ===")
    backfill_links()

    count = Database.fetch_one("SELECT COUNT(*) AS c FROM paper_links")
    print(f"\npaper_links rows: {count['c']}")

    doi_count = Database.fetch_one("SELECT COUNT(*) AS c FROM paper_links WHERE doi IS NOT NULL")
    print(f"paper_links with DOI: {doi_count['c']}")

    print("\n=== Phase 2: Enrich papers with DOIs ===")
    enrich_from_links()

    enriched = Database.fetch_one("SELECT COUNT(*) AS c FROM papers WHERE openalex_id IS NOT NULL")
    print(f"\nTotal enriched papers: {enriched['c']}")
```

- [ ] **Step 2: Commit the script**

```bash
git add scripts/backfill_paper_links.py
git commit -m "feat: add backfill script for paper_links and DOI enrichment"
```

- [ ] **Step 3: Run the migration first**

Run: `poetry run python -c "from database import Database; Database.create_tables()"`

- [ ] **Step 4: Run the backfill**

Run: `poetry run python scripts/backfill_paper_links.py`
Expected: populates `paper_links` table with links, resolves DOIs, enriches papers.

---

### Task 12: Integration — Wire into Scheduler

**Files:**
- Modify: `scheduler.py` (around line 196-221, after `save_publications`)

- [ ] **Step 1: Verify match_and_save_paper_links is already called in scheduler**

Check `scheduler.py`. The `match_and_save_paper_links` function is NOT currently called in the scheduler's `run_scrape_job`. It's only called in `main.py` (the CLI pipeline). The scheduler calls `save_publications` but skips link extraction.

- [ ] **Step 2: Add link extraction to scheduler's scrape loop**

In `scheduler.py`, after `save_publications` (line 200), add the link extraction call:

Add import at top of file (with other imports around line 10):
```python
from link_extractor import match_and_save_paper_links
```

After line 201 (`logger.info(f"  save_publications — {save_ms:.0f}ms")`), add:

```python
                            # Extract and match trusted links
                            t0 = time.time()
                            match_and_save_paper_links(url_id, pubs)
                            links_ms = (time.time() - t0) * 1000
                            logger.info(f"  paper links — {links_ms:.0f}ms")
```

- [ ] **Step 3: Run full test suite**

Run: `poetry run pytest tests/ -v --ignore=tests/test_api_search.py`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add scheduler.py
git commit -m "feat: wire link extraction into scheduler scrape loop"
```

---

### Task 13: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `poetry run pytest tests/ -v --ignore=tests/test_api_search.py`
Expected: all PASS (351+ tests)

- [ ] **Step 2: Run TypeScript checks**

Run: `cd app && npx tsc --noEmit && npx jest`
Expected: PASS (no frontend changes)

- [ ] **Step 3: Verify end-to-end manually**

```bash
poetry run python -c "
from doi_resolver import resolve_doi
# Test regex extraction
print('Springer:', resolve_doi('https://link.springer.com/article/10.1007/s40641-016-0032-z'))
# Test PII -> Crossref
print('ScienceDirect:', resolve_doi('https://www.sciencedirect.com/science/article/pii/S0959378013002410'))
# Test OpenAlex DOI lookup
from openalex import lookup_by_doi
result = lookup_by_doi('10.1007/s40641-016-0032-z')
print('OpenAlex:', result['title'] if result else 'MISS')
"
```

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git log --oneline feature/doi-first-enrichment ^main
```
