# Discover URLs — Design Spec

## Problem

The econ-newsfeed scraping pipeline extracts publications from researcher pages. During extraction, co-author names are parsed and inserted into the `researchers` table via `get_researcher_id()`. These co-authors exist in the database (linked to papers via `authorship`) but have no entries in `researcher_urls` — so their own publications are never scraped.

Currently, adding new researchers requires manually finding their research page URL and adding it to `urls.csv`. This doesn't scale.

## Solution

A Claude Code project-level slash command (`/project:discover-urls`) that automatically discovers, validates, and imports research page URLs for unlinked researchers. It uses Claude Code's built-in tools (Bash for DB queries, WebSearch for finding pages, WebFetch for validation) — no new Python modules or API keys required.

## Workflow

### Step 1: Find unlinked researchers

Query the database for researchers who:
- Have at least one `authorship` record (they're a real co-author, not noise)
- Have zero entries in `researcher_urls`

```sql
SELECT r.id, r.first_name, r.last_name, r.affiliation
FROM researchers r
JOIN authorship a ON a.researcher_id = r.id
LEFT JOIN researcher_urls ru ON ru.researcher_id = r.id
WHERE ru.id IS NULL
GROUP BY r.id
```

Database access: read `.env` for credentials, then use `python -c "from database import Database; ..."` to run queries through the existing connection pooling layer rather than raw `mysql` CLI.

### Step 2: Search for candidate URLs

For each unlinked researcher, use WebSearch to find their research page:
- Query: `"{first_name} {last_name}" {affiliation} economist research publications`
- If affiliation is NULL (the common case — `get_researcher_id()` is called without affiliation for co-authors), supplement the search with a co-authored paper title for disambiguation:
  - Pull one paper title via: `SELECT p.title FROM papers p JOIN authorship a ON a.publication_id = p.id WHERE a.researcher_id = {id} LIMIT 1`
  - Use query: `"{first_name} {last_name}" economist "{paper_title_fragment}"`
- Collect the top 3-5 results as candidates

### Step 3: Sitemap-assisted discovery

For each candidate domain, attempt to fetch `{scheme}://{domain}/sitemap.xml`:
- Parse the sitemap for URLs matching patterns: `/research`, `/publications`, `/working-papers`, `/papers`, `/wp`
- Collect all matching sub-pages that belong to the same researcher — a single researcher can have multiple URLs with different page types (e.g., a HOME page, a PUB page, and a WP page)
- Add sitemap-discovered URLs alongside search result URLs as additional candidates (don't replace — accumulate)
- If sitemap is unavailable (404, timeout), skip this step and proceed with the search result URLs

### Step 4: Automated validation

Fetch each candidate page via WebFetch and apply these checks:

| Check | Pass criteria | Fail → edge case |
|-------|--------------|-------------------|
| **HTTP status** | Returns 200 | Non-200 status |
| **Name presence** | Page text contains the researcher's last name | Name not found on page |
| **Academic content** | Page contains academic signals: "publication", "working paper", "research", "journal", "NBER", "paper", university names, etc. | No academic signals detected |
| **Page type** | Not a directory/list page with many different researchers | Appears to be a department listing, not an individual page |
| **Single candidate** | Exactly one strong candidate URL | Multiple plausible URLs found |
| **Name uniqueness** | Search results clearly point to one person. Use affiliation (if available) as primary disambiguation. If no affiliation and name appears in multiple distinct academic profiles, flag as edge case rather than guessing. | Common name with ambiguous results |

### Step 5: Auto-approve or surface edge cases

**Auto-approve** (insert directly into `researcher_urls`): Candidates that pass ALL validation checks. A single researcher may have multiple approved URLs. Assign `page_type` based on URL path and content:
- `PUB` if URL path or content clearly indicates a publications list
- `WP` if URL path or content indicates working papers
- `RES` if it's a research page
- `HOME` as the default fallback

**Surface to user**: Any candidate that fails one or more checks. Present as a concise table:

```
Edge cases requiring review:
| # | Researcher        | Candidate URL              | Issue                        |
|---|-------------------|----------------------------|------------------------------|
| 1 | Jane Smith        | https://example.edu/~smith | Multiple candidates found    |
| 2 | John Doe          | (none found)               | No results for common name   |
| 3 | Maria Garcia      | https://uni.edu/garcia     | Name not found on page       |
```

For each edge case, include the alternative URLs or the reason for ambiguity. The user responds with which to approve/reject (e.g., "approve 1 with the first URL, skip 2, approve 3").

### Step 6: Output to CSV

Write all results to `discovered_urls.csv` in the project root, using the same format as `urls.csv`:

```
first_name,last_name,position,affiliation,page_type,url
```

Include both auto-approved and user-approved URLs. The user can then review the file and import it when ready via the existing `python main.py import discovered_urls.csv` command.

Print a summary: X auto-approved, Y user-approved, Z skipped, output written to `discovered_urls.csv`.

## Implementation

This is a single file: `.claude/commands/discover-urls.md` — a Claude Code project command. No Python code changes needed. The skill instructs Claude to:

1. Run queries via inline Python using the existing `database.py` module (handles connection pooling, `.env` loading)
2. Use WebSearch for each researcher
3. Use WebFetch to grab sitemaps and candidate pages
4. Reason about validation checks inline
5. Auto-approve clear matches, surface ambiguous ones
6. Write all approved URLs to `discovered_urls.csv` (same format as `urls.csv`) for user review and manual import

## Constraints

- **Rate limiting**: Process researchers sequentially, not in parallel, to avoid overwhelming search APIs
- **Batch size**: If more than 20 unlinked researchers, process in batches of 20 and ask if user wants to continue
- **No new dependencies**: Uses only Claude Code's built-in tools
- **Security**: The skill only inserts URL strings into `researcher_urls`. Actual fetching happens later via the scraping pipeline, which performs full SSRF validation via `HTMLFetcher.validate_url()` (private IP blocking, metadata endpoint rejection). The skill only needs to ensure URLs are HTTPS and plausibly academic.
- **Idempotent**: `INSERT IGNORE` / `add_researcher_url()` prevents duplicate URL entries
