# Text Normalization for Change Detection

**Date:** 2026-03-30
**Status:** Approved
**Approach:** Normalize text before hashing (Approach A)

## Problem

The HTML change detection pipeline produces ~60% false positives. SHA-256 hashing of extracted text triggers on cosmetic changes that contain no new publication data, wasting LLM API calls.

Validated false-positive categories from the March 27 scrape (214 of 1,245 URLs flagged as changed):

1. **Whitespace/spacing** — Trailing spaces removed before closing parens (Hye Young You), Google Sites collapsing whitespace between nav elements (Laurence van Lent, Maria Silfa)
2. **Character encoding** — Curly/smart quotes replaced with straight quotes (Lars Svensson)
3. **Boilerplate churn** — Google Sites chrome ("Search this site", "Skip to navigation", cookie consent text) re-rendering with different whitespace

## Design

### New function: `normalize_text(text: str) -> str`

A pure function in `html_fetcher.py` that canonicalizes extracted text before hashing and storage. Applied transformations, in order:

1. **Quote normalization** — Replace `\u201c \u201d` (curly double) with `"`, `\u2018 \u2019` (curly single) with `'`
2. **Whitespace collapsing** — Replace all runs of `\s+` (including `\u00a0` non-breaking space) with a single space
3. **Boilerplate stripping** — Remove known noise substrings (case-insensitive matching, preserves casing of surrounding text):
   - Google Sites: "Search this site", "Embedded Files", "Skip to main content", "Skip to navigation", "Report abuse", "Page details", "Page updated", "This site uses cookies from Google to deliver its services and to analyze traffic", "Learn more Got it"
   - Generic: "Accept all cookies", "Reject all cookies"
4. **Final trim** — Strip leading/trailing whitespace

The function does NOT normalize numbers, author names, paper titles, status keywords, or URLs — these must remain change-sensitive.

### Pipeline integration

```
Current:  extract_text_content(html) → hash_text_content(text) → compare
New:      extract_text_content(html) → normalize_text(text) → hash_text_content(normalized) → compare
```

#### Affected code

1. **`fetch_and_save_if_changed()`** (`html_fetcher.py:364-409`) — Insert `normalize_text()` call after `extract_text_content()`, before hashing. The normalized text is stored in `html_content.content`.
2. **`compute_diff()`** (`html_fetcher.py:561-570`) — No change needed. Operates on stored text, which will now be normalized.
3. **`extract_relevant_html()`** (`publication.py:342-358`) — No change. Operates on `raw_html` for LLM input, unaffected by normalization.

### Migration

Existing `content_hash` values were computed on un-normalized text. After deployment, the first scrape cycle will see every page as "changed" because new normalized hashes won't match old hashes.

**Mitigation:** A one-time backfill script that:
1. Reads each `html_content.content` row
2. Applies `normalize_text()`
3. Recomputes and updates both `content_hash` and `content` (normalized text)
4. Sets `extracted_hash = content_hash` to prevent re-triggering extraction on already-extracted pages

This avoids a one-time burst of false-positive LLM extractions.

### Testing

Unit tests for `normalize_text()` using real false-positive fixtures:

- **Hye Young You:** `"Pamela Ban and Ju Yeon Park )"` → `"Pamela Ban and Ju Yeon Park)"` (whitespace before paren)
- **Laurence van Lent:** `"202 6"` → `"2026"`, `"condi tionally"` → `"conditionally"` (Google Sites word splitting)
- **Lars Svensson:** Curly `\u201c` quotes → straight `"` quotes
- **Maria Silfa (true positive):** R&R status update still produces a non-empty diff after normalization

Integration test: verify `fetch_and_save_if_changed()` returns `False` when the only difference between old and new content is whitespace/quotes/boilerplate.

### Limitations

- **Google Sites word splitting** (`"202 6"` → `"2026"`, `"condi tionally"` → `"conditionally"`): Whitespace collapsing handles these only when the split produces `"202 6"` (two tokens separated by space). If Google Sites inserts zero-width characters or other non-whitespace separators, those would not be caught. This is acceptable — these are rare edge cases.
- **New boilerplate patterns:** The boilerplate list is static. New noise patterns (e.g., a site adding a new cookie consent framework) require adding entries manually. The list should be kept short and conservative.
