"""Database package — thin facade preserving the Database class API.

All existing code can continue to use ``from database import Database``.
Internally, methods are organized into focused modules:
  connection.py  — pool management and query execution
  schema.py      — DDL, migrations, seeding
  researchers.py — researcher find/create, URL management, CSV import
  papers.py      — title normalization, dedup hashing, draft URLs
  snapshots.py   — append-only versioning for researchers and papers
  llm.py         — LLM usage logging and cost estimation
"""
from database.connection import (
    get_connection as _get_connection,
    execute_query as _execute_query,
    fetch_all as _fetch_all,
    fetch_one as _fetch_one,
)
from database.schema import (
    create_database as _create_database,
    create_tables as _create_tables,
    seed_research_fields as _seed_research_fields,
    seed_jel_codes as _seed_jel_codes,
    backfill_seed_publications as _backfill_seed_publications,
)
from database.researchers import (
    get_researcher_id as _get_researcher_id,
    update_researcher_bio as _update_researcher_bio,
    add_researcher_url as _add_researcher_url,
    import_data_from_file as _import_data_from_file,
    merge_researchers as _merge_researchers,
)
from database.papers import (
    normalize_title as _normalize_title,
    compute_title_hash as _compute_title_hash,
    update_draft_url_status as _update_draft_url_status,
    get_unchecked_draft_urls as _get_unchecked_draft_urls,
    update_openalex_data as _update_openalex_data,
    get_unenriched_papers as _get_unenriched_papers,
)
from database.snapshots import (
    _compute_researcher_content_hash,
    append_researcher_snapshot as _append_researcher_snapshot,
    get_researcher_snapshots as _get_researcher_snapshots,
    _compute_paper_content_hash,
    append_paper_snapshot as _append_paper_snapshot,
    get_paper_snapshots as _get_paper_snapshots,
)
from database.llm import log_llm_usage as _log_llm_usage
from database.jel import (
    get_all_jel_codes as _get_all_jel_codes,
    get_jel_codes_for_researcher as _get_jel_codes_for_researcher,
    get_jel_codes_for_researchers as _get_jel_codes_for_researchers,
    save_researcher_jel_codes as _save_researcher_jel_codes,
    get_researchers_needing_classification as _get_researchers_needing_classification,
    save_paper_topics as _save_paper_topics,
    get_paper_topics_for_researcher as _get_paper_topics_for_researcher,
    get_papers_needing_topics as _get_papers_needing_topics,
    get_all_researcher_topics as _get_all_researcher_topics,
    add_researcher_jel_codes as _add_researcher_jel_codes,
    sync_researcher_fields_from_jel as _sync_researcher_fields_from_jel,
)
from database.admin import get_admin_dashboard_stats as _get_admin_dashboard_stats


class Database:
    """Facade class re-exporting all database operations as static methods."""

    # Connection
    get_connection = staticmethod(_get_connection)
    execute_query = staticmethod(_execute_query)
    fetch_all = staticmethod(_fetch_all)
    fetch_one = staticmethod(_fetch_one)

    # Schema
    create_database = staticmethod(_create_database)
    create_tables = staticmethod(_create_tables)
    seed_research_fields = staticmethod(_seed_research_fields)
    seed_jel_codes = staticmethod(_seed_jel_codes)
    backfill_seed_publications = staticmethod(_backfill_seed_publications)

    # Researchers
    get_researcher_id = staticmethod(_get_researcher_id)
    update_researcher_bio = staticmethod(_update_researcher_bio)
    add_researcher_url = staticmethod(_add_researcher_url)
    import_data_from_file = staticmethod(_import_data_from_file)
    merge_researchers = staticmethod(_merge_researchers)

    # Papers
    normalize_title = staticmethod(_normalize_title)
    compute_title_hash = staticmethod(_compute_title_hash)
    update_draft_url_status = staticmethod(_update_draft_url_status)
    get_unchecked_draft_urls = staticmethod(_get_unchecked_draft_urls)
    update_openalex_data = staticmethod(_update_openalex_data)
    get_unenriched_papers = staticmethod(_get_unenriched_papers)

    # Snapshots
    _compute_researcher_content_hash = staticmethod(_compute_researcher_content_hash)
    append_researcher_snapshot = staticmethod(_append_researcher_snapshot)
    get_researcher_snapshots = staticmethod(_get_researcher_snapshots)
    _compute_paper_content_hash = staticmethod(_compute_paper_content_hash)
    append_paper_snapshot = staticmethod(_append_paper_snapshot)
    get_paper_snapshots = staticmethod(_get_paper_snapshots)

    # LLM
    log_llm_usage = staticmethod(_log_llm_usage)

    # JEL codes
    get_all_jel_codes = staticmethod(_get_all_jel_codes)
    get_jel_codes_for_researcher = staticmethod(_get_jel_codes_for_researcher)
    get_jel_codes_for_researchers = staticmethod(_get_jel_codes_for_researchers)
    save_researcher_jel_codes = staticmethod(_save_researcher_jel_codes)
    get_researchers_needing_classification = staticmethod(_get_researchers_needing_classification)
    save_paper_topics = staticmethod(_save_paper_topics)
    get_paper_topics_for_researcher = staticmethod(_get_paper_topics_for_researcher)
    get_papers_needing_topics = staticmethod(_get_papers_needing_topics)
    get_all_researcher_topics = staticmethod(_get_all_researcher_topics)
    add_researcher_jel_codes = staticmethod(_add_researcher_jel_codes)
    sync_researcher_fields_from_jel = staticmethod(_sync_researcher_fields_from_jel)

    # Admin
    get_admin_dashboard_stats = staticmethod(_get_admin_dashboard_stats)
