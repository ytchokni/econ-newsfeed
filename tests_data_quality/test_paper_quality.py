"""Paper field invariants against the real database.

Guards: issue #148 (lowercase titles), issue #146 (NULL years), PR #153
(status regressions on the denormalized papers table), encoding incidents.
"""
from conftest import STATUS_ORDER, fmt_violations, mojibake_condition


class TestTitleQuality:
    """clean_title() capitalizes on save; the #151 migration backfilled old rows."""

    def test_no_titles_starting_with_lowercase_letter(self, db):
        # ORD() of the first character is collation-independent; 97-122 = a-z.
        rows = db.fetch_all(
            """
            SELECT id, title, source_url
            FROM papers
            WHERE title IS NOT NULL
              AND ORD(SUBSTRING(title, 1, 1)) BETWEEN 97 AND 122
            LIMIT 50
            """
        )
        assert not rows, "papers with lowercase first letter (issue #148):\n" + fmt_violations(rows)

    def test_no_empty_or_whitespace_titles(self, db):
        rows = db.fetch_all(
            "SELECT id, title, source_url FROM papers "
            "WHERE title IS NULL OR TRIM(title) = '' LIMIT 50"
        )
        assert not rows, "papers with empty titles:\n" + fmt_violations(rows)

    def test_no_mojibake_in_titles(self, db):
        """UTF-8 read as latin-1 leaves 'Ã'+symbol or 'â€' bigrams in text."""
        rows = db.fetch_all(
            f"SELECT id, title FROM papers WHERE {mojibake_condition('title')} LIMIT 50"
        )
        assert not rows, "papers with mojibake titles:\n" + fmt_violations(rows)

    def test_no_metadata_suffixes_in_titles(self, db):
        """clean_title() strips ' — Working Paper' / '[JMP]' style suffixes on save."""
        rows = db.fetch_all(
            r"""
            SELECT id, title FROM papers
            WHERE title REGEXP '\\[(JMP|Draft|Working Paper|New!?)\\]$'
               OR title REGEXP '(--|—|–)[[:space:]]*(JMP|Working Paper|Job Market Paper|Draft)$'
            LIMIT 50
            """
        )
        assert not rows, "papers with unstripped metadata suffixes:\n" + fmt_violations(rows)


class TestYearQuality:
    """Issue #146: 65% of new_paper events had NULL year. PR #154 tightened the prompt."""

    # Target after PR #154's prompt fix + re-extraction of the backlog.
    MAX_NULL_YEAR_RATE = 0.50

    def test_year_values_are_four_digit_years(self, db):
        """The Pydantic validator falls back to s[:4], which can store garbage
        like 'fort' (from 'forthcoming') — those rows are data corruption."""
        rows = db.fetch_all(
            """
            SELECT id, year, title FROM papers
            WHERE year IS NOT NULL
              AND year NOT REGEXP '^(19|20)[0-9]{2}$'
            LIMIT 50
            """
        )
        assert not rows, "papers with non-year garbage in year column:\n" + fmt_violations(rows)

    def test_null_year_rate_below_threshold(self, db):
        row = db.fetch_one(
            "SELECT COUNT(*) AS total, SUM(year IS NULL) AS null_years FROM papers"
        )
        total, null_years = row["total"], int(row["null_years"] or 0)
        if total == 0:
            return
        rate = null_years / total
        assert rate <= self.MAX_NULL_YEAR_RATE, (
            f"{null_years}/{total} papers ({rate:.0%}) have NULL year — "
            f"threshold is {self.MAX_NULL_YEAR_RATE:.0%} (issue #146; "
            "re-run extraction backlog or improve the prompt)"
        )

    def test_no_implausible_years(self, db):
        rows = db.fetch_all(
            """
            SELECT id, year, title FROM papers
            WHERE year IS NOT NULL
              AND year REGEXP '^(19|20)[0-9]{2}$'
              AND (CAST(year AS UNSIGNED) < 1900
                   OR CAST(year AS UNSIGNED) > YEAR(NOW()) + 2)
            LIMIT 50
            """
        )
        assert not rows, "papers with implausible years:\n" + fmt_violations(rows)


class TestStatusDenormalizationIntegrity:
    """papers.status must never be outranked by any of its snapshots (PR #153).

    append_paper_snapshot keeps the highest-ranked status on the papers table;
    a paper whose snapshot outranks its current status means a regression was
    applied (pre-#153 damage, or writes bypassing the guard).
    """

    def test_paper_status_not_outranked_by_snapshots(self, db):
        rows = db.fetch_all(
            f"""
            SELECT p.id, p.status AS paper_status,
                   MAX(FIELD(ps.status, {STATUS_ORDER})) AS max_snapshot_rank,
                   FIELD(p.status, {STATUS_ORDER}) AS paper_rank
            FROM papers p
            JOIN paper_snapshots ps ON ps.paper_id = p.id
            WHERE ps.status IS NOT NULL AND p.status IS NOT NULL
            GROUP BY p.id, p.status
            HAVING paper_rank < max_snapshot_rank
            LIMIT 50
            """
        )
        assert not rows, (
            "papers whose status regressed below a snapshot status (PR #153):\n" + fmt_violations(rows)
        )


class TestPaperRelationalIntegrity:
    """Every paper must be reachable: at least one author."""

    def test_no_orphan_papers_without_authorship(self, db):
        rows = db.fetch_all(
            """
            SELECT p.id, p.title, p.source_url
            FROM papers p
            WHERE NOT EXISTS (SELECT 1 FROM authorship a WHERE a.publication_id = p.id)
            LIMIT 50
            """
        )
        assert not rows, "papers with no authors (unreachable from any researcher):\n" + fmt_violations(rows)


class TestTitleHashIntegrity:
    """papers.title_hash must equal compute_title_hash(title) for every row.

    Drift means a writer updated one without the other (the #177 rename bug
    family) — dedup silently breaks for that paper from then on.
    """

    def test_title_hash_matches_title(self, db):
        from database.papers import compute_title_hash

        rows = db.fetch_all("SELECT id, title, title_hash FROM papers WHERE title IS NOT NULL")
        bad = [
            {"id": r["id"], "title": (r["title"] or "")[:60]}
            for r in rows
            if r["title_hash"] != compute_title_hash(r["title"])
        ]
        assert not bad, f"papers with stale title_hash ({len(bad)} total):\n" + fmt_violations(bad)


class TestNearDuplicatePapers:
    """Two papers by the same researcher with near-identical titles are the
    title-variant duplicates of issue #107 / the #177 collision family —
    merge_duplicate_papers only catches DOI/OpenAlex or 2+-shared-author cases.
    """

    SIMILARITY = 0.92

    def test_no_near_duplicate_titles_per_researcher(self, db):
        from difflib import SequenceMatcher
        from database.papers import normalize_title

        rows = db.fetch_all(
            """SELECT a.researcher_id, p.id, p.title
               FROM papers p JOIN authorship a ON a.publication_id = p.id
               WHERE p.title IS NOT NULL"""
        )
        by_researcher: dict[int, list[tuple[int, str, str]]] = {}
        for r in rows:
            norm = normalize_title(r["title"])
            if norm:
                by_researcher.setdefault(r["researcher_id"], []).append((r["id"], r["title"], norm))

        seen_pairs = set()
        dupes = []
        for rid, papers in by_researcher.items():
            if len(papers) < 2:
                continue
            papers.sort(key=lambda x: x[2])
            for (id1, t1, n1), (id2, t2, n2) in zip(papers, papers[1:]):
                pair = tuple(sorted((id1, id2)))
                if id1 == id2 or pair in seen_pairs or n1[:20] != n2[:20]:
                    continue
                if SequenceMatcher(None, n1.split(), n2.split()).ratio() >= self.SIMILARITY:
                    seen_pairs.add(pair)
                    dupes.append({"paper_ids": pair, "t1": t1[:55], "t2": t2[:55]})
        assert not dupes, (
            f"near-duplicate paper titles within a researcher ({len(dupes)} pairs):\n"
            + fmt_violations(dupes)
        )


class TestExtractionArtifacts:
    """LLM/HTML residue that should never survive clean_title/save paths."""

    def test_no_raw_html_entities_in_titles(self, db):
        rows = db.fetch_all(
            """SELECT id, title FROM papers
               WHERE title LIKE '%&amp;%' OR title LIKE '%&#%'
                  OR title LIKE '%&quot;%' OR title LIKE '%&nbsp;%'
               LIMIT 50"""
        )
        assert not rows, "titles with raw HTML entities:\n" + fmt_violations(rows)

    def test_no_html_tags_in_titles(self, db):
        rows = db.fetch_all(
            """SELECT id, title FROM papers
               WHERE title LIKE '%</%' OR title LIKE '%<em>%' OR title LIKE '%<i>%'
                  OR title LIKE '%<span%' OR title LIKE '%<br%'
               LIMIT 50"""
        )
        assert not rows, "titles with HTML tags:\n" + fmt_violations(rows)

    def test_no_truncated_titles(self, db):
        """Trailing ellipsis/comma/dash usually means the LLM hit a token cliff."""
        rows = db.fetch_all(
            r"""SELECT id, title FROM papers
                WHERE title REGEXP '(\\.\\.\\.|…|,|;)$'
                LIMIT 50"""
        )
        assert not rows, "titles ending in truncation markers:\n" + fmt_violations(rows)

    def test_no_status_words_as_venue(self, db):
        """A venue that is ONLY a status phrase belongs in papers.status."""
        rows = db.fetch_all(
            """SELECT id, venue, status FROM papers
               WHERE LOWER(TRIM(venue)) IN
                 ('working paper', 'draft', 'under review', 'submitted',
                  'r&r', 'revise and resubmit', 'reject and resubmit',
                  'work in progress', 'job market paper', 'jmp')
               LIMIT 50"""
        )
        assert not rows, "status phrases stored as venue:\n" + fmt_violations(rows)

    def test_no_mojibake_in_venue_or_abstract(self, db):
        rows = db.fetch_all(
            f"""SELECT id, LEFT(COALESCE(venue,''), 40) AS venue,
                       LEFT(COALESCE(abstract,''), 60) AS abstract_head
                FROM papers
                WHERE {mojibake_condition('venue')} OR {mojibake_condition('abstract')}
                LIMIT 50"""
        )
        assert not rows, "mojibake in venue/abstract:\n" + fmt_violations(rows)

    def test_no_junk_abstracts(self, db):
        """Non-null abstracts under 40 chars are extraction noise, not abstracts."""
        rows = db.fetch_all(
            """SELECT id, abstract FROM papers
               WHERE abstract IS NOT NULL AND TRIM(abstract) != ''
                 AND CHAR_LENGTH(abstract) < 40
               LIMIT 50"""
        )
        assert not rows, "junk abstracts (<40 chars):\n" + fmt_violations(rows)


class TestDraftUrlQuality:
    def test_draft_urls_use_http_scheme(self, db):
        rows = db.fetch_all(
            """SELECT id, draft_url FROM papers
               WHERE draft_url IS NOT NULL
                 AND draft_url NOT LIKE 'http://%' AND draft_url NOT LIKE 'https://%'
               LIMIT 50"""
        )
        assert not rows, "draft_urls with bad scheme:\n" + fmt_violations(rows)

    def test_draft_url_is_not_source_url(self, db):
        """draft_url pointing back at the listing page is an extraction artifact."""
        rows = db.fetch_all(
            """SELECT id, draft_url FROM papers
               WHERE draft_url IS NOT NULL AND draft_url = source_url
               LIMIT 50"""
        )
        assert not rows, "draft_url equals source_url:\n" + fmt_violations(rows)


class TestDiscoveredAtSanity:
    def test_no_future_discovered_at(self, db):
        rows = db.fetch_all(
            "SELECT id, discovered_at FROM papers WHERE discovered_at > NOW() + INTERVAL 1 DAY LIMIT 50"
        )
        assert not rows, "papers discovered in the future:\n" + fmt_violations(rows)

    def test_no_pre_epoch_discovered_at(self, db):
        from conftest import PROJECT_EPOCH
        rows = db.fetch_all(
            f"SELECT id, discovered_at FROM papers WHERE discovered_at < '{PROJECT_EPOCH}' LIMIT 50"
        )
        assert not rows, "papers discovered before the project existed:\n" + fmt_violations(rows)


class TestAuthorshipSanity:
    """Extraction blow-ups create papers with absurd author lists."""

    MAX_PLAUSIBLE_AUTHORS = 100

    def test_no_papers_with_absurd_author_counts(self, db):
        rows = db.fetch_all(
            f"""SELECT p.id, LEFT(p.title, 50) AS title, COUNT(*) AS n_authors
                FROM papers p JOIN authorship a ON a.publication_id = p.id
                GROUP BY p.id, p.title
                HAVING COUNT(*) > {self.MAX_PLAUSIBLE_AUTHORS}
                ORDER BY n_authors DESC
                LIMIT 50"""
        )
        assert not rows, "papers with implausibly many authors:\n" + fmt_violations(rows)
