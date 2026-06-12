"""Researcher data invariants + live checks of what the directory page serves.

Guards: issue #147/#152 (zero-publication coauthors in the directory),
issue #150 (NULL affiliations), encoding incidents in names.

The directory tests call the REAL search_researchers() against the real
database — the same code path the /researchers page renders — so they catch
both SQL-guard regressions and bad rows that slip through.
"""
from conftest import fmt_violations, mojibake_condition


class TestDirectoryServesValidResearchers:
    """What /researchers actually returns must satisfy the PR #152 guards."""

    PAGE_SIZE = 500

    def _directory_rows(self):
        from database.researchers import search_researchers

        rows, total = search_researchers(offset=0, limit=self.PAGE_SIZE)
        return rows, total

    def test_every_directory_researcher_has_a_publication(self, db):
        rows, _ = self._directory_rows()
        if not rows:
            return
        ids = [r["id"] for r in rows]
        placeholders = ",".join(["%s"] * len(ids))
        counts = db.fetch_all(
            f"SELECT researcher_id, COUNT(*) AS cnt FROM authorship "
            f"WHERE researcher_id IN ({placeholders}) GROUP BY researcher_id",
            tuple(ids),
        )
        have_pubs = {c["researcher_id"] for c in counts}
        violators = [
            {"id": r["id"], "name": f"{r['first_name']} {r['last_name']}"}
            for r in rows if r["id"] not in have_pubs
        ]
        assert not violators, (
            "directory returns researchers with zero publications (issue #147):\n"
            + fmt_violations(violators)
        )

    def test_no_initial_only_names_in_directory(self):
        rows, _ = self._directory_rows()
        violators = [
            {"id": r["id"], "first_name": r["first_name"], "last_name": r["last_name"]}
            for r in rows
            if len((r["first_name"] or "").strip()) <= 2 or len((r["last_name"] or "").strip()) <= 2
        ]
        assert not violators, (
            "directory returns initial-only/abbreviated names:\n" + fmt_violations(violators)
        )


class TestResearcherFieldQuality:
    """Field-level sanity on the researchers table itself."""

    # Issue #150 measured ~18% NULL; the placeholder UI tolerates NULLs but a
    # spike means extraction broke.
    MAX_NULL_AFFILIATION_RATE = 0.35

    def test_null_affiliation_rate_below_threshold(self, db):
        row = db.fetch_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(affiliation IS NULL AND position IS NULL) AS no_affil
            FROM researchers r
            WHERE EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r.id)
              AND EXISTS (SELECT 1 FROM researcher_urls ru WHERE ru.researcher_id = r.id)
            """
        )
        total, no_affil = row["total"], int(row["no_affil"] or 0)
        if total == 0:
            return
        rate = no_affil / total
        assert rate <= self.MAX_NULL_AFFILIATION_RATE, (
            f"{no_affil}/{total} tracked researchers ({rate:.0%}) have neither position "
            f"nor affiliation — threshold {self.MAX_NULL_AFFILIATION_RATE:.0%} (issue #150)"
        )

    def test_no_mojibake_in_researcher_names(self, db):
        """Bare 'Ã' is legitimate (SÃO); only 'Ã'+symbol bigrams are mojibake."""
        rows = db.fetch_all(
            f"""
            SELECT id, first_name, last_name FROM researchers
            WHERE {mojibake_condition('first_name')} OR {mojibake_condition('last_name')}
            LIMIT 50
            """
        )
        assert not rows, "researchers with mojibake names:\n" + fmt_violations(rows)

    def test_no_blank_names(self, db):
        rows = db.fetch_all(
            """
            SELECT id, first_name, last_name FROM researchers
            WHERE TRIM(COALESCE(last_name, '')) = ''
            LIMIT 50
            """
        )
        assert not rows, "researchers with blank last names:\n" + fmt_violations(rows)


class TestActiveUrlHealth:
    """URL lifecycle invariants (PR #137 auto-deactivation)."""

    def test_deactivated_urls_have_reason(self, db):
        rows = db.fetch_all(
            """
            SELECT id, url, deactivated_at FROM researcher_urls
            WHERE is_active = FALSE AND deactivated_at IS NOT NULL
              AND deactivation_reason IS NULL
            LIMIT 50
            """
        )
        assert not rows, "deactivated URLs missing a reason:\n" + fmt_violations(rows)

    def test_active_urls_have_no_deactivation_timestamp(self, db):
        rows = db.fetch_all(
            """
            SELECT id, url, deactivated_at, deactivation_reason FROM researcher_urls
            WHERE is_active = TRUE AND deactivated_at IS NOT NULL
            LIMIT 50
            """
        )
        assert not rows, "active URLs carrying deactivation state:\n" + fmt_violations(rows)


class TestResearcherDuplicates:
    """Disambiguation failures create twin researcher rows that split a
    person's papers across two profiles."""

    def test_no_shared_openalex_author_id(self, db):
        rows = db.fetch_all(
            """SELECT openalex_author_id, COUNT(*) AS n,
                      GROUP_CONCAT(id ORDER BY id) AS researcher_ids
               FROM researchers
               WHERE openalex_author_id IS NOT NULL
               GROUP BY openalex_author_id
               HAVING COUNT(*) > 1
               LIMIT 50"""
        )
        assert not rows, "openalex_author_id shared by multiple researchers:\n" + fmt_violations(rows)

    def test_no_exact_name_duplicates_with_publications(self, db):
        """Same (case-insensitive) full name on 2+ researchers who BOTH have
        papers — almost always a split profile, not a namesake."""
        rows = db.fetch_all(
            """SELECT LOWER(first_name) AS fn, LOWER(last_name) AS ln,
                      COUNT(*) AS n, GROUP_CONCAT(r.id ORDER BY r.id) AS ids
               FROM researchers r
               WHERE EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r.id)
               GROUP BY LOWER(first_name), LOWER(last_name)
               HAVING COUNT(*) > 1
               LIMIT 50"""
        )
        assert not rows, "exact-name duplicate researchers with publications:\n" + fmt_violations(rows)


class TestAffiliationFieldQuality:
    def test_no_overlong_affiliations(self, db):
        """Affiliations beyond 250 chars are usually a bio sentence stuffed
        into the field by the LLM."""
        rows = db.fetch_all(
            """SELECT id, CHAR_LENGTH(affiliation) AS len, LEFT(affiliation, 70) AS head
               FROM researchers
               WHERE CHAR_LENGTH(affiliation) > 250
               LIMIT 50"""
        )
        assert not rows, "overlong affiliations (bio leakage):\n" + fmt_violations(rows)

    def test_affiliation_not_equal_position(self, db):
        rows = db.fetch_all(
            """SELECT id, position FROM researchers
               WHERE affiliation IS NOT NULL AND position IS NOT NULL
                 AND LOWER(TRIM(affiliation)) = LOWER(TRIM(position))
               LIMIT 50"""
        )
        assert not rows, "affiliation duplicated into position:\n" + fmt_violations(rows)


class TestUrlIntegrity:
    """researcher_urls hygiene beyond the deactivation-state checks."""

    def test_active_urls_below_failure_threshold(self, db):
        """_URL_DEACTIVATION_THRESHOLD=3 — an active URL at/over it means the
        auto-deactivation path was bypassed."""
        rows = db.fetch_all(
            """SELECT id, url, consecutive_failures FROM researcher_urls
               WHERE is_active = TRUE AND consecutive_failures >= 3
               LIMIT 50"""
        )
        assert not rows, "active URLs at/over the deactivation threshold:\n" + fmt_violations(rows)

    def test_urls_use_http_scheme(self, db):
        rows = db.fetch_all(
            """SELECT id, url FROM researcher_urls
               WHERE url NOT LIKE 'http://%' AND url NOT LIKE 'https://%'
               LIMIT 50"""
        )
        assert not rows, "researcher URLs with bad scheme:\n" + fmt_violations(rows)

    def test_no_url_shared_across_researchers(self, db):
        """The same page tracked under two researchers double-extracts every
        paper on it and mis-attributes page ownership."""
        rows = db.fetch_all(
            """SELECT url, COUNT(DISTINCT researcher_id) AS n,
                      GROUP_CONCAT(DISTINCT researcher_id) AS researcher_ids
               FROM researcher_urls
               GROUP BY url
               HAVING COUNT(DISTINCT researcher_id) > 1
               LIMIT 50"""
        )
        assert not rows, "URLs tracked under multiple researchers:\n" + fmt_violations(rows)


class TestNameHygiene:
    def test_no_digits_or_urls_in_names(self, db):
        rows = db.fetch_all(
            """SELECT id, first_name, last_name FROM researchers
               WHERE first_name REGEXP '[0-9@/]' OR last_name REGEXP '[0-9@/]'
               LIMIT 50"""
        )
        assert not rows, "researcher names containing digits/URL chars:\n" + fmt_violations(rows)

    def test_no_untrimmed_names(self, db):
        rows = db.fetch_all(
            """SELECT id, CONCAT('[', first_name, '][', last_name, ']') AS bracketed
               FROM researchers
               WHERE first_name != TRIM(first_name) OR last_name != TRIM(last_name)
               LIMIT 50"""
        )
        assert not rows, "researcher names with stray whitespace:\n" + fmt_violations(rows)


class TestDescriptionQuality:
    def test_no_literal_null_descriptions(self, db):
        """LLMs sometimes return the strings 'null'/'None' — storing them
        verbatim puts the word 'null' on researcher cards."""
        rows = db.fetch_all(
            """SELECT id, description FROM researchers
               WHERE LOWER(TRIM(COALESCE(description, 'x'))) IN ('null', 'none', 'n/a', 'unknown')
               LIMIT 50"""
        )
        assert not rows, "literal null-ish descriptions:\n" + fmt_violations(rows)

    def test_no_mojibake_descriptions(self, db):
        rows = db.fetch_all(
            f"""SELECT id, LEFT(description, 60) AS head FROM researchers
                WHERE {mojibake_condition('description')}
                LIMIT 50"""
        )
        assert not rows, "mojibake researcher descriptions:\n" + fmt_violations(rows)
