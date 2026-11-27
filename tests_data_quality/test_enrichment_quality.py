"""Enrichment-layer invariants: DOIs, OpenAlex IDs, paper_links, coauthors.

The enrichment pipeline (doi_resolver, openalex, link_extractor) writes
identifiers that drive dedup and the UI's DOI/link buttons — bad values
here mean broken links on the page and merge passes that can't converge.
"""
from conftest import fmt_violations, mojibake_condition


class TestDoiFormat:
    def test_papers_doi_format(self, db):
        """All DOIs start '10.' — anything else is a resolver bug, and the
        frontend builds https://doi.org/<doi> links straight from this."""
        rows = db.fetch_all(
            """SELECT id, doi FROM papers
               WHERE doi IS NOT NULL AND doi NOT REGEXP '^10\\.[0-9]{4,9}/'
               LIMIT 50"""
        )
        assert not rows, "papers with malformed DOI:\n" + fmt_violations(rows)

    def test_paper_links_doi_format(self, db):
        rows = db.fetch_all(
            """SELECT id, paper_id, doi FROM paper_links
               WHERE doi IS NOT NULL AND doi NOT REGEXP '^10\\.[0-9]{4,9}/'
               LIMIT 50"""
        )
        assert not rows, "paper_links with malformed DOI:\n" + fmt_violations(rows)

    def test_no_doi_url_prefix_stored(self, db):
        """DOIs must be bare ('10.x/y'), never full URLs — double-prefixing
        produces https://doi.org/https://doi.org/... dead links."""
        rows = db.fetch_all(
            """SELECT id, doi FROM papers
               WHERE doi LIKE 'http%' OR doi LIKE 'doi.org%' OR doi LIKE 'doi:%'
               LIMIT 50"""
        )
        assert not rows, "papers with URL-prefixed DOI:\n" + fmt_violations(rows)


class TestIdentifierMergeDebt:
    """merge_duplicate_papers collapses papers sharing a DOI/OpenAlex ID —
    leftovers mean the merge job isn't running or keeps failing."""

    def test_no_duplicate_papers_by_doi(self, db):
        rows = db.fetch_all(
            """SELECT doi, COUNT(*) AS n, GROUP_CONCAT(id ORDER BY id) AS paper_ids
               FROM papers WHERE doi IS NOT NULL
               GROUP BY doi HAVING COUNT(*) > 1
               LIMIT 50"""
        )
        assert not rows, "papers sharing a DOI (unmerged duplicates):\n" + fmt_violations(rows)

    def test_no_duplicate_papers_by_openalex_id(self, db):
        rows = db.fetch_all(
            """SELECT openalex_id, COUNT(*) AS n, GROUP_CONCAT(id ORDER BY id) AS paper_ids
               FROM papers WHERE openalex_id IS NOT NULL
               GROUP BY openalex_id HAVING COUNT(*) > 1
               LIMIT 50"""
        )
        assert not rows, "papers sharing an OpenAlex ID (unmerged duplicates):\n" + fmt_violations(rows)

    def test_no_unmerged_title_variants(self, db):
        """Same author set + same_paper_title (hyphen/accent/spacing variants or a
        revised sample period, e.g. Rieger & Bartels' "Spatial Income Inequality
        in Germany, 1957-2020" vs "..., 1957-2021") must be merged."""
        from backend.enrichment.paper_merge import find_fuzzy_duplicate_groups

        groups = find_fuzzy_duplicate_groups()
        rows = [{"paper_ids": ",".join(map(str, g))} for g in groups[:50]]
        assert not groups, (
            f"unmerged title-variant duplicates ({len(groups)} groups):\n" + fmt_violations(rows)
        )

    def test_no_path_suffix_junk_in_link_dois(self, db):
        """DOIs extracted from publisher URLs sometimes keep trailing path
        segments ('/html', '/abstract', article ids) — those are not DOIs.
        (A link DOI legitimately differing from paper.doi — preprint vs
        published version — is fine and NOT flagged.)"""
        rows = db.fetch_all(
            """SELECT pl.paper_id, pl.doi AS link_doi, p.doi AS paper_doi
               FROM paper_links pl JOIN papers p ON p.id = pl.paper_id
               WHERE pl.doi IS NOT NULL AND p.doi IS NOT NULL
                 AND LOWER(pl.doi) LIKE CONCAT(LOWER(p.doi), '/%')
               LIMIT 50"""
        )
        rows += db.fetch_all(
            """SELECT paper_id, doi AS link_doi, NULL AS paper_doi FROM paper_links
               WHERE doi REGEXP '/(html|abstract|pdf|epdf|fulltext)$'
               LIMIT 50"""
        )
        assert not rows, "link DOIs carrying URL path junk:\n" + fmt_violations(rows)


class TestPaperLinkHygiene:
    def test_links_use_http_scheme(self, db):
        rows = db.fetch_all(
            """SELECT id, paper_id, url FROM paper_links
               WHERE url NOT LIKE 'http://%' AND url NOT LIKE 'https://%'
               LIMIT 50"""
        )
        assert not rows, "paper_links with bad scheme:\n" + fmt_violations(rows)

    def test_no_duplicate_links_per_paper(self, db):
        rows = db.fetch_all(
            """SELECT paper_id, url, COUNT(*) AS n
               FROM paper_links
               GROUP BY paper_id, url HAVING COUNT(*) > 1
               LIMIT 50"""
        )
        assert not rows, "duplicate (paper, url) links:\n" + fmt_violations(rows)


class TestCoauthorQuality:
    def test_no_blank_coauthor_names(self, db):
        rows = db.fetch_all(
            """SELECT id, paper_id FROM openalex_coauthors
               WHERE TRIM(COALESCE(display_name, '')) = ''
               LIMIT 50"""
        )
        assert not rows, "blank coauthor names:\n" + fmt_violations(rows)

    def test_no_mojibake_coauthor_names(self, db):
        rows = db.fetch_all(
            f"""SELECT id, paper_id, display_name FROM openalex_coauthors
                WHERE {mojibake_condition('display_name')}
                LIMIT 50"""
        )
        assert not rows, "mojibake coauthor names:\n" + fmt_violations(rows)


class TestIdentifierReverification:
    """#266: enrichment before #244 (15 Oct 2026) accepted a page's DOI without
    a title check, so some papers carry a neighbouring entry's DOI/OpenAlex ID.
    scripts/reverify_openalex_ids.py re-checks them and records each decision in
    openalex_identifier_checks."""

    def test_pre_title_check_identifiers_reverified(self, db):
        from backend.enrichment.identifier_verification import count_papers_to_verify

        remaining = count_papers_to_verify()
        assert remaining == 0, (
            f"{remaining} OpenAlex identifiers set before the #244 title check are not re-verified; "
            "run scripts/reverify_openalex_ids.py --apply"
        )

    def test_cleared_identifier_not_reattached(self, db):
        """A cleared work must not come back on the same paper (enrichment now title-checks)."""
        rows = db.fetch_all(
            """SELECT c.paper_id, c.openalex_id, c.reason FROM openalex_identifier_checks c
               JOIN papers p ON p.id = c.paper_id AND p.openalex_id = c.openalex_id
               WHERE c.verdict = 'cleared' LIMIT 50"""
        )
        assert not rows, "papers carrying an identifier re-verification cleared:\n" + fmt_violations(rows)

    def test_cleared_doi_not_left_on_links(self, db):
        rows = db.fetch_all(
            """SELECT c.paper_id, c.doi, pl.id AS link_id FROM openalex_identifier_checks c
               JOIN paper_links pl ON pl.paper_id = c.paper_id AND LOWER(pl.doi) = LOWER(c.doi)
               WHERE c.verdict = 'cleared' LIMIT 50"""
        )
        assert not rows, "paper_links still carrying a cleared DOI:\n" + fmt_violations(rows)

    def test_shared_identifier_with_different_titles_is_reviewed(self, db):
        """No DOI/OpenAlex ID is shared by papers with different titles unless the
        pair is a known rename (sharing a link: merge_renamed_papers.py) or is on
        the re-verification review list."""
        from backend.enrichment.paper_merge import find_duplicate_groups, same_paper_title

        groups = find_duplicate_groups()
        ids = sorted({pid for g in groups for pid in g})
        if not ids:
            return
        placeholders = ",".join(["%s"] * len(ids))
        titles = {r["id"]: r["title"] for r in db.fetch_all(
            f"SELECT id, title FROM papers WHERE id IN ({placeholders})", tuple(ids))}
        reviewed = {r["paper_id"] for r in db.fetch_all(
            f"""SELECT paper_id FROM openalex_identifier_checks
                WHERE verdict = 'review' AND paper_id IN ({placeholders})""", tuple(ids))}
        from backend.enrichment.rename_merge import load_rename_candidates

        renames = {frozenset((a, b)) for a, b, _ in load_rename_candidates()[0]}
        rows = []
        for group in groups:
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if same_paper_title(titles.get(a), titles.get(b)):
                        continue
                    if a in reviewed or b in reviewed or frozenset((a, b)) in renames:
                        continue
                    rows.append({"paper_ids": f"{a},{b}", "a": titles.get(a), "b": titles.get(b)})
        assert not rows, (
            "identifier shared by differently titled papers (neither reviewed nor a link-sharing rename):\n"
            + fmt_violations(rows[:50], total=len(rows))
        )


class TestRenameMergeDebt:
    def test_no_unmerged_renames(self, db):
        """#265: renamed papers sharing an SSRN/NBER/arXiv/DOI link must be merged
        (scripts/merge_renamed_papers.py --apply)."""
        from backend.enrichment.rename_merge import find_rename_groups

        groups, _ = find_rename_groups()
        rows = [{"paper_ids": ",".join(map(str, g))} for g in groups[:50]]
        assert not groups, f"unmerged renames ({len(groups)} groups):\n" + fmt_violations(rows, total=len(groups))
