# DOI-First Enrichment Pipeline

## Problem

Current enrichment has 3% coverage (105/3,471 papers enriched). OpenAlex title search is fuzzy, produces wrong matches on generic titles, and misses many papers. Meanwhile, researcher pages contain 356 journal/DOI links with extractable identifiers that are currently ignored for enrichment.

Additionally, link-to-paper matching and researcher disambiguation both use LLM calls that could be replaced with deterministic identifier-based matching.

## Design

### Core Principle: Resolve DOIs from URLs before searching by title

Extract identifiers directly from publisher URLs found on researcher pages, resolve them to DOIs, then use DOI for exact OpenAlex lookup. Fall back to title search only for published papers with no links.

### Evidence (tested against live data)

| Strategy | Accuracy | Coverage | API cost |
|----------|----------|----------|----------|
| DOI from URL regex | 100% | 62% of links | 0 calls |
| PII → Crossref → DOI | ~90% | 21% of links | 1 call |
| DOI → OpenAlex lookup | 100% | all DOI-resolved | 1 call |
| Title search (current) | ~50% | all papers | 1 call, wrong matches |

Head-to-head on 10 papers with links: URL DOI was correct 10/10, title search was correct 5/10 (3 wrong DOI, 2 miss).

### Enrichment Rules

- **Paper has link on page:** resolve DOI from URL, enrich via OpenAlex regardless of status
- **Paper has no link, status = published:** fall back to OpenAlex title+author search
- **Paper has no link, status != published:** skip enrichment (no fabricated links)

## Components

### 1. DOI Resolver (`doi_resolver.py`)

New module with two responsibilities:

**URL → identifier extraction** (no API calls):
- DOI regex from URL path/query string — covers AEA, Springer, UChicago, Wiley, T&F, Annual Reviews, etc.
- PII from ScienceDirect `/pii/XXXXX` paths
- Extensible `_EXTRACTORS` list for future patterns

**Identifier → DOI resolution** (API calls only when needed):
- DOI already extracted → return immediately
- PII → Crossref `alternative-id` filter → DOI

```python
def resolve_doi_from_url(url: str) -> str | None:
    """Extract or resolve a DOI from a publisher URL. Returns DOI string or None."""
```

### 2. Enhanced Link-to-Paper Matching

When a link has a resolvable DOI, use the canonical title from Crossref/OpenAlex to match against papers in the DB instead of anchor text heuristics:

1. Resolve DOI from URL
2. Look up canonical title via OpenAlex `doi:` endpoint
3. Match canonical title to papers using `title_hash` or normalized comparison
4. Fall back to anchor text matching for links without resolvable DOIs

This replaces LLM-extracted title dependency for matching.

### 3. Researcher Disambiguation via OpenAlex Author ID

**Schema change:** Add `openalex_author_id VARCHAR(255)` to `researchers` table.

When enriching via DOI → OpenAlex, the response includes `openalex_author_id` for each author. Matching chain:

1. Match by `openalex_author_id` (deterministic, free)
2. Exact name match (current step 1)
3. LLM disambiguation (current step 2, now rarely needed)

Populate `openalex_author_id` on researchers whenever a match is confirmed.

### 4. Updated Pipeline Flow

```
1. Fetch HTML
2. LLM extract publications → save to papers
3. Extract trusted links from HTML
4. For each link:
   a. Try resolve DOI from URL (regex or Crossref)
   b. If DOI found: get canonical title from OpenAlex
   c. Match to paper by canonical title (or fall back to anchor text)
   d. Store in paper_links (with DOI if resolved)
5. For matched papers with DOI: update DOI + OpenAlex metadata directly
6. Enrichment fallback: title-search only published papers with no links
```

### 5. Schema Changes

**`researchers` table:**
- Add `openalex_author_id VARCHAR(255) DEFAULT NULL`

**`paper_links` table:**
- Add `doi VARCHAR(255) DEFAULT NULL` — store resolved DOI alongside URL

### 6. Backfill Script

One-time script to:
1. Re-run link extraction on all 535 stored HTML pages → populate `paper_links`
2. Resolve DOIs from extracted links
3. Enrich papers that get DOIs
4. Populate `openalex_author_id` on researchers from enrichment data

### 7. LLM Cost Reduction

Three mechanisms reduce LLM spend:
- **Link matching:** canonical title from DOI replaces anchor text heuristics (no LLM needed)
- **Researcher disambiguation:** OpenAlex author ID match replaces LLM name matching (3,100 calls saved historically)
- **Enrichment accuracy:** DOI lookup is exact — no wrong matches to clean up

## Out of Scope

- Scraping publisher pages for metadata (we only use APIs)
- Adding new trusted domains
- Changing the LLM extraction prompt
- Real-time Crossref/OpenAlex webhook integration
