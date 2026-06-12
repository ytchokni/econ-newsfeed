"""Database package — re-exports from submodules.

All functions are importable directly: ``from database import get_connection``.

Submodules:
  connection.py  — pool management and query execution
  schema.py      — DDL, migrations, seeding
  researchers.py — researcher find/create, URL management, CSV import
  papers.py      — title normalization, dedup hashing, draft URLs
  snapshots.py   — append-only versioning for researchers and papers
  llm.py         — LLM usage logging and cost estimation
  jel.py         — JEL code classification and paper topics
"""

from database.connection import (
    get_connection,
    connection_scope,
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
    search_researchers,
    get_researcher_detail,
    get_researcher_papers,
    get_urls_for_researchers,
    get_pub_counts_for_researchers,
    get_fields_for_researchers,
    get_deactivated_urls,
    get_at_risk_urls,
    get_urls_needing_extraction,
    reactivate_url,
)
from database.papers import (
    normalize_title,
    compute_title_hash,
    update_draft_url_status,
    get_unchecked_draft_urls,
    update_openalex_data,
    get_unenriched_papers,
    search_feed_events,
    get_paper_detail,
    get_paper_history,
    get_authors_for_papers,
    get_coauthors_for_papers,
    get_links_for_papers,
)
from database.snapshots import (
    _compute_researcher_content_hash,
    append_researcher_snapshot,
    get_researcher_snapshots,
    _compute_paper_content_hash,
    append_paper_snapshot,
    get_paper_snapshots,
    PaperSnapshotResult,
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
from database.admin import get_admin_dashboard_stats
