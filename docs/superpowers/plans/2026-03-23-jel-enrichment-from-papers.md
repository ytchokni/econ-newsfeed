# JEL Enrichment from Paper Topics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve researcher JEL code classifications by fetching OpenAlex topics for their papers and mapping those topics to JEL codes, complementing the existing LLM-based bio classification.

**Architecture:** For papers already enriched with an `openalex_id`, fetch topic metadata from the OpenAlex `/works` API in batches. Store topics in a `paper_topics` table. Map topic names to JEL codes via keyword matching. Aggregate per researcher and merge (non-destructively) with existing bio-based JEL codes.

**Tech Stack:** OpenAlex API (existing integration in `openalex.py`), MySQL (`paper_topics` table), Python with `requests` (existing)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `topic_jel_map.py` | **Create** | Keyword-based OpenAlex topic name → JEL code mapping |
| `jel_enrichment.py` | **Create** | Orchestration pipeline: fetch → store → map → aggregate → merge |
| `tests/test_topic_jel_map.py` | **Create** | Tests for mapping logic |
| `tests/test_jel_enrichment.py` | **Create** | Tests for enrichment pipeline |
| `database/schema.py` | Modify:160-177,388-396 | Add `paper_topics` table definition + migration list |
| `database/jel.py` | Modify:1-87 (append) | Add paper topic DB operations + non-destructive JEL merge |
| `database/__init__.py` | Modify:48-54,98-103 | Expose new DB operations on Database facade |
| `openalex.py` | Modify:118-141 (extend `_parse_work`), append | Add topic extraction + `fetch_topics_batch()` |
| `tests/test_openalex.py` | Modify:91-121 (extend sample), append | Tests for topic extraction |
| `main.py` | Modify:387-412 | Add `enrich-jel` CLI command |
| `Makefile` | Modify | Add `enrich-jel` target |

---

## Task 1: Topic-to-JEL Mapping Module

**Files:**
- Create: `topic_jel_map.py`
- Test: `tests/test_topic_jel_map.py`

- [ ] **Step 1.1: Write failing tests for `map_topic_to_jel()`**

```python
# tests/test_topic_jel_map.py
"""Tests for OpenAlex topic → JEL code mapping."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from topic_jel_map import map_topic_to_jel, map_topics_to_jel


class TestMapTopicToJel:
    def test_labor_topic_maps_to_j(self):
        assert "J" in map_topic_to_jel("Labor market dynamics and wage inequality")

    def test_monetary_policy_maps_to_e(self):
        assert "E" in map_topic_to_jel("Monetary Policy and Economic Impact")

    def test_trade_maps_to_f(self):
        assert "F" in map_topic_to_jel("Global trade and economics")

    def test_migration_maps_to_j_and_f(self):
        codes = map_topic_to_jel("Migration and Labor Dynamics")
        assert "J" in codes
        assert "F" in codes

    def test_financial_maps_to_g(self):
        assert "G" in map_topic_to_jel("Financial market volatility and risk")

    def test_education_maps_to_i(self):
        assert "I" in map_topic_to_jel("Education policy and outcomes")

    def test_environmental_maps_to_q(self):
        assert "Q" in map_topic_to_jel("Environmental regulation and climate change")

    def test_urban_maps_to_r(self):
        assert "R" in map_topic_to_jel("Urban development and housing markets")

    def test_unrelated_topic_returns_empty(self):
        assert map_topic_to_jel("Quantum computing advances") == []

    def test_case_insensitive(self):
        assert "J" in map_topic_to_jel("LABOR MARKET DYNAMICS")


class TestMapTopicsToJel:
    def test_aggregates_multiple_topics(self):
        topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "Migration and policy", "score": 0.95},
            {"topic_name": "International trade flows", "score": 0.80},
        ]
        result = map_topics_to_jel(topics)
        assert "J" in result
        assert "F" in result

    def test_higher_score_wins(self):
        topics = [
            {"topic_name": "Labor market", "score": 0.60},
            {"topic_name": "Wage inequality", "score": 0.95},
        ]
        result = map_topics_to_jel(topics)
        assert result["J"] == 0.95

    def test_empty_topics_returns_empty(self):
        assert map_topics_to_jel([]) == {}

    def test_handles_display_name_key(self):
        """OpenAlex raw response uses 'display_name', stored data uses 'topic_name'."""
        topics = [{"display_name": "Monetary Policy", "score": 0.9}]
        result = map_topics_to_jel(topics)
        assert "E" in result
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_topic_jel_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'topic_jel_map'`

- [ ] **Step 1.3: Implement `topic_jel_map.py`**

```python
# topic_jel_map.py
"""Map OpenAlex topic names to JEL codes via keyword matching.

OpenAlex assigns topics to works with a hierarchical taxonomy
(domain > field > subfield > topic). This module maps the topic
display names to JEL (Journal of Economic Literature) codes using
keyword matching, enabling JEL enrichment from paper metadata.
"""

# Ordered by specificity — more specific patterns checked first.
# Each tuple: (keyword_to_match_in_lowercase, list_of_jel_codes)
_KEYWORD_JEL_RULES: list[tuple[str, list[str]]] = [
    # J — Labor and Demographic Economics
    ("labor market", ["J"]),
    ("labour market", ["J"]),
    ("wage", ["J"]),
    ("employment effect", ["J"]),
    ("unemployment", ["J"]),
    ("immigration", ["J"]),
    ("demographic", ["J"]),
    ("fertility", ["J"]),
    ("pension", ["J", "H"]),
    ("human capital", ["J", "I"]),
    # F — International Economics
    ("international trade", ["F"]),
    ("trade and", ["F"]),
    ("trade flow", ["F"]),
    ("exchange rate", ["F"]),
    ("globalization", ["F"]),
    ("migration", ["J", "F"]),
    ("foreign direct investment", ["F"]),
    # E — Macroeconomics and Monetary Economics
    ("monetary policy", ["E"]),
    ("inflation", ["E"]),
    ("central bank", ["E"]),
    ("macroeconomic", ["E"]),
    ("business cycle", ["E"]),
    ("interest rate", ["E"]),
    # G — Financial Economics
    ("financial market", ["G"]),
    ("banking", ["G"]),
    ("stock market", ["G"]),
    ("asset pricing", ["G"]),
    ("corporate finance", ["G"]),
    ("credit risk", ["G"]),
    ("insurance market", ["G"]),
    ("portfolio", ["G"]),
    # H — Public Economics
    ("tax", ["H"]),
    ("public finance", ["H"]),
    ("public good", ["H"]),
    ("government spending", ["H"]),
    ("fiscal policy", ["E", "H"]),
    ("public debt", ["H", "E"]),
    # I — Health, Education, and Welfare
    ("health economics", ["I"]),
    ("education", ["I"]),
    ("welfare", ["I"]),
    ("poverty", ["I", "O"]),
    ("health care", ["I"]),
    ("schooling", ["I"]),
    # O — Economic Development, Innovation, Technological Change, and Growth
    ("economic development", ["O"]),
    ("economic growth", ["O"]),
    ("innovation", ["O"]),
    ("technological change", ["O"]),
    ("technology adoption", ["O"]),
    ("entrepreneurship", ["L", "O"]),
    # D — Microeconomics
    ("behavioral economics", ["D"]),
    ("consumer", ["D"]),
    ("household decision", ["D"]),
    ("auction", ["D"]),
    ("inequality", ["D", "I"]),
    ("game theory", ["C", "D"]),
    # L — Industrial Organization
    ("industrial organization", ["L"]),
    ("firm", ["L"]),
    ("market structure", ["L"]),
    ("competition", ["L"]),
    ("antitrust", ["L", "K"]),
    ("market power", ["L"]),
    # C — Mathematical and Quantitative Methods
    ("econometric", ["C"]),
    ("experimental economics", ["C"]),
    ("statistical", ["C"]),
    ("causal inference", ["C"]),
    # Q — Agricultural and Natural Resource Economics
    ("environmental", ["Q"]),
    ("agricultural", ["Q"]),
    ("natural resource", ["Q"]),
    ("climate", ["Q"]),
    ("energy", ["Q"]),
    # R — Urban, Rural, Regional, Real Estate, and Transportation Economics
    ("urban", ["R"]),
    ("housing", ["R"]),
    ("regional", ["R"]),
    ("transportation", ["R"]),
    ("real estate", ["R"]),
    # K — Law and Economics
    ("law and economics", ["K"]),
    ("crime", ["K"]),
    ("regulation", ["K", "L"]),
    ("legal", ["K"]),
    # N — Economic History
    ("economic history", ["N"]),
    ("historical", ["N"]),
    # P — Economic Systems
    ("political economy", ["P", "H"]),
    ("economic system", ["P"]),
    # M — Business Administration
    ("marketing", ["M"]),
    ("accounting", ["M"]),
    ("management", ["M"]),
]


def map_topic_to_jel(topic_name: str) -> list[str]:
    """Map a single OpenAlex topic name to JEL codes via keyword matching.

    Returns list of unique JEL codes (e.g. ["J", "F"]) or empty list if no match.
    """
    lower = topic_name.lower()
    codes: list[str] = []
    for keyword, jel_codes in _KEYWORD_JEL_RULES:
        if keyword in lower:
            for code in jel_codes:
                if code not in codes:
                    codes.append(code)
    return codes


def map_topics_to_jel(topics: list[dict]) -> dict[str, float]:
    """Map multiple OpenAlex topics to JEL codes with scores.

    Accepts dicts with either 'topic_name' or 'display_name' key.
    Returns dict of {jel_code: max_score} — highest score across matching topics.
    """
    jel_scores: dict[str, float] = {}
    for topic in topics:
        name = topic.get("topic_name") or topic.get("display_name", "")
        score = float(topic.get("score", 0.5))
        codes = map_topic_to_jel(name)
        for code in codes:
            if code not in jel_scores or score > jel_scores[code]:
                jel_scores[code] = score
    return jel_scores
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_topic_jel_map.py -v`
Expected: All PASS

- [ ] **Step 1.5: Commit**

```bash
git add topic_jel_map.py tests/test_topic_jel_map.py
git commit -m "feat: add OpenAlex topic → JEL code keyword mapping"
```

---

## Task 2: Database Schema — `paper_topics` Table

**Files:**
- Modify: `database/schema.py:160-177,388-396`

- [ ] **Step 2.1: Write failing test for `paper_topics` table definition**

```python
# Append to tests/test_topic_jel_map.py (or a new section)

class TestPaperTopicsSchema:
    def test_table_definition_exists(self):
        from database.schema import _TABLE_DEFINITIONS
        assert "paper_topics" in _TABLE_DEFINITIONS
        ddl = _TABLE_DEFINITIONS["paper_topics"]
        assert "paper_id" in ddl
        assert "openalex_topic_id" in ddl
        assert "topic_name" in ddl
        assert "score" in ddl
        assert "FOREIGN KEY" in ddl
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `poetry run pytest tests/test_topic_jel_map.py::TestPaperTopicsSchema -v`
Expected: FAIL — `paper_topics` not in `_TABLE_DEFINITIONS`

- [ ] **Step 2.3: Add `paper_topics` table to schema.py**

Add after the `researcher_jel_codes` entry (around line 177) in `_TABLE_DEFINITIONS`:

```python
    "paper_topics": """
        CREATE TABLE IF NOT EXISTS paper_topics (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paper_id INT NOT NULL,
            openalex_topic_id VARCHAR(255) NOT NULL,
            topic_name VARCHAR(500) NOT NULL,
            subfield_name VARCHAR(255) DEFAULT NULL,
            field_name VARCHAR(255) DEFAULT NULL,
            domain_name VARCHAR(255) DEFAULT NULL,
            score DECIMAL(5,4) DEFAULT NULL,
            UNIQUE KEY uq_paper_topic (paper_id, openalex_topic_id),
            INDEX idx_paper_id (paper_id),
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
```

Also add `"paper_topics"` to the `_ALL_TABLES` list (around line 396) in `create_tables()`:

```python
    _ALL_TABLES = [
        "researchers", "researcher_urls", "papers", "html_content",
        "authorship", "research_fields", "researcher_fields",
        "jel_codes", "researcher_jel_codes",
        "scrape_log", "researcher_snapshots", "paper_snapshots",
        "paper_urls", "llm_usage", "feed_events", "batch_jobs",
        "openalex_coauthors",
        "paper_links",
        "paper_topics",  # <-- add this
    ]
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `poetry run pytest tests/test_topic_jel_map.py::TestPaperTopicsSchema -v`
Expected: PASS

- [ ] **Step 2.5: Run full test suite to verify no regressions**

Run: `poetry run pytest -v`
Expected: All previously-passing tests still pass

- [ ] **Step 2.6: Commit**

```bash
git add database/schema.py tests/test_topic_jel_map.py
git commit -m "feat: add paper_topics table for OpenAlex topic storage"
```

---

## Task 3: Database Operations for Paper Topics

**Files:**
- Modify: `database/jel.py` (append new functions)
- Modify: `database/__init__.py` (expose on facade)
- Test: `tests/test_jel_enrichment.py`

- [ ] **Step 3.1: Write failing tests for new DB operations**

```python
# tests/test_jel_enrichment.py
"""Tests for JEL enrichment from paper topics."""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test_econ_newsfeed")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("SCRAPE_API_KEY", "test-key")

from unittest.mock import MagicMock, patch, call


class TestSavePaperTopics:
    """Tests for database.jel.save_paper_topics."""

    def test_inserts_topics_for_paper(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        topics = [
            {
                "openalex_topic_id": "T10001",
                "topic_name": "Labor market dynamics",
                "subfield_name": "Economics and Econometrics",
                "field_name": "Economics, Econometrics and Finance",
                "domain_name": "Social Sciences",
                "score": 0.99,
            },
            {
                "openalex_topic_id": "T10002",
                "topic_name": "Migration and policy",
                "subfield_name": "Sociology and Political Science",
                "field_name": "Social Sciences",
                "domain_name": "Social Sciences",
                "score": 0.85,
            },
        ]

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import save_paper_topics
            save_paper_topics(paper_id=42, topics=topics)

        # Should delete existing + insert each topic
        assert mock_cursor.execute.call_count == 3  # 1 delete + 2 inserts
        delete_sql = mock_cursor.execute.call_args_list[0][0][0]
        assert "DELETE FROM paper_topics" in delete_sql
        mock_conn.commit.assert_called_once()


class TestGetPaperTopicsForResearcher:
    """Tests for database.jel.get_paper_topics_for_researcher."""

    def test_returns_topics_for_researcher_papers(self):
        mock_rows = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "Migration and policy", "score": 0.85},
        ]
        with patch("database.jel.fetch_all", return_value=mock_rows) as mock_fetch:
            from database.jel import get_paper_topics_for_researcher
            result = get_paper_topics_for_researcher(researcher_id=1)

        assert len(result) == 2
        sql = mock_fetch.call_args[0][0]
        assert "paper_topics" in sql
        assert "authorship" in sql


class TestGetPapersNeedingTopics:
    """Tests for database.jel.get_papers_needing_topics."""

    def test_returns_papers_with_openalex_id_but_no_topics(self):
        mock_rows = [
            {"id": 1, "openalex_id": "W123"},
            {"id": 2, "openalex_id": "W456"},
        ]
        with patch("database.jel.fetch_all", return_value=mock_rows) as mock_fetch:
            from database.jel import get_papers_needing_topics
            result = get_papers_needing_topics()

        assert len(result) == 2
        sql = mock_fetch.call_args[0][0]
        assert "openalex_id IS NOT NULL" in sql
        assert "LEFT JOIN paper_topics" in sql


class TestAddResearcherJelCodes:
    """Tests for database.jel.add_researcher_jel_codes (non-destructive)."""

    def test_inserts_without_deleting_existing(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("database.jel.get_connection", return_value=mock_conn):
            from database.jel import add_researcher_jel_codes
            add_researcher_jel_codes(researcher_id=1, jel_codes=["J", "F"])

        # Should NOT contain DELETE — non-destructive
        all_sql = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert not any("DELETE" in sql for sql in all_sql)
        # Should use INSERT INTO (relies on IntegrityError catch for duplicates)
        assert any("INSERT INTO researcher_jel_codes" in sql for sql in all_sql)
        assert mock_cursor.execute.call_count == 2  # One per code
        mock_conn.commit.assert_called_once()
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_jel_enrichment.py -v`
Expected: FAIL — `ImportError: cannot import name 'save_paper_topics'`

- [ ] **Step 3.3: Implement DB operations in `database/jel.py`**

Append the following functions to `database/jel.py`:

```python
def save_paper_topics(paper_id: int, topics: list[dict]) -> None:
    """Store OpenAlex topics for a paper. Replaces existing topics."""
    from database.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM paper_topics WHERE paper_id = %s", (paper_id,)
            )
            for topic in topics:
                cursor.execute(
                    """INSERT INTO paper_topics
                       (paper_id, openalex_topic_id, topic_name, subfield_name,
                        field_name, domain_name, score)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        paper_id,
                        topic["openalex_topic_id"],
                        topic["topic_name"],
                        topic.get("subfield_name"),
                        topic.get("field_name"),
                        topic.get("domain_name"),
                        topic.get("score"),
                    ),
                )
            conn.commit()


def get_paper_topics_for_researcher(researcher_id: int) -> list[dict]:
    """Get all OpenAlex topics for papers authored by a researcher."""
    return fetch_all(
        """SELECT pt.topic_name, pt.score
           FROM paper_topics pt
           JOIN papers p ON p.id = pt.paper_id
           JOIN authorship a ON a.publication_id = p.id
           WHERE a.researcher_id = %s
           ORDER BY pt.score DESC""",
        (researcher_id,),
    )


def get_papers_needing_topics() -> list[dict]:
    """Get papers with openalex_id but no topics stored yet."""
    return fetch_all(
        """SELECT p.id, p.openalex_id
           FROM papers p
           LEFT JOIN paper_topics pt ON pt.paper_id = p.id
           WHERE p.openalex_id IS NOT NULL
             AND pt.id IS NULL"""
    )


def add_researcher_jel_codes(researcher_id: int, jel_codes: list[str]) -> None:
    """Add JEL codes to a researcher without removing existing ones.

    Skips codes already assigned (duplicate key, errno 1062).
    Logs a warning for unknown JEL codes (FK violation, errno 1452).
    """
    from database.connection import get_connection
    from mysql.connector.errors import IntegrityError

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            for code in jel_codes:
                try:
                    cursor.execute(
                        """INSERT INTO researcher_jel_codes
                           (researcher_id, jel_code, classified_at)
                           VALUES (%s, %s, %s)""",
                        (researcher_id, code.upper().strip(), now),
                    )
                except IntegrityError as e:
                    if getattr(e, "errno", None) == 1062:
                        pass  # Already assigned — skip silently
                    else:
                        logging.warning(
                            "Skipped unknown JEL code '%s' for researcher %d",
                            code,
                            researcher_id,
                        )
            conn.commit()
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_jel_enrichment.py -v`
Expected: All PASS

- [ ] **Step 3.5: Expose new operations on Database facade**

In `database/__init__.py`, add imports (in the `from database.jel import` block):

```python
    save_paper_topics as _save_paper_topics,
    get_paper_topics_for_researcher as _get_paper_topics_for_researcher,
    get_papers_needing_topics as _get_papers_needing_topics,
    add_researcher_jel_codes as _add_researcher_jel_codes,
```

And in the `Database` class (in the `# JEL codes` section):

```python
    save_paper_topics = staticmethod(_save_paper_topics)
    get_paper_topics_for_researcher = staticmethod(_get_paper_topics_for_researcher)
    get_papers_needing_topics = staticmethod(_get_papers_needing_topics)
    add_researcher_jel_codes = staticmethod(_add_researcher_jel_codes)
```

- [ ] **Step 3.6: Run full test suite**

Run: `poetry run pytest -v`
Expected: All previously-passing tests still pass

- [ ] **Step 3.7: Commit**

```bash
git add database/jel.py database/__init__.py tests/test_jel_enrichment.py
git commit -m "feat: add DB operations for paper topics and non-destructive JEL merge"
```

---

## Task 4: OpenAlex Topic Fetching

**Files:**
- Modify: `openalex.py:118-141` (extend `_parse_work`), append `fetch_topics_batch()`
- Modify: `tests/test_openalex.py:91-121` (extend sample response), append new tests

- [ ] **Step 4.1: Write failing tests for topic extraction and batch fetch**

Append to `tests/test_openalex.py`:

```python
SAMPLE_TOPICS = [
    {
        "id": "https://openalex.org/T10001",
        "display_name": "Labor market dynamics and wage inequality",
        "score": 0.99,
        "subfield": {"id": "https://openalex.org/subfields/2002", "display_name": "Economics and Econometrics"},
        "field": {"id": "https://openalex.org/fields/20", "display_name": "Economics, Econometrics and Finance"},
        "domain": {"id": "https://openalex.org/domains/2", "display_name": "Social Sciences"},
    },
    {
        "id": "https://openalex.org/T10002",
        "display_name": "Migration and Labor Dynamics",
        "score": 0.85,
        "subfield": {"id": "https://openalex.org/subfields/3312", "display_name": "Sociology and Political Science"},
        "field": {"id": "https://openalex.org/fields/33", "display_name": "Social Sciences"},
        "domain": {"id": "https://openalex.org/domains/2", "display_name": "Social Sciences"},
    },
]


class TestParseWorkTopics:
    """Tests for topic extraction in _parse_work."""

    def test_parse_work_includes_topics(self):
        work = {
            **SAMPLE_OPENALEX_RESPONSE["results"][0],
            "topics": SAMPLE_TOPICS,
        }
        from openalex import _parse_work
        result = _parse_work(work)
        assert "topics" in result
        assert len(result["topics"]) == 2
        assert result["topics"][0]["openalex_topic_id"] == "T10001"
        assert result["topics"][0]["topic_name"] == "Labor market dynamics and wage inequality"
        assert result["topics"][0]["subfield_name"] == "Economics and Econometrics"
        assert result["topics"][0]["score"] == 0.99

    def test_parse_work_handles_no_topics(self):
        work = SAMPLE_OPENALEX_RESPONSE["results"][0]  # No topics key
        from openalex import _parse_work
        result = _parse_work(work)
        assert result["topics"] == []


class TestFetchTopicsBatch:
    """Tests for openalex.fetch_topics_batch."""

    def test_fetches_topics_for_multiple_works(self):
        api_response = {
            "results": [
                {"id": "https://openalex.org/W123", "topics": SAMPLE_TOPICS},
                {"id": "https://openalex.org/W456", "topics": SAMPLE_TOPICS[:1]},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200

        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            from openalex import fetch_topics_batch
            result = fetch_topics_batch(["W123", "W456"])

        assert "W123" in result
        assert "W456" in result
        assert len(result["W123"]) == 2
        assert len(result["W456"]) == 1

    def test_returns_empty_on_api_error(self):
        import requests as req
        with patch("openalex._get_session") as mock_session:
            mock_session.return_value.get.side_effect = req.RequestException("timeout")
            from openalex import fetch_topics_batch
            result = fetch_topics_batch(["W123"])

        assert result == {}

    def test_handles_empty_input(self):
        from openalex import fetch_topics_batch
        result = fetch_topics_batch([])
        assert result == {}
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_openalex.py::TestParseWorkTopics tests/test_openalex.py::TestFetchTopicsBatch -v`
Expected: FAIL — `topics` key missing from `_parse_work` result / `fetch_topics_batch` not defined

- [ ] **Step 4.3: Extend `_parse_work()` to include topics**

In `openalex.py`, modify `_parse_work()` (around line 118) to extract topics:

```python
def _parse_work(work: dict) -> dict:
    """Parse an OpenAlex work object into our enrichment dict."""
    doi = _strip_prefix(work.get("doi"), _DOI_PREFIX)
    openalex_id = _strip_prefix(work.get("id"), _OPENALEX_PREFIX)

    coauthors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        coauthors.append({
            "display_name": author.get("display_name", ""),
            "openalex_author_id": _strip_prefix(author.get("id"), _OPENALEX_PREFIX),
        })

    abstract = None
    inverted_index = work.get("abstract_inverted_index")
    if inverted_index:
        abstract = reconstruct_abstract(inverted_index)

    topics = []
    for t in work.get("topics", []):
        topics.append({
            "openalex_topic_id": _strip_prefix(t.get("id"), _OPENALEX_PREFIX),
            "topic_name": t.get("display_name", ""),
            "subfield_name": (t.get("subfield") or {}).get("display_name"),
            "field_name": (t.get("field") or {}).get("display_name"),
            "domain_name": (t.get("domain") or {}).get("display_name"),
            "score": t.get("score"),
        })

    return {
        "doi": doi,
        "openalex_id": openalex_id,
        "coauthors": coauthors,
        "abstract": abstract,
        "topics": topics,
    }
```

- [ ] **Step 4.4: Implement `fetch_topics_batch()`**

Append to `openalex.py`:

```python
def fetch_topics_batch(openalex_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch topics for multiple works from OpenAlex.

    Returns dict mapping openalex_id -> list of topic dicts.
    Processes in chunks of 50 (OpenAlex filter limit).
    """
    if not openalex_ids:
        return {}

    if not _check_budget():
        logger.info("OpenAlex daily budget exhausted, skipping topic fetch")
        return {}

    session = _get_session()
    result: dict[str, list[dict]] = {}

    for i in range(0, len(openalex_ids), 50):
        if not _check_budget():
            logger.info("OpenAlex daily budget reached, stopping topic fetch")
            break

        chunk = openalex_ids[i : i + 50]
        full_ids = "|".join(f"https://openalex.org/{oid}" for oid in chunk)
        params: dict[str, str | int] = {
            "filter": f"openalex:{full_ids}",
            "per_page": 50,
            "select": "id,topics",
        }
        if MAILTO:
            params["mailto"] = MAILTO

        try:
            resp = _get_with_retry(session, f"{OPENALEX_BASE_URL}/works", params)
            resp.raise_for_status()
            _increment_budget()

            for work in resp.json().get("results", []):
                oa_id = _strip_prefix(work.get("id"), _OPENALEX_PREFIX)
                topics = []
                for t in work.get("topics", []):
                    topics.append({
                        "openalex_topic_id": _strip_prefix(t.get("id"), _OPENALEX_PREFIX),
                        "topic_name": t.get("display_name", ""),
                        "subfield_name": (t.get("subfield") or {}).get("display_name"),
                        "field_name": (t.get("field") or {}).get("display_name"),
                        "domain_name": (t.get("domain") or {}).get("display_name"),
                        "score": t.get("score"),
                    })
                if topics and oa_id:
                    result[oa_id] = topics
        except (requests.RequestException, ValueError) as e:
            logger.warning("Failed to fetch topics for chunk starting at %d: %s", i, e)

        if i + 50 < len(openalex_ids):
            time.sleep(0.5)

    return result
```

- [ ] **Step 4.5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_openalex.py -v`
Expected: All PASS (including existing tests)

- [ ] **Step 4.6: Commit**

```bash
git add openalex.py tests/test_openalex.py
git commit -m "feat: extract OpenAlex topics from works and add batch fetch"
```

---

## Task 5: JEL Enrichment Pipeline

**Files:**
- Create: `jel_enrichment.py`
- Modify: `tests/test_jel_enrichment.py` (append pipeline tests)

- [ ] **Step 5.1: Write failing tests for the enrichment pipeline**

Append to `tests/test_jel_enrichment.py`:

```python
from topic_jel_map import map_topic_to_jel


class TestAggregateJelForResearcher:
    """Tests for jel_enrichment.aggregate_jel_for_researcher."""

    def test_aggregates_jel_from_paper_topics(self):
        mock_topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "Labor market dynamics", "score": 0.95},  # Duplicate paper
            {"topic_name": "Migration and policy", "score": 0.85},
            {"topic_name": "International trade flows", "score": 0.80},
        ]
        with patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=mock_topics):
            from jel_enrichment import aggregate_jel_for_researcher
            codes = aggregate_jel_for_researcher(researcher_id=1)

        assert "J" in codes  # Labor + Migration
        assert "F" in codes  # Migration + Trade
        assert len(codes) <= 5

    def test_returns_empty_for_no_topics(self):
        with patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=[]):
            from jel_enrichment import aggregate_jel_for_researcher
            codes = aggregate_jel_for_researcher(researcher_id=1)

        assert codes == []

    def test_limits_to_top_5(self):
        # Create topics that would generate > 5 JEL codes
        mock_topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},        # J
            {"topic_name": "International trade flows", "score": 0.95},    # F
            {"topic_name": "Monetary Policy impact", "score": 0.90},       # E
            {"topic_name": "Financial market risk", "score": 0.85},        # G
            {"topic_name": "Public finance and tax", "score": 0.80},       # H
            {"topic_name": "Environmental regulation", "score": 0.75},     # Q
            {"topic_name": "Urban housing markets", "score": 0.70},        # R
        ]
        with patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=mock_topics):
            from jel_enrichment import aggregate_jel_for_researcher
            codes = aggregate_jel_for_researcher(researcher_id=1)

        assert len(codes) <= 5


class TestEnrichJelFromPapers:
    """Tests for jel_enrichment.enrich_jel_from_papers."""

    def test_full_pipeline(self):
        papers_needing = [
            {"id": 1, "openalex_id": "W123"},
            {"id": 2, "openalex_id": "W456"},
        ]
        topics_by_id = {
            "W123": [
                {"openalex_topic_id": "T1", "topic_name": "Labor market dynamics",
                 "subfield_name": "Econ", "field_name": "Econ", "domain_name": "SS", "score": 0.99},
            ],
            "W456": [
                {"openalex_topic_id": "T2", "topic_name": "International trade",
                 "subfield_name": "Econ", "field_name": "Econ", "domain_name": "SS", "score": 0.90},
            ],
        }
        researchers = [
            {"id": 10, "first_name": "Jane", "last_name": "Doe"},
        ]
        researcher_topics = [
            {"topic_name": "Labor market dynamics", "score": 0.99},
            {"topic_name": "International trade", "score": 0.90},
        ]

        with (
            patch("jel_enrichment.Database.get_papers_needing_topics", return_value=papers_needing),
            patch("jel_enrichment.fetch_topics_batch", return_value=topics_by_id),
            patch("jel_enrichment.Database.save_paper_topics") as mock_save_topics,
            patch("jel_enrichment.Database.fetch_all", return_value=researchers),
            patch("jel_enrichment.Database.get_paper_topics_for_researcher", return_value=researcher_topics),
            patch("jel_enrichment.Database.add_researcher_jel_codes") as mock_add_jel,
        ):
            from jel_enrichment import enrich_jel_from_papers
            count = enrich_jel_from_papers()

        assert count == 1
        assert mock_save_topics.call_count == 2
        mock_add_jel.assert_called_once()
        # Verify JEL codes include J and F
        jel_codes_arg = mock_add_jel.call_args[0][1]
        assert "J" in jel_codes_arg
        assert "F" in jel_codes_arg

    def test_returns_zero_when_no_papers(self):
        with (
            patch("jel_enrichment.Database.get_papers_needing_topics", return_value=[]),
            patch("jel_enrichment.Database.fetch_all", return_value=[]),
        ):
            from jel_enrichment import enrich_jel_from_papers
            count = enrich_jel_from_papers()

        assert count == 0
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_jel_enrichment.py::TestAggregateJelForResearcher tests/test_jel_enrichment.py::TestEnrichJelFromPapers -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jel_enrichment'`

- [ ] **Step 5.3: Implement `jel_enrichment.py`**

```python
# jel_enrichment.py
"""JEL enrichment pipeline: derive researcher JEL codes from paper topics.

Fetches OpenAlex topics for papers, maps them to JEL codes, and
merges with existing bio-based JEL classifications.
"""
import logging
from collections import Counter

from database import Database
from topic_jel_map import map_topic_to_jel

logger = logging.getLogger(__name__)


def aggregate_jel_for_researcher(researcher_id: int) -> list[str]:
    """Aggregate JEL codes from a researcher's paper topics.

    Returns up to 5 JEL codes, ranked by weighted frequency across papers.
    """
    topics = Database.get_paper_topics_for_researcher(researcher_id)
    jel_counter: Counter = Counter()
    for topic in topics:
        codes = map_topic_to_jel(topic["topic_name"])
        score = float(topic.get("score") or 0.5)
        for code in codes:
            jel_counter[code] += score

    return [code for code, _ in jel_counter.most_common(5)]


def enrich_jel_from_papers() -> int:
    """Main pipeline: fetch topics, map to JEL, aggregate per researcher.

    Returns number of researchers enriched.
    """
    from openalex import fetch_topics_batch

    # Step 1: Fetch and store topics for papers missing them
    papers = Database.get_papers_needing_topics()
    if papers:
        logger.info("Fetching topics for %d papers from OpenAlex", len(papers))
        openalex_ids = [p["openalex_id"] for p in papers]
        topics_by_id = fetch_topics_batch(openalex_ids)
        stored = 0
        for paper in papers:
            topics = topics_by_id.get(paper["openalex_id"], [])
            if topics:
                Database.save_paper_topics(paper["id"], topics)
                stored += 1
        logger.info("Stored topics for %d/%d papers", stored, len(papers))

    # Step 2: Aggregate and merge per researcher
    researchers = Database.fetch_all(
        """SELECT DISTINCT r.id, r.first_name, r.last_name
           FROM researchers r
           JOIN authorship a ON a.researcher_id = r.id
           JOIN papers p ON p.id = a.publication_id
           JOIN paper_topics pt ON pt.paper_id = p.id
           ORDER BY r.id"""
    )
    enriched = 0
    for r in researchers:
        codes = aggregate_jel_for_researcher(r["id"])
        if codes:
            Database.add_researcher_jel_codes(r["id"], codes)
            enriched += 1
            logger.info(
                "Enriched JEL for %s %s (id=%d): %s",
                r["first_name"],
                r["last_name"],
                r["id"],
                ", ".join(codes),
            )

    logger.info(
        "JEL enrichment done: %d/%d researchers enriched", enriched, len(researchers)
    )
    return enriched
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_jel_enrichment.py -v`
Expected: All PASS

- [ ] **Step 5.5: Run full test suite**

Run: `poetry run pytest -v`
Expected: All previously-passing tests still pass

- [ ] **Step 5.6: Commit**

```bash
git add jel_enrichment.py tests/test_jel_enrichment.py
git commit -m "feat: add JEL enrichment pipeline from paper topics"
```

---

## Task 6: CLI Command and Makefile Integration

**Files:**
- Modify: `main.py:387-412`
- Modify: `Makefile`

- [ ] **Step 6.1: Write failing test for CLI command**

Append to `tests/test_jel_enrichment.py`:

```python
class TestCliIntegration:
    """Verify the CLI command is registered."""

    def test_enrich_jel_command_registered(self):
        """The 'enrich-jel' subcommand should be in main.py source."""
        import inspect
        import main as main_mod
        source = inspect.getsource(main_mod)
        assert "enrich-jel" in source
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `poetry run pytest tests/test_jel_enrichment.py::TestCliIntegration -v`
Expected: FAIL — `enrich-jel` not found in source

- [ ] **Step 6.3: Add `enrich-jel` command to `main.py`**

In `main.py`, add after the `enrich` parser (around line 388):

```python
    subparsers.add_parser('enrich-jel', help='Enrich researcher JEL codes from paper topics via OpenAlex')
```

And in the command dispatch block (around line 410):

```python
    elif args.command == 'enrich-jel':
        Database.create_tables()
        from jel_enrichment import enrich_jel_from_papers
        enrich_jel_from_papers()
```

- [ ] **Step 6.4: Add Makefile target**

Add to `Makefile`:

```makefile
enrich-jel:  ## Enrich researcher JEL codes from paper topics
	poetry run python main.py enrich-jel
```

- [ ] **Step 6.5: Run test to verify it passes**

Run: `poetry run pytest tests/test_jel_enrichment.py::TestCliIntegration -v`
Expected: PASS

- [ ] **Step 6.6: Run full test suite**

Run: `poetry run pytest -v`
Expected: All previously-passing tests still pass (344+)

- [ ] **Step 6.7: Commit**

```bash
git add main.py Makefile tests/test_jel_enrichment.py
git commit -m "feat: add enrich-jel CLI command and Makefile target"
```

---

## Task 7: Verify Existing Tests Unaffected

- [ ] **Step 7.1: Run the full test suite one final time**

Run: `poetry run pytest -v`
Expected: Same pass/fail counts as baseline (344 passed, 5 pre-existing failures)

- [ ] **Step 7.2: Verify `_parse_work` change doesn't break `enrich_publication`**

The `enrich_publication()` function passes `result["doi"]`, `result["openalex_id"]`, `result["coauthors"]`, `result["abstract"]` to `update_openalex_data()`. The new `result["topics"]` key is simply ignored by existing callers. Verify this by running:

Run: `poetry run pytest tests/test_openalex.py -v`
Expected: All PASS including existing `TestEnrichPublication` tests

- [ ] **Step 7.3: Final commit with all changes**

If any tests needed fixing, commit those fixes. Otherwise, the feature is complete.

```bash
git log --oneline -6  # Verify commit history looks clean
```
