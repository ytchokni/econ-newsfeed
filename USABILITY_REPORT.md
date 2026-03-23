# Usability Report: Econ Newsfeed

**Reviewer perspective:** Economics researcher (potential end user)
**Date:** 2026-03-20

---

## What Works Well

- **Clean, readable design.** Serif headings + sans-serif body feels academic and professional. Papers are easy to scan.
- **Chronological feed with date grouping.** The "newspaper front page" metaphor works — new items are immediately visible.
- **Status badges** (Working Paper, Published, R&R, Accepted) with intuitive color coding.
- **Researcher detail pages** — working papers separated from publications, expandable abstracts.
- **Author linking.** Clicking coauthor names navigates to their page. Great for discovery.
- **Filters** by status, year, and institution are sensible starting points.
- **Mobile layout** is responsive and usable.

---

## What's Missing

### P0 — Critical (blocks daily use)

#### 1. Search (researchers + papers)
There is no search functionality anywhere. The Researchers page shows thousands of names alphabetically with no way to find someone. I need to type "Acemoglu" and find the person. Similarly, I should be able to search for a paper by title keyword.

#### 2. Links to the actual paper
Paper titles are not clickable. There's no link to the PDF, NBER page, SSRN, journal page, or even the source website. The `source_url` and `draft_url` fields exist in the API but aren't surfaced in the UI. Without being able to actually *read* the paper, the feed is informational but not actionable.

---

### P1 — High Priority (needed for regular use)

#### 3. Field/topic filter on the feed
The feed mixes all fields — trade, macro, political economy, labor, finance, accounting. The Researchers page has a "Field" filter, but the feed does not. A trade economist shouldn't have to scroll through 79 pages to find relevant papers.

#### 5. Event type context ("new paper" vs "status changed")
The feed is event-driven (`event_type` exists in the API), but the UI doesn't explain *why* something appeared. A status change from Working Paper to R&R at a top-5 journal is much more interesting than a new working paper. The feed should distinguish these events visually.

---

### P2 — Medium Priority (would make it indispensable)

#### 6. Email digest / RSS notifications
A feed is only useful if checked. Researchers would want a weekly digest: "12 new papers this week from researchers you follow." Or at minimum, an RSS feed.

#### 7. Follow researchers / personalized feed
Not all 4,000+ researchers are equally relevant. Let users follow specific researchers or fields and see a personalized feed.

#### 8. Researcher deduplication
Observed duplicates:
- "Tasso Adamopoulos" and "Tasso Adamopoulus" (typo)
- "M. Zanardi" appears with researcher IDs 57 and 4103
- "E. Albagli" and "Elías Albagli" are separate entries

This fragmentation means papers are missed if only one entry is followed.

#### 9. Expandable abstracts on feed cards
The main feed doesn't show abstracts — users must click through to a researcher page. An expandable abstract on feed cards (like the researcher detail page already has) would save clicks.

---

### P3 — Lower Priority (polish and refinement)

#### 10. JEL codes / topic tags
Papers in economics are classified by JEL codes (e.g., F10 for trade, E52 for monetary policy). These enable precise filtering and are familiar to every economist.

#### 11. Better pagination
79 pages with no way to jump to a specific page or date. Infinite scroll or a date picker ("show me papers from February 2026") would be more natural.

#### 12. Sorting options
The feed is purely chronological by discovery date. Options to sort by publication year or filter by collaboration patterns would add value.

#### 13. Title casing normalization
On some researcher pages, titles appear in all lowercase (e.g., "the power of the street: evidence from egypt's arab spring"). Titles should be properly capitalized.

---

## Summary

| Priority | Feature | Impact |
|----------|---------|--------|
| **P0** | Search (researchers + papers) | Blocks basic usability |
| **P0** | Links to actual papers (PDF/SSRN/journal) | Feed is not actionable without this |
| **P1** | Field/topic filter on the feed | Essential for field-specific researchers |
| **P1** | Show affiliations on feed cards | Missing context for prioritization |
| **P1** | Event type context in feed | Distinguishes signal from noise |
| **P2** | Email digest / RSS | Drives repeat engagement |
| **P2** | Follow researchers / personalized feed | Reduces noise |
| **P2** | Researcher deduplication | Data quality |
| **P2** | Expandable abstracts on feed | Reduces clicks |
| **P3** | JEL codes | Precise filtering |
| **P3** | Better pagination | Browse by date |
| **P3** | Sorting options | Flexible exploration |
| **P3** | Title casing normalization | Visual polish |

**Bottom line:** The foundation is solid — the scraping pipeline, data model, and visual design are all good. But right now it's a *display* of data, not yet a *tool* a researcher would use daily. Search and paper links would immediately make it useful. Field filtering and notifications would make it indispensable.
