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
