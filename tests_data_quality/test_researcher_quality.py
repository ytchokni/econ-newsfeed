"""Researcher data invariants + live checks of what the directory page serves.

Guards: issue #147/#152 (zero-publication coauthors in the directory),
issue #150 (NULL affiliations), encoding incidents in names.

The directory tests call the REAL search_researchers() against the real
database — the same code path the /researchers page renders — so they catch
both SQL-guard regressions and bad rows that slip through.
"""
from conftest import fmt_violations, mojibake_condition
from backend.database.researchers import is_abbreviation_of, is_compatible_name


class TestDirectoryServesValidResearchers:
    """What /researchers actually returns must satisfy the PR #152 guards."""

    PAGE_SIZE = 500

    def _directory_rows(self):
        from backend.database.researchers import search_researchers

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

    def test_no_surname_repeated_in_first_name(self, db):
        """PR #249: LLM author splits like ["Peter Hull", "Hull"] render as "Peter Hull Hull"."""
        from backend.database.identity import strip_repeated_surname

        rows = db.fetch_all(
            "SELECT id, first_name, last_name FROM researchers "
            "WHERE first_name LIKE CONCAT('% ', last_name)"
        )
        rows = [r for r in rows if strip_repeated_surname(r["first_name"], r["last_name"]) != r["first_name"]]
        assert not rows, (
            "researchers whose first name repeats the surname "
            "(fix: scripts/repair_researcher_identity.py --steps strip_repeated_surnames):\n"
            + fmt_violations(rows[:50])
        )

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

    def test_no_compatible_name_researchers_sharing_papers(self, db):
        """Researchers with the same last name, compatible first names (initials,
        prefixes, multi-initials), and 2+ shared papers are almost certainly the
        same person. Uses the same is_compatible_name function as the pipeline."""
        from backend.database.researchers import is_compatible_name

        rows = db.fetch_all(
            """SELECT r1.id AS id1, r1.first_name AS fn1, r1.last_name AS ln1,
                      r2.id AS id2, r2.first_name AS fn2, r2.last_name AS ln2,
                      COUNT(DISTINCT a1.publication_id) AS shared_papers
               FROM researchers r1
               JOIN researchers r2 ON r2.id > r1.id
                 AND LOWER(r2.last_name) = LOWER(r1.last_name)
               JOIN authorship a1 ON a1.researcher_id = r1.id
               JOIN authorship a2 ON a2.researcher_id = r2.id
                 AND a2.publication_id = a1.publication_id
               WHERE EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r1.id)
                 AND EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r2.id)
               GROUP BY r1.id, r1.first_name, r1.last_name,
                        r2.id, r2.first_name, r2.last_name
               HAVING COUNT(DISTINCT a1.publication_id) >= 2
               LIMIT 100"""
        )
        dupes = [
            {"ids": f"{r['id1']},{r['id2']}", "names": f"{r['fn1']} {r['ln1']} / {r['fn2']} {r['ln2']}",
             "shared": r['shared_papers']}
            for r in rows
            if is_compatible_name(r['fn1'], r['fn2'])
        ]
        assert not dupes, (
            "compatible-name researchers sharing 2+ papers (ADR-0002):\n"
            + fmt_violations(dupes)
        )

    def test_no_same_last_name_researchers_sharing_5_plus_papers(self, db):
        """Same last name + 5+ shared papers + no conflicting personal websites
        almost certainly means the same person, even with nickname differences
        like Chris/Christopher. Skips pairs where both have different URLs."""
        rows = db.fetch_all(
            """SELECT r1.id AS id1, r1.first_name AS fn1, r1.last_name AS ln1,
                      r2.id AS id2, r2.first_name AS fn2, r2.last_name AS ln2,
                      COUNT(DISTINCT a1.publication_id) AS shared_papers
               FROM researchers r1
               JOIN researchers r2 ON r2.id > r1.id
                 AND LOWER(r2.last_name) = LOWER(r1.last_name)
               JOIN authorship a1 ON a1.researcher_id = r1.id
               JOIN authorship a2 ON a2.researcher_id = r2.id
                 AND a2.publication_id = a1.publication_id
               WHERE EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r1.id)
                 AND EXISTS (SELECT 1 FROM authorship a WHERE a.researcher_id = r2.id)
               GROUP BY r1.id, r1.first_name, r1.last_name,
                        r2.id, r2.first_name, r2.last_name
               HAVING COUNT(DISTINCT a1.publication_id) >= 5
               LIMIT 200"""
        )
        dupes = []
        for r in rows:
            if is_compatible_name(r['fn1'], r['fn2']):
                continue
            if not is_abbreviation_of(r['fn1'], r['fn2']):
                continue
            urls1 = {u['url'] for u in db.fetch_all(
                "SELECT url FROM researcher_urls WHERE researcher_id = %s", (r['id1'],))}
            urls2 = {u['url'] for u in db.fetch_all(
                "SELECT url FROM researcher_urls WHERE researcher_id = %s", (r['id2'],))}
            if urls1 and urls2 and not (urls1 & urls2):
                continue
            dupes.append({
                "ids": f"{r['id1']},{r['id2']}",
                "names": f"{r['fn1']} {r['ln1']} / {r['fn2']} {r['ln2']}",
                "shared": r['shared_papers'],
            })
        assert not dupes, (
            "same-last-name researchers sharing 5+ papers without conflicting URLs:\n"
            + fmt_violations(dupes)
        )


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

    def test_no_llm_reasoning_in_descriptions(self, db):
        """Issue #258: the bio call stored the model's '<thought>* Task: …'
        notes on 3,502 profiles. Fix with
        `scripts/cleanup_data_quality.py --steps restore_reasoning_descriptions`."""
        from backend.pipeline.description import looks_like_reasoning, reasoning_sql_condition

        cond, params = reasoning_sql_condition("description")
        for table in ("researchers", "researcher_snapshots"):
            id_col = "researcher_id" if table == "researcher_snapshots" else "id"
            rows = [
                {"researcher_id": r["rid"], "head": r["description"][:80]}
                for r in db.fetch_all(
                    f"SELECT {id_col} AS rid, description FROM {table} "
                    f"WHERE description IS NOT NULL AND {cond}", params)
                if looks_like_reasoning(r["description"])
            ]
            assert not rows, (
                f"LLM reasoning text stored as a bio in {table}:\n" + fmt_violations(rows)
            )

class TestResearcherIdentity:
    """One person, one record; a page's papers belong to its owner; only
    personal pages credit their owner. Fix with
    scripts/repair_researcher_identity.py (dry run first, then --apply)."""

    def test_no_paper_lists_one_openalex_author_twice(self, db):
        """Issue #259: "Andrew Garin, Andy Garin" on paper 74682."""
        rows = db.fetch_all(
            """SELECT a1.publication_id AS paper_id, r1.openalex_author_id,
                      a1.researcher_id AS id1, a2.researcher_id AS id2
               FROM authorship a1
               JOIN researchers r1 ON r1.id = a1.researcher_id
               JOIN authorship a2 ON a2.publication_id = a1.publication_id
                                 AND a2.researcher_id > a1.researcher_id
               JOIN researchers r2 ON r2.id = a2.researcher_id
               WHERE r1.openalex_author_id IS NOT NULL
                 AND r2.openalex_author_id = r1.openalex_author_id
               LIMIT 50"""
        )
        assert not rows, "papers listing one OpenAlex author twice:\n" + fmt_violations(rows)

    def test_no_same_person_records_share_openalex_id(self, db):
        """Issue #259: records with one OpenAlex ID and agreeing names are one person."""
        from backend.database.identity import cluster_same_person

        rows = db.fetch_all(
            """SELECT r.id, r.first_name, r.last_name, r.openalex_author_id
               FROM researchers r
               JOIN (SELECT openalex_author_id FROM researchers WHERE openalex_author_id IS NOT NULL
                     GROUP BY openalex_author_id HAVING COUNT(*) > 1) g
                 ON g.openalex_author_id = r.openalex_author_id"""
        )
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(r["openalex_author_id"], []).append(r)
        violations = [
            {"openalex_author_id": oa, "records": [(r["id"], f"{r['first_name']} {r['last_name']}") for r in c]}
            for oa, recs in groups.items() for c in cluster_same_person(recs, strong=True)
        ]
        assert not violations, "split researcher records:\n" + fmt_violations(violations)

    def test_page_owner_not_split_from_own_papers(self, db):
        """Issue #260: papers on an owner's active page credited to a stub with
        the owner's name ("Dora Costa" 67804 instead of Dora L. Costa 16478)."""
        from backend.database.identity import same_person

        rows = db.fetch_all(
            """SELECT ru.researcher_id AS owner_id, o.first_name AS ofn, o.last_name AS oln,
                      pu.paper_id, a.researcher_id AS stub_id, r.first_name, r.last_name
               FROM paper_urls pu
               JOIN researcher_urls ru ON ru.url = pu.url AND ru.is_active = TRUE
               JOIN researchers o ON o.id = ru.researcher_id
               JOIN authorship a ON a.publication_id = pu.paper_id AND a.author_order > 0
                                AND a.researcher_id != ru.researcher_id
               JOIN researchers r ON r.id = a.researcher_id
                                 AND LOWER(r.last_name) = LOWER(o.last_name)
               LEFT JOIN authorship ao ON ao.publication_id = pu.paper_id
                                      AND ao.researcher_id = ru.researcher_id
               WHERE (ao.id IS NULL OR COALESCE(ao.author_order, 0) = 0)
                 AND NOT EXISTS (SELECT 1 FROM researcher_urls u WHERE u.researcher_id = r.id)
                 AND (r.openalex_author_id IS NULL OR o.openalex_author_id IS NULL
                      OR r.openalex_author_id = o.openalex_author_id)"""
        )
        violations = [r for r in rows if same_person(r["ofn"], r["oln"], r["first_name"], r["last_name"])]
        assert not violations, "owner's papers credited to a name-variant stub:\n" + fmt_violations(
            violations, total=len(violations))

    def test_no_active_page_shared_by_unrelated_researchers(self, db):
        """Issue #262: stanford.edu/research tracked for Abramitzky, Bloom and Bagwell."""
        from backend.database.identity import SHARED_PAGE, page_ownership_problem

        rows = db.fetch_all(
            """SELECT DISTINCT ru.url, r.id, r.first_name, r.last_name
               FROM researcher_urls ru JOIN researchers r ON r.id = ru.researcher_id
               JOIN (SELECT url FROM researcher_urls GROUP BY url
                     HAVING COUNT(DISTINCT researcher_id) > 1 AND SUM(is_active) > 0) s
                 ON s.url = ru.url"""
        )
        by_url: dict[str, list[dict]] = {}
        for r in rows:
            by_url.setdefault(r["url"], []).append(r)
        violations = [{"url": url, "researchers": [r["id"] for r in owners]}
                      for url, owners in by_url.items()
                      if page_ownership_problem(owners, None) == SHARED_PAGE]
        assert not violations, "active pages tracked for unrelated researchers:\n" + fmt_violations(violations)

    def test_no_owner_credit_from_unowned_pages(self, db):
        """Issue #262: owner attachments (author_order 0) whose only source is a
        page deactivated as shared ("Rubin Observatory..." for Abramitzky)."""
        rows = db.fetch_all(
            """SELECT a.researcher_id, a.publication_id
               FROM authorship a
               JOIN paper_urls pu ON pu.paper_id = a.publication_id
               JOIN researcher_urls ru ON ru.url = pu.url AND ru.researcher_id = a.researcher_id
               WHERE COALESCE(a.author_order, 0) = 0
                 AND ru.deactivation_reason = 'shared_page'
                 AND NOT EXISTS (
                     SELECT 1 FROM paper_urls pu2
                     JOIN researcher_urls ru2 ON ru2.url = pu2.url
                     WHERE pu2.paper_id = a.publication_id AND ru2.researcher_id = a.researcher_id
                       AND (ru2.deactivation_reason IS NULL OR ru2.deactivation_reason != 'shared_page'))
               LIMIT 50"""
        )
        assert not rows, "owners credited with items from pages that are not theirs:\n" + fmt_violations(rows)
