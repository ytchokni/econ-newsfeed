# Two-layer researcher deduplication: within-paper prevention + cross-paper merge

Duplicate researcher rows (e.g., "Max Friedrich Steinhardt", "Max Steinhardt", "M. F. Steinhardt" as separate rows) split a person's papers across profiles and cause display artifacts like "M. Steinhardt, M. Steinhardt, M. Steinhardt" on a single paper. We fix this with two layers rather than making `get_researcher_id` globally more aggressive, because aggressive global name matching risks merging genuinely different people (e.g., two Webers at different institutions).

**Layer 1 — within-paper authorship dedup (prevention).** When saving a paper's author list, before resolving each author, check if an already-resolved author on this same paper has the same last name and a compatible first name. If so, reuse that researcher ID. Same check before inserting the page owner. A paper never lists the same person twice, so within-paper context makes prefix/initial matching safe without the risk of a global match.

**Layer 2 — per-paper researcher merge (consolidation).** After saving each paper, check whether any of its authors share the same last name and co-occur on enough papers. Two tiers: (a) compatible first names (initials/prefixes via `is_compatible_name`) require only 2+ shared papers; (b) any same-last-name pair requires 5+ shared papers AND no conflicting personal websites (both have URLs but none overlap). Tier (b) catches nickname variants like Chris/Christopher that escape token-level matching. The URL guard prevents merging genuinely different people who happen to share a common last name. Runs per-paper (not as a batch step) because extractions often fail partway through a run.

**Compatible name matching** is a shared function (`is_compatible_name`) using token-level positional alignment: tokenize both first names, align shorter to longer, accept each pair if equal or one is a single-char initial matching the other's first character. If all tokens in the shorter name match, return True (prefix semantics). This replaces the old `first_name_is_initial_match` which only handled single initials.

## Considered options

- **Global aggressive matching in `get_researcher_id`**: would catch more duplicates at creation time but risks merging different people with similar names across unrelated papers. Rejected because the same-paper and multi-paper signals are what make the matching safe.
- **Batch merge after full extraction run**: simpler but unreliable — if extraction crashes partway through, the merge never runs. Per-paper is more resilient.
- **Source-URL tracking on authorship rows**: would enable full reconciliation of which URL added which author, but requires a schema change. Page-owner scoped reconciliation solves the immediate problem without it.
