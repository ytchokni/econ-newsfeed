# Phase 1: Code Quality & Architecture Review

## Code Quality Findings

### Critical (5)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| CQ-1 | SQL injection via f-string in database name | `database.py:24` | Database name interpolated directly into DDL without sanitization |
| CQ-2 | Connection/cursor leak in `create_database()` | `database.py:17-31` | `finally` block references `conn` which may be unbound if `connect()` fails, causing `NameError` |
| CQ-3 | `get_connection()` returns `None` crashing callers | `database.py:34-43` | Callers use `with Database.get_connection() as conn:` which raises `AttributeError` on `None` -- not caught by `except Error` |
| CQ-4 | Unreliable transaction rollback in `save_publications` | `publication.py:57-61` | Checks `if 'conn' in locals()` but `conn` from `with` block may already be closed |
| CQ-5 | No validation of OpenAI-extracted data before DB insert | `publication.py:20-62` | LLM output parsed and inserted directly; missing keys or wrong shapes cause unhandled crashes |

### High (11)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| CQ-6 | New DB connection opened per query (no pooling) | `database.py:34-43` | Every query opens/closes a TCP connection; during imports this means 2+ connections per CSV row |
| CQ-7 | Mixed connection management breaks transaction integrity | `publication.py:20-62` | `save_publications` opens its own connection but calls `get_researcher_id()` which opens a separate connection |
| CQ-8 | `datetime.utcnow()` deprecated, timezone-naive | `html_fetcher.py:66` | Creates ambiguous timestamps; inconsistent with `datetime.now()` in `publication.py:33` |
| CQ-9 | OpenAI client instantiated per function call | `publication.py:90` | Discards HTTP connection pool each time |
| CQ-10 | 4000-char truncation with Python comment in prompt | `publication.py:86` | Comment `# Limit content...` sent to LLM; magic number not configurable |
| CQ-11 | No rate limiting or retry for OpenAI API calls | `publication.py:92-107` | Rate-limit errors (429) silently skip publications |
| CQ-12 | `RETURNING` clause not supported by MySQL | `researcher.py:48,64` | PostgreSQL syntax will throw runtime syntax errors |
| CQ-13 | `add_url_to_researcher()` missing `page_type` param | `researcher.py:64` | NOT NULL column omitted from INSERT |
| CQ-14 | Duplicate `python-dotenv` in requirements.txt | `requirements.txt:2,6` | Contradictory version pins; most deps unpinned |
| CQ-15 | No OpenAI API key validation | `publication.py:90` | `None` key causes opaque errors at call time |
| CQ-16 | `db_config` values may all be `None` | `db_config.py:7-12` | No startup validation of required env vars |

### Medium (14)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| CQ-17 | God class: `Database` does everything | `database.py` | Mixes connection mgmt, schema mgmt, query execution, and domain logic |
| CQ-18 | All static methods prevent dependency injection | All Python files | No way to substitute test doubles |
| CQ-19 | Shared mutable `requests.Session` at class level | `html_fetcher.py:12-15` | Cookies persist across unrelated requests; not thread-safe |
| CQ-20 | Duplicate HTML-to-text extraction logic | `html_fetcher.py:42`, `publication.py:68` | Two different stripping functions; `extract_relevant_html` likely dead code |
| CQ-21 | Redundant `url` column in `html_content` table | `database.py:129` | Denormalized; derivable via `url_id` FK |
| CQ-22 | No duplicate publication detection | `publication.py:20-63` | Re-running extraction inserts duplicates |
| CQ-23 | Triple `logging.basicConfig()` calls | `main.py:7`, `database.py:9`, `html_fetcher.py:9` | Different formats; only first call takes effect |
| CQ-24 | `extract_publications()` / `extract_relevant_html()` confusion | `publication.py:66-86` | Parallel unused code paths suggest incomplete refactoring |
| CQ-25 | Broad `except Exception` silences programming bugs | `database.py:207`, `publication.py:57,105` | `KeyError`, `TypeError` caught and logged as operational failures |
| CQ-26 | No `robots.txt` respect in scraper | `html_fetcher.py` | Identified in DESIGN.md as a risk but not addressed |
| CQ-27 | Only timeout errors retried in `fetch_html` | `html_fetcher.py:23-34` | Server 5xx errors are not retried |
| CQ-28 | Hardcoded OpenAI model name | `publication.py:94` | Should be configurable via env var |
| CQ-29 | CSV import has no idempotency | `database.py:190-208` | `add_researcher_url()` always inserts without duplicate check |
| CQ-30 | Next.js 14.2.13 outdated; `@ts-morph/common` unnecessary | `app/package.json` | Security patches missed; accidental dependency |

### Low (9)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| CQ-31 | Variable `id` shadows built-in | `main.py:18,25` | Should use `url_id` or `record_id` |
| CQ-32 | `Publication.__init__` never used in main flow | `publication.py:11-17` | Hybrid class -- sometimes data model, sometimes static utility |
| CQ-33 | `Researcher.name` loses first/last name separation | `researcher.py:5-10` | Cannot reconstruct components from concatenated name |
| CQ-34 | Unused `BeautifulSoup` import if dead code removed | `publication.py:3` | Tied to `extract_relevant_html()` |
| CQ-35 | `is_valid_json` logs error for expected cases | `publication.py:141-149` | First call always fails; spurious error logged |
| CQ-36 | No `.env.example` file | Project root | New devs must read source to find required vars |
| CQ-37 | URL columns limited to VARCHAR(255) | `database.py:110,117,129` | Academic URLs can exceed 255 chars |
| CQ-38 | No backoff between retry attempts | `html_fetcher.py:23-34` | Immediate retries may be treated as abusive |
| CQ-39 | Zero test files in repository | Entire project | No unit, integration, or test configuration |

---

## Architecture Findings

### Critical (2)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| AR-1 | SQL injection in database creation | `database.py:24` | F-string interpolation of DB name into DDL |
| AR-2 | Transaction safety gap in publication saving | `publication.py:20-62` | Multi-connection writes cause partial commits and orphaned researcher records |

### High (4)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| AR-3 | God-class `Database` combines 4 distinct responsibilities | `database.py` | Connection mgmt + query execution + schema mgmt + domain data access |
| AR-4 | All modules depend concretely on `Database` | All Python files | Violates Dependency Inversion; blocks async migration for FastAPI |
| AR-5 | `RETURNING` clause will fail on MySQL at runtime | `researcher.py:47,64` | PostgreSQL-specific syntax in MySQL codebase |
| AR-6 | No domain model -- data flows as raw dicts/tuples | `publication.py`, `researcher.py` | No type safety, no business rule enforcement |

### Medium (8)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| AR-7 | Static-method-only classes prevent DI and testing | All Python classes | Cannot substitute test doubles or use FastAPI Depends() |
| AR-8 | `HTMLFetcher` conflates HTTP, transformation, and persistence | `html_fetcher.py` | Three concerns in one class |
| AR-9 | No validation layer for CSV import or LLM output | `database.py:190-208`, `publication.py:40-41` | Missing Pydantic models at trust boundaries |
| AR-10 | Publications store bare URL string instead of FK | `database.py:115-123` | No referential integrity to source scrape |
| AR-11 | No secondary indexes on frequently queried columns | `database.py:91-147` | Change detection and newsfeed queries will degrade |
| AR-12 | No concurrency guard for overlapping scrapes | `DESIGN.md` 4.3/6.2 | Manual + scheduled scrapes could create duplicates |
| AR-13 | `fetch_and_save_if_changed` returns None, not boolean | `html_fetcher.py:93` | Breaks planned scheduler integration per DESIGN.md |
| AR-14 | Missing error response contract in planned API | `DESIGN.md` 4.x | Success schemas defined but no error envelope |

### Low (5)

| # | Finding | Location | Description |
|---|---------|----------|-------------|
| AR-15 | Duplicate python-dotenv and unpinned deps | `requirements.txt` | Non-reproducible builds |
| AR-16 | Unnecessary `@ts-morph/common` in frontend | `app/package.json` | Accidental dependency |
| AR-17 | Redundant `url` column in `html_content` | `database.py:129` | Denormalization risk |
| AR-18 | Inconsistent logging across modules | `main.py`, `database.py`, `html_fetcher.py` | Format depends on import order |
| AR-19 | Hardcoded OpenAI model name | `publication.py:94` | Should be env-configurable |

---

## Critical Issues for Phase 2 Context

The following findings from Phase 1 should directly inform the Security and Performance reviews in Phase 2:

### Security-Relevant
1. **SQL injection in `create_database()`** (CQ-1/AR-1) — f-string interpolation of config value into DDL
2. **No input validation on LLM output** (CQ-5) — untrusted data inserted directly into DB
3. **No OpenAI API key validation** (CQ-15) — `None` key silently passed
4. **No `db_config` validation** (CQ-16) — all config values could be `None`
5. **No `robots.txt` respect** (CQ-26) — legal/ethical scraping risk
6. **Broad exception catching** (CQ-25) — may mask security-relevant errors

### Performance-Relevant
1. **New DB connection per query** (CQ-6) — no connection pooling; TCP overhead on every operation
2. **OpenAI client recreated per call** (CQ-9) — discards HTTP connection pool
3. **No secondary database indexes** (AR-11) — queries will degrade with data volume
4. **No duplicate publication detection** (CQ-22) — unbounded table growth
5. **No retry/backoff for OpenAI** (CQ-11) — transient failures cause data loss
6. **Only timeout errors retried in HTTP fetcher** (CQ-27) — 5xx errors not retried
7. **Mixed transaction management** (CQ-7/AR-2) — excessive connection churn during publication saves
