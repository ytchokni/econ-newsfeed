# Paper Link Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract paper-related hyperlinks from researcher web pages and associate them with the correct papers. Also provide a `make discover-domains` command that scans all stored HTML to find untrusted domains that likely host paper links, so the trusted domain list can be expanded over time.

**Architecture:** Programmatic link extraction — zero LLM cost. Store raw HTML during fetch. After LLM extracts papers, a post-processing step parses `<a>` tags, filters to trusted domains, and matches links to papers by anchor text. A separate CLI command scans stored HTML for untrusted-domain links with paper-title-length anchor text to suggest new domains.

**Prototype:** `test_link_prototype_v3.py` — validated on 10 sites, 76/88 papers matched (86%), zero false positives, all matches score 1.00.

**Tech Stack:** Python (FastAPI, BeautifulSoup), MySQL, Next.js/TypeScript

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `database/schema.py` | `raw_html` column + `paper_links` table |
| Create | `link_extractor.py` | All link extraction, matching, and domain discovery logic (ported from prototype) |
| Modify | `html_fetcher.py` | Store raw HTML during fetch; add `get_raw_html()` |
| Modify | `main.py` | Wire `match_and_save_paper_links()` into pipeline; add `discover-domains` CLI command |
| Modify | `api.py` | Add `links` to publication endpoints |
| Modify | `app/src/lib/types.ts` | `PaperLink` interface |
| Modify | `app/src/components/PublicationCard.tsx` | Render links |
| Create | `tests/test_link_extractor.py` | Tests for extraction, matching, domain discovery |

---

## Task 1: Database Migration

**Files:**
- Modify: `database/schema.py`

- [ ] **Step 1: Add `raw_html` column migration**

In the advisory-lock migrations block (after line ~335 in `schema.py`), add:

```python
try:
    cursor.execute("""
        ALTER TABLE html_content
        ADD COLUMN raw_html MEDIUMTEXT DEFAULT NULL AFTER content
    """)
    logging.info("Added raw_html column to html_content")
except Exception as e:
    if "Duplicate column name" not in str(e):
        raise
```

- [ ] **Step 2: Add `paper_links` table to `_TABLE_DEFINITIONS` dict (before line 267)**

```python
"paper_links": """
    CREATE TABLE IF NOT EXISTS paper_links (
        id INT AUTO_INCREMENT PRIMARY KEY,
        paper_id INT NOT NULL,
        url VARCHAR(2048) NOT NULL,
        link_type ENUM('pdf', 'ssrn', 'nber', 'arxiv', 'doi', 'journal',
                        'drive', 'dropbox', 'repository', 'other') DEFAULT NULL,
        discovered_at DATETIME NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
        UNIQUE KEY uq_paper_link (paper_id, url(500))
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
""",
```

- [ ] **Step 3: Add `"paper_links"` to the `_ALL_TABLES` list** (line ~344)

- [ ] **Step 4: Run `make seed`** — verify no errors

- [ ] **Step 5: Commit**

```bash
git add database/schema.py
git commit -m "feat: add raw_html column and paper_links table"
```

---

## Task 2: Store Raw HTML During Fetch

**Files:**
- Modify: `html_fetcher.py`
- Test: `tests/test_link_extractor.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_link_extractor.py`:

```python
"""Tests for link extraction and matching."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o-mini")
os.environ.setdefault("CONTENT_MAX_CHARS", "4000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("SCRAPE_API_KEY", "test-secret-key-for-ci-runs")
os.environ.setdefault("SCRAPE_INTERVAL_HOURS", "24")

from unittest.mock import patch, MagicMock
import pytest
from html_fetcher import HTMLFetcher


class TestSaveTextWithRawHtml:
    @patch("html_fetcher.Database.execute_query")
    def test_save_text_stores_raw_html(self, mock_execute):
        HTMLFetcher.save_text(url_id=1, text_content="text", text_hash="abc",
                              researcher_id=10, raw_html="<html>test</html>")
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "raw_html" in sql
        assert "<html>test</html>" in params

    @patch("html_fetcher.Database.execute_query")
    def test_save_text_without_raw_html_passes_none(self, mock_execute):
        HTMLFetcher.save_text(url_id=1, text_content="text", text_hash="abc", researcher_id=10)
        assert mock_execute.call_args[0][1][-1] is None
```

- [ ] **Step 2: Run → FAIL**

Run: `poetry run pytest tests/test_link_extractor.py::TestSaveTextWithRawHtml -v`

- [ ] **Step 3: Update `save_text()`** — add `raw_html=None` param, include in INSERT/UPDATE

In `html_fetcher.py` line 210, change signature and SQL:

```python
@staticmethod
def save_text(url_id, text_content, text_hash, researcher_id, raw_html=None):
    """Save pre-extracted text content, hash, and optionally raw HTML to the database."""
    query = """
        INSERT INTO html_content (url_id, content, content_hash, timestamp, researcher_id, raw_html)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            content = VALUES(content),
            content_hash = VALUES(content_hash),
            timestamp = VALUES(timestamp),
            raw_html = VALUES(raw_html)
    """
    try:
        Database.execute_query(query, (url_id, text_content, text_hash,
                                       datetime.now(timezone.utc), researcher_id, raw_html))
        logging.info(f"Text content saved for URL ID: {url_id} (Researcher ID: {researcher_id})")
    except Exception as e:
        logging.error("Error saving text content for URL ID %s: %s", url_id, type(e).__name__)
```

- [ ] **Step 4: Update `fetch_and_save_if_changed()`** — rename `html_content` to `raw_html`, pass through

At line 281:
```python
raw_html = HTMLFetcher.fetch_html(url)
if not raw_html:
    ...
text_content = HTMLFetcher.extract_text_content(raw_html)
...
HTMLFetcher.save_text(url_id, text_content, text_hash, researcher_id, raw_html=raw_html)
```

- [ ] **Step 5: Add `get_raw_html()` method**

```python
@staticmethod
def get_raw_html(url_id):
    """Retrieve stored raw HTML for a URL ID."""
    result = Database.fetch_one(
        "SELECT raw_html FROM html_content WHERE url_id = %s", (url_id,),
    )
    return result['raw_html'] if result else None
```

- [ ] **Step 6: Run → PASS**
- [ ] **Step 7: Commit**

---

## Task 3: Link Extractor Module

**Files:**
- Create: `link_extractor.py`
- Test: `tests/test_link_extractor.py`

This is the core. Port the validated logic from `test_link_prototype_v3.py` into a proper module.

- [ ] **Step 1: Write failing tests for `extract_trusted_links`**

Append to `tests/test_link_extractor.py`:

```python
from link_extractor import extract_trusted_links, match_link_to_paper, discover_untrusted_domains


class TestExtractTrustedLinks:
    def test_extracts_ssrn_link(self):
        html = '<div><a href="https://ssrn.com/abstract=1">Paper Title</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert links[0]['link_type'] == 'ssrn'

    def test_ignores_untrusted(self):
        html = '<div><a href="https://twitter.com/x">T</a><a href="https://ssrn.com/1">S</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1

    def test_ignores_non_article_journal_paths(self):
        html = '''<div>
            <a href="https://www.sciencedirect.com/topics/economics/x">topic</a>
            <a href="https://www.sciencedirect.com/science/article/pii/S1">article</a>
        </div>'''
        links = extract_trusted_links(html)
        assert len(links) == 1

    def test_strips_nav_footer(self):
        html = '<nav><a href="https://ssrn.com/1">X</a></nav><div><a href="https://ssrn.com/2">Y</a></div>'
        links = extract_trusted_links(html)
        assert len(links) == 1

    def test_url_dedup_picks_best_anchor(self):
        """When same URL appears twice, picks the best (non-generic) anchor."""
        html = '''<div>
            <a href="https://dropbox.com/paper.pdf">"</a>
            <a href="https://dropbox.com/paper.pdf">My Great Paper Title</a>
        </div>'''
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert 'My Great Paper' in links[0]['anchor_text']

    def test_sibling_fallback_for_empty_anchor(self):
        html = '''<p>
            <a href="/local.pdf"><strong>Paper Title Here</strong></a>
            <a href="https://nber.org/papers/w123"><br></a>
        </p>'''
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert 'Paper Title Here' in links[0]['anchor_text']

    def test_parent_text_fallback_for_generic_anchor(self):
        html = '''<p>Incomplete Take-Up of Insurance Benefits (with J. Doe)
            [ <a href="https://ssrn.com/abstract=99">SSRN Version</a> ]</p>'''
        links = extract_trusted_links(html)
        assert len(links) == 1
        assert 'Incomplete Take-Up' in links[0]['anchor_text']
```

- [ ] **Step 2: Run → FAIL** (module doesn't exist)

- [ ] **Step 3: Create `link_extractor.py`**

Port from `test_link_prototype_v3.py` — the following functions (without the test harness / DB / fetch_page code):

- `TRUSTED_LINK_DOMAINS` tuple + env var extension
- `_JOURNAL_NON_ARTICLE_PATTERNS`
- `_GENERIC_ANCHOR_PATTERNS`
- `_INLINE_TAGS`, `_BLOCK_TAGS`, `STOP_WORDS`
- `classify_link_type(url)`
- `is_trusted_domain(url)`
- `_strip_to_alnum(text)`
- `_meaningful_words(text)`
- `_is_generic_anchor(text)`
- `_get_sibling_anchor_text(a_tag)`
- `_get_parent_title_text(a_tag)`
- `extract_trusted_links(html)` — returns `[{'url', 'anchor_text', 'link_type'}, ...]`
- `match_link_to_paper(anchor_text, paper_titles, threshold=0.75)` — returns `(title, score)` or `(None, 0.0)`

Copy these functions verbatim from `test_link_prototype_v3.py`. Only change: add proper module docstring and imports (`re`, `unicodedata`, `os`, `BeautifulSoup`, `NavigableString`, `urlparse`).

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

```bash
git add link_extractor.py tests/test_link_extractor.py
git commit -m "feat: link extraction and anchor-text matching engine"
```

---

## Task 4: Matching Tests + `match_and_save_paper_links`

**Files:**
- Modify: `link_extractor.py`
- Test: `tests/test_link_extractor.py`

- [ ] **Step 1: Write failing tests for matching**

```python
class TestMatchLinkToPaper:
    def test_exact_match(self):
        title, _ = match_link_to_paper("Trade and Wages", ["Trade and Wages", "Other"])
        assert title == "Trade and Wages"

    def test_no_match_generic(self):
        title, _ = match_link_to_paper("PDF", ["Trade and Wages"])
        assert title is None

    def test_css_concatenation(self):
        title, _ = match_link_to_paper(
            "Outforgood: Transitory andpersistent labor",
            ["Out for good: Transitory and persistent labor"])
        assert title is not None

    def test_no_false_positive_short_substring(self):
        """'Green Waste' should NOT match 'The Green Waste Tax' at ratio 0.75."""
        title, _ = match_link_to_paper("Green Waste", ["The Green Waste Tax Problem"])
        assert title is None

    def test_accented_characters(self):
        title, _ = match_link_to_paper(
            "Économétrie: Résumé des Résultats",
            ["Econometrie: Resume des Resultats"])
        assert title is not None
```

- [ ] **Step 2: Run → PASS** (these test existing code from Task 3)

- [ ] **Step 3: Write failing test for `match_and_save_paper_links`**

```python
from link_extractor import match_and_save_paper_links


class TestMatchAndSavePaperLinks:
    @patch("link_extractor.Database.execute_query")
    @patch("link_extractor.Database.fetch_one")
    def test_matches_and_saves(self, mock_fetch_one, mock_execute):
        from html_fetcher import HTMLFetcher
        html = '<div><a href="https://ssrn.com/1">Trade and Wages</a></div>'
        mock_fetch_one.side_effect = [
            {'raw_html': html},   # get_raw_html
            {'id': 10},           # paper lookup
        ]
        with patch("link_extractor.HTMLFetcher.get_raw_html", return_value=html):
            match_and_save_paper_links(url_id=1, publications=[{'title': 'Trade and Wages'}])
        link_calls = [c for c in mock_execute.call_args_list if 'paper_links' in c[0][0]]
        assert len(link_calls) == 1
        assert link_calls[0][0][1][0] == 10  # paper_id

    @patch("link_extractor.Database.execute_query")
    def test_skips_no_raw_html(self, mock_execute):
        with patch("link_extractor.HTMLFetcher.get_raw_html", return_value=None):
            match_and_save_paper_links(url_id=1, publications=[{'title': 'X'}])
        assert not any('paper_links' in str(c) for c in mock_execute.call_args_list)
```

- [ ] **Step 4: Run → FAIL**

- [ ] **Step 5: Implement `match_and_save_paper_links` in `link_extractor.py`**

```python
import logging
from datetime import datetime, timezone
from database import Database
from html_fetcher import HTMLFetcher


def match_and_save_paper_links(url_id, publications):
    """Match page links to papers by anchor text, save to paper_links.

    Called after save_publications(). Extracts trusted-domain links from
    stored raw HTML, matches each to the best paper by anchor text,
    and persists matches.
    """
    raw_html = HTMLFetcher.get_raw_html(url_id)
    if not raw_html:
        return

    page_links = extract_trusted_links(raw_html)
    if not page_links:
        return

    paper_ids = {}
    for pub in publications:
        title = (pub.get('title') or '').strip()
        if not title:
            continue
        title_hash = Database.compute_title_hash(title)
        row = Database.fetch_one("SELECT id FROM papers WHERE title_hash = %s", (title_hash,))
        if row:
            paper_ids[title] = row['id']

    if not paper_ids:
        return

    for link in page_links:
        matched_title, _ = match_link_to_paper(link['anchor_text'], list(paper_ids.keys()))
        if matched_title:
            try:
                Database.execute_query(
                    """INSERT IGNORE INTO paper_links (paper_id, url, link_type, discovered_at)
                       VALUES (%s, %s, %s, %s)""",
                    (paper_ids[matched_title], link['url'], link['link_type'],
                     datetime.now(timezone.utc)))
            except Exception as e:
                logging.warning("Error saving paper link: %s", e)
```

- [ ] **Step 6: Run → PASS**
- [ ] **Step 7: Run full test suite**: `poetry run pytest -v`
- [ ] **Step 8: Commit**

```bash
git add link_extractor.py tests/test_link_extractor.py
git commit -m "feat: match_and_save_paper_links with anchor-text matching"
```

---

## Task 5: Domain Discovery Command

**Files:**
- Modify: `link_extractor.py`
- Modify: `main.py`
- Modify: `Makefile`
- Test: `tests/test_link_extractor.py`

This adds a `make discover-domains` command that scans all stored raw HTML for links to untrusted domains where the anchor text is long enough to be a paper title. Helps expand the trusted list over time.

- [ ] **Step 1: Write failing test**

```python
class TestDiscoverUntrustedDomains:
    def test_finds_untrusted_domain_with_title_anchor(self):
        html = '''<div>
            <a href="https://ssrn.com/1">Known Link</a>
            <a href="https://newjournal.org/article/123">Some Long Paper Title Here</a>
            <a href="https://twitter.com/x">Short</a>
        </div>'''
        domains = discover_untrusted_domains(html)
        assert 'newjournal.org' in domains
        assert 'twitter.com' not in domains  # anchor too short
        assert 'ssrn.com' not in domains  # already trusted

    def test_returns_empty_for_all_trusted(self):
        html = '<div><a href="https://ssrn.com/1">Paper Title</a></div>'
        assert discover_untrusted_domains(html) == {}
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement `discover_untrusted_domains` in `link_extractor.py`**

```python
def discover_untrusted_domains(html, min_anchor_len=20):
    """Find untrusted domains with paper-title-length anchor text.

    Returns {domain: count} of domains that have links with anchor text
    long enough to be a paper title but are not in the trusted list.
    Useful for expanding the trusted domain list over time.
    """
    from collections import Counter
    soup = BeautifulSoup(html, 'html.parser')
    for el in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        el.decompose()

    domains = Counter()
    for a in soup.find_all('a', href=True):
        url = a['href']
        if not url.startswith(('http://', 'https://')):
            continue
        if is_trusted_domain(url):
            continue
        anchor = a.get_text(strip=True)
        if len(anchor) >= min_anchor_len:
            try:
                hostname = urlparse(url).hostname
                if hostname:
                    domains[hostname] += 1
            except Exception:
                pass
    return dict(domains)
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Add `discover-domains` CLI command to `main.py`**

Add a new subparser and function:

```python
subparsers.add_parser('discover-domains', help='Scan stored HTML for untrusted domains with paper-title links')
```

And the handler:

```python
def discover_domains():
    """Scan all stored raw HTML to find untrusted domains that may host paper links."""
    from collections import Counter
    from link_extractor import discover_untrusted_domains

    rows = Database.fetch_all(
        "SELECT url_id, raw_html FROM html_content WHERE raw_html IS NOT NULL"
    )
    if not rows:
        logging.info("No raw HTML stored yet. Run 'make fetch' first.")
        return

    totals = Counter()
    for row in rows:
        domains = discover_untrusted_domains(row['raw_html'])
        totals.update(domains)

    if not totals:
        logging.info("No untrusted domains with paper-title-length anchors found.")
        return

    print(f"\nUntrusted domains with paper-title-length anchor text ({len(totals)} domains):\n")
    for domain, count in totals.most_common(30):
        print(f"  {count:4d}x  {domain}")
    print(f"\nTo add a domain, append it to TRUSTED_LINK_DOMAINS in link_extractor.py")
    print(f"or set the TRUSTED_LINK_DOMAINS env var (comma-separated).")
```

Wire it in the `if args.command` block:

```python
elif args.command == 'discover-domains':
    discover_domains()
```

- [ ] **Step 6: Add to `Makefile`**

```makefile
discover-domains:  ## Scan for untrusted domains that may host paper links
	poetry run python main.py discover-domains
```

- [ ] **Step 7: Run tests**: `poetry run pytest -v`
- [ ] **Step 8: Commit**

```bash
git add link_extractor.py main.py Makefile tests/test_link_extractor.py
git commit -m "feat: discover-domains command to find missing trusted domains"
```

---

## Task 6: Wire into Extraction Pipeline

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add import** at top of `main.py`:

```python
from link_extractor import match_and_save_paper_links
```

- [ ] **Step 2: Add to `extract_data_from_htmls()`** (after line ~56):

```python
if extracted_publications:
    Publication.save_publications(url, extracted_publications)
    match_and_save_paper_links(id, extracted_publications)
```

- [ ] **Step 3: Add to `_process_one_url()`** (after line ~75):

```python
if pubs:
    Publication.save_publications(url, pubs)
    match_and_save_paper_links(url_id, pubs)
```

- [ ] **Step 4: Add to `batch_check()`** (after line ~271):

```python
if validated:
    Publication.save_publications(url, validated)
    match_and_save_paper_links(url_id, validated)
```

- [ ] **Step 5: Run tests**: `poetry run pytest -v`
- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat: wire link matching into extraction pipeline"
```

---

## Task 7: API — Expose Links

**Files:**
- Modify: `api.py`
- Test: `tests/test_link_extractor.py`

- [ ] **Step 1: Write failing test**

```python
class TestApiPaperLinks:
    @patch("api.Database.fetch_all")
    @patch("api.Database.fetch_one")
    def test_publication_detail_includes_links(self, mock_fetch_one, mock_fetch_all, client):
        mock_fetch_one.return_value = {
            "id": 1, "title": "Test", "year": "2024", "venue": "AER",
            "source_url": "https://x.com", "discovered_at": "2024-01-01T00:00:00",
            "status": "working_paper", "draft_url": None,
            "draft_url_status": "unchecked", "abstract": None,
        }
        mock_fetch_all.side_effect = [
            [{"id": 1, "first_name": "J", "last_name": "S"}],  # authors
            [{"url": "https://ssrn.com/1", "link_type": "ssrn"}],  # links
        ]
        resp = client.get("/api/publications/1")
        assert resp.status_code == 200
        assert resp.json()["links"][0]["link_type"] == "ssrn"
```

Uses shared `client` fixture from `conftest.py`.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Add response model**

In `api.py`, after `AuthorResponse`:

```python
class PaperLinkResponse(BaseModel):
    url: str
    link_type: str | None
```

Add to `PublicationResponse`:

```python
links: list[PaperLinkResponse] = []
```

- [ ] **Step 4: Add `_get_links_for_publication` helper**

```python
def _get_links_for_publication(paper_id: int) -> list[dict]:
    rows = Database.fetch_all(
        "SELECT url, link_type FROM paper_links WHERE paper_id = %s", (paper_id,),
    )
    return [{"url": r['url'], "link_type": r['link_type']} for r in rows]


def _get_links_for_publications(pub_ids: list[int]) -> dict[int, list[dict]]:
    """Batch-fetch links for multiple publications."""
    if not pub_ids:
        return {}
    placeholders = ",".join(["%s"] * len(pub_ids))
    rows = Database.fetch_all(
        f"SELECT paper_id, url, link_type FROM paper_links WHERE paper_id IN ({placeholders})",
        tuple(pub_ids),
    )
    result: dict[int, list[dict]] = {pid: [] for pid in pub_ids}
    for row in rows:
        result[row['paper_id']].append({"url": row['url'], "link_type": row['link_type']})
    return result
```

- [ ] **Step 5: Update `_format_publication`** to accept and include links

Change signature to `_format_publication(row, authors, links=None)`:

```python
def _format_publication(row: dict, authors: list[dict], links: list[dict] | None = None) -> dict:
    result = {
        # ... existing fields unchanged ...
    }
    result["links"] = links or []
    return result
```

- [ ] **Step 6: Update `get_publication` endpoint** (line 536):

```python
authors = _get_authors_for_publication(publication_id)
links = _get_links_for_publication(publication_id)
result = _format_publication(row, authors, links)
```

- [ ] **Step 7: Update `list_publications` endpoint** (line 508-510):

```python
pub_ids = [row['paper_id'] for row in rows]
authors_by_pub = _get_authors_for_publications(pub_ids)
links_by_pub = _get_links_for_publications(pub_ids)
items = [_format_feed_event(row, authors_by_pub.get(row['paper_id'], []),
         links_by_pub.get(row['paper_id'], [])) for row in rows]
```

Update `_format_feed_event` to pass links through:

```python
def _format_feed_event(row: dict, authors: list[dict], links: list[dict] | None = None) -> dict:
    pub_row = {**row, "id": row["paper_id"]}
    result = _format_publication(pub_row, authors, links)
    ...
```

- [ ] **Step 8: Update `get_researcher` endpoint** (line 841):

```python
pub_ids = [pr['id'] for pr in pub_rows]
authors_by_pub = _get_authors_for_publications(pub_ids)
links_by_pub = _get_links_for_publications(pub_ids)
publications = [_format_publication(pr, authors_by_pub.get(pr['id'], []),
                links_by_pub.get(pr['id'], [])) for pr in pub_rows]
```

- [ ] **Step 9: Run → PASS**
- [ ] **Step 10: Run full test suite**: `poetry run pytest -v`
- [ ] **Step 11: Commit**

```bash
git add api.py tests/test_link_extractor.py
git commit -m "feat: expose paper links in API responses"
```

---

## Task 8: Frontend — Types + Display

**Files:**
- Modify: `app/src/lib/types.ts`
- Modify: `app/src/components/PublicationCard.tsx`

- [ ] **Step 1: Add types**

In `app/src/lib/types.ts`:

```typescript
export type LinkType =
  | "pdf" | "ssrn" | "nber" | "arxiv" | "doi"
  | "journal" | "drive" | "dropbox" | "repository" | "other";

export interface PaperLink {
  url: string;
  link_type: LinkType | null;
}
```

Add to `Publication` interface:

```typescript
links: PaperLink[];
```

- [ ] **Step 2: Add links to PublicationCard**

In `app/src/components/PublicationCard.tsx`, in the bottom row div (after the abstract toggle button, before the closing `</div>` at line ~119):

```tsx
{publication.links && publication.links.length > 0 &&
  publication.links.map((link) => (
    <a
      key={link.url}
      href={link.url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-violet-50 text-violet-700 hover:bg-violet-100 transition-colors"
    >
      {link.link_type === 'doi' ? 'DOI' : (link.link_type?.toUpperCase() || 'LINK')}
      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
      </svg>
    </a>
  ))
}
```

- [ ] **Step 3: Update test mocks** — add `links: []` to mock Publication objects in `app/src/components/__tests__/PublicationCard.test.tsx` and other test files

- [ ] **Step 4: Run**: `cd app && npx tsc --noEmit && npx jest`
- [ ] **Step 5: Commit**

```bash
git add app/src/lib/types.ts app/src/components/PublicationCard.tsx app/src/components/__tests__/ app/src/app/__tests__/
git commit -m "feat: display paper links in frontend"
```

---

## Task 9: Verification

- [ ] **Step 1: `make check`** — all checks pass
- [ ] **Step 2: Smoke test** (if dev DB available):

```bash
make seed        # migration
make fetch       # stores raw HTML
make parse       # extracts papers + matches links
make discover-domains  # shows untrusted domains to consider
```

Then verify:
```sql
SELECT pl.link_type, COUNT(*) FROM paper_links pl GROUP BY pl.link_type;
SELECT p.title, pl.url, pl.link_type FROM paper_links pl JOIN papers p ON p.id = pl.paper_id LIMIT 20;
```

- [ ] **Step 3: Clean up prototype files**

```bash
rm test_link_prototype.py test_link_prototype_v2.py test_link_prototype_v3.py
```
