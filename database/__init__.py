"""Database package — direct re-exports from submodules.

All functions are importable directly: ``from database import get_connection``.
The ``Database`` facade class is retained for backwards compatibility so that
existing ``from database import Database; Database.foo()`` calls continue to
work without modification.

Submodules:
  connection.py  — pool management and query execution
  schema.py      — DDL, migrations, seeding
  researchers.py — researcher find/create, URL management, CSV import
  papers.py      — title normalization, dedup hashing, draft URLs
  snapshots.py   — append-only versioning for researchers and papers
  llm.py         — LLM usage logging and cost estimation
  jel.py         — JEL code classification and paper topics
"""

# ---------------------------------------------------------------------------
# Direct re-exports — prefer these for new code
# ---------------------------------------------------------------------------

from database.connection import (
    get_connection,
    execute_query,
    fetch_all,
    fetch_one,
)
from database.schema import (
    create_database,
    create_tables,
    seed_research_fields,
    seed_jel_codes,
    backfill_seed_publications,
)
from database.researchers import (
    get_researcher_id,
    update_researcher_bio,
    add_researcher_url,
    import_data_from_file,
    merge_researchers,
)
from database.papers import (
    normalize_title,
    compute_title_hash,
    update_draft_url_status,
    get_unchecked_draft_urls,
    update_openalex_data,
    get_unenriched_papers,
)
from database.snapshots import (
    _compute_researcher_content_hash,
    append_researcher_snapshot,
    get_researcher_snapshots,
    _compute_paper_content_hash,
    append_paper_snapshot,
    get_paper_snapshots,
)
from database.llm import log_llm_usage
from database.jel import (
    get_all_jel_codes,
    get_jel_codes_for_researcher,
    get_jel_codes_for_researchers,
    save_researcher_jel_codes,
    get_researchers_needing_classification,
    save_paper_topics,
    get_paper_topics_for_researcher,
    get_papers_needing_topics,
    get_all_researcher_topics,
    add_researcher_jel_codes,
    sync_researcher_fields_from_jel,
)
from database.admin import get_admin_dashboard_stats as _get_admin_dashboard_stats


# ---------------------------------------------------------------------------
# Backwards-compatible facade — existing code using Database.foo() still works
# ---------------------------------------------------------------------------

class Database:
    """Thin facade re-exporting all database operations as static methods.

    Kept for backwards compatibility. New code should import functions directly
    from the ``database`` package instead of going through this class.
    """

    # Connection
    get_connection = staticmethod(get_connection)
    execute_query = staticmethod(execute_query)
    fetch_all = staticmethod(fetch_all)
    fetch_one = staticmethod(fetch_one)

    # Schema
    create_database = staticmethod(create_database)
    create_tables = staticmethod(create_tables)
    seed_research_fields = staticmethod(seed_research_fields)
    seed_jel_codes = staticmethod(seed_jel_codes)
    backfill_seed_publications = staticmethod(backfill_seed_publications)

    # Researchers
    get_researcher_id = staticmethod(get_researcher_id)
    update_researcher_bio = staticmethod(update_researcher_bio)
    add_researcher_url = staticmethod(add_researcher_url)
    import_data_from_file = staticmethod(import_data_from_file)
    merge_researchers = staticmethod(merge_researchers)

    # Papers
    normalize_title = staticmethod(normalize_title)
    compute_title_hash = staticmethod(compute_title_hash)
    update_draft_url_status = staticmethod(update_draft_url_status)
    get_unchecked_draft_urls = staticmethod(get_unchecked_draft_urls)
    update_openalex_data = staticmethod(update_openalex_data)
    get_unenriched_papers = staticmethod(get_unenriched_papers)

    # Snapshots
    _compute_researcher_content_hash = staticmethod(_compute_researcher_content_hash)
    append_researcher_snapshot = staticmethod(append_researcher_snapshot)
    get_researcher_snapshots = staticmethod(get_researcher_snapshots)
    _compute_paper_content_hash = staticmethod(_compute_paper_content_hash)
    append_paper_snapshot = staticmethod(append_paper_snapshot)
    get_paper_snapshots = staticmethod(get_paper_snapshots)

    # LLM
    log_llm_usage = staticmethod(log_llm_usage)

    # JEL codes
    get_all_jel_codes = staticmethod(get_all_jel_codes)
    get_jel_codes_for_researcher = staticmethod(get_jel_codes_for_researcher)
    get_jel_codes_for_researchers = staticmethod(get_jel_codes_for_researchers)
    save_researcher_jel_codes = staticmethod(save_researcher_jel_codes)
    get_researchers_needing_classification = staticmethod(get_researchers_needing_classification)
    save_paper_topics = staticmethod(save_paper_topics)
    get_paper_topics_for_researcher = staticmethod(get_paper_topics_for_researcher)
    get_papers_needing_topics = staticmethod(get_papers_needing_topics)
    get_all_researcher_topics = staticmethod(get_all_researcher_topics)
    add_researcher_jel_codes = staticmethod(add_researcher_jel_codes)
    sync_researcher_fields_from_jel = staticmethod(sync_researcher_fields_from_jel)

    # Admin
    get_admin_dashboard_stats = staticmethod(_get_admin_dashboard_stats)
