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
  users.py       — user accounts, follows, notification preferences
"""

from backend.database.connection import (
    get_connection,
    connection_scope,
    execute_query,
    fetch_all,
    fetch_one,
)
from backend.database.schema import (
    create_database,
    create_tables,
    seed_research_fields,
    seed_jel_codes,
    backfill_seed_publications,
)
from backend.database.researchers import (
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
from backend.database.papers import (
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
from backend.database.snapshots import (
    _compute_researcher_content_hash,
    append_researcher_snapshot,
    get_researcher_snapshots,
    _compute_paper_content_hash,
    append_paper_snapshot,
    get_paper_snapshots,
    PaperSnapshotResult,
)
from backend.database.llm import log_llm_usage
from backend.database.jel import (
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
from backend.database.admin import get_admin_dashboard_stats
from backend.database.discoveries import (
    get_discovery_candidates,
    insert_discovery,
    get_pending_discoveries,
    approve_discovery,
    reject_discovery,
    bulk_approve_discoveries,
    get_discovery_stats,
    get_recent_discoveries,
)
from backend.database.users import (
    get_or_create_user,
    get_user_by_google_id,
    add_follow,
    remove_follow,
    get_followed_researcher_ids,
    get_notification_prefs,
    update_notification_prefs,
    get_digest_recipients,
    get_feed_events_for_researchers,
    update_last_digest_sent,
    researcher_exists,
    generate_unsubscribe_token,
    verify_unsubscribe_token,
)


# ---------------------------------------------------------------------------
# Backwards-compatible facade — existing code using Database.foo() still works
# ---------------------------------------------------------------------------

class Database:
    """Thin facade re-exporting all database operations as static methods.

    Kept for backwards compatibility. New code should import functions directly
    from the ``backend.database`` package instead of going through this class.
    """

    # Connection
    get_connection = staticmethod(get_connection)
    connection_scope = staticmethod(connection_scope)
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
    search_researchers = staticmethod(search_researchers)
    get_researcher_detail = staticmethod(get_researcher_detail)
    get_researcher_papers = staticmethod(get_researcher_papers)
    get_urls_for_researchers = staticmethod(get_urls_for_researchers)
    get_pub_counts_for_researchers = staticmethod(get_pub_counts_for_researchers)
    get_fields_for_researchers = staticmethod(get_fields_for_researchers)
    get_deactivated_urls = staticmethod(get_deactivated_urls)
    get_at_risk_urls = staticmethod(get_at_risk_urls)
    get_urls_needing_extraction = staticmethod(get_urls_needing_extraction)
    reactivate_url = staticmethod(reactivate_url)

    # Papers
    normalize_title = staticmethod(normalize_title)
    compute_title_hash = staticmethod(compute_title_hash)
    update_draft_url_status = staticmethod(update_draft_url_status)
    get_unchecked_draft_urls = staticmethod(get_unchecked_draft_urls)
    update_openalex_data = staticmethod(update_openalex_data)
    get_unenriched_papers = staticmethod(get_unenriched_papers)
    search_feed_events = staticmethod(search_feed_events)
    get_paper_detail = staticmethod(get_paper_detail)
    get_paper_history = staticmethod(get_paper_history)
    get_authors_for_papers = staticmethod(get_authors_for_papers)
    get_coauthors_for_papers = staticmethod(get_coauthors_for_papers)
    get_links_for_papers = staticmethod(get_links_for_papers)

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
    get_admin_dashboard_stats = staticmethod(get_admin_dashboard_stats)

    # Discoveries
    get_discovery_candidates = staticmethod(get_discovery_candidates)
    insert_discovery = staticmethod(insert_discovery)
    get_pending_discoveries = staticmethod(get_pending_discoveries)
    approve_discovery = staticmethod(approve_discovery)
    reject_discovery = staticmethod(reject_discovery)
    bulk_approve_discoveries = staticmethod(bulk_approve_discoveries)
    get_discovery_stats = staticmethod(get_discovery_stats)
    get_recent_discoveries = staticmethod(get_recent_discoveries)

    # Users
    get_or_create_user = staticmethod(get_or_create_user)
    get_user_by_google_id = staticmethod(get_user_by_google_id)
    add_follow = staticmethod(add_follow)
    remove_follow = staticmethod(remove_follow)
    get_followed_researcher_ids = staticmethod(get_followed_researcher_ids)
    get_notification_prefs = staticmethod(get_notification_prefs)
    update_notification_prefs = staticmethod(update_notification_prefs)
    get_digest_recipients = staticmethod(get_digest_recipients)
    get_feed_events_for_researchers = staticmethod(get_feed_events_for_researchers)
    update_last_digest_sent = staticmethod(update_last_digest_sent)
    researcher_exists = staticmethod(researcher_exists)
    generate_unsubscribe_token = staticmethod(generate_unsubscribe_token)
    verify_unsubscribe_token = staticmethod(verify_unsubscribe_token)
