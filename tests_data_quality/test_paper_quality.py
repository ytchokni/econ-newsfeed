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
