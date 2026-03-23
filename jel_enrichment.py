# jel_enrichment.py
"""JEL enrichment pipeline: derive researcher JEL codes from paper topics.

Fetches OpenAlex topics for papers, maps them to JEL codes, and
merges with existing bio-based JEL classifications.
"""
import logging
from collections import Counter

from database import Database
from openalex import fetch_topics_batch
from topic_jel_map import map_topic_to_jel

logger = logging.getLogger(__name__)


def aggregate_jel_from_topics(topics: list[dict]) -> list[str]:
    """Aggregate JEL codes from a list of paper topics.

    Returns up to 5 JEL codes, ranked by weighted frequency.
    """
    jel_counter: Counter = Counter()
    for topic in topics:
        codes = map_topic_to_jel(topic["topic_name"])
        score = float(topic.get("score") or 0.5)
        for code in codes:
            jel_counter[code] += score

    return [code for code, _ in jel_counter.most_common(5)]


def enrich_jel_from_papers() -> int:
    """Main pipeline: fetch topics, map to JEL, aggregate per researcher.

    Returns number of researchers enriched.
    """
    # Step 1: Fetch and store topics for papers missing them
    papers = Database.get_papers_needing_topics()
    if papers:
        logger.info("Fetching topics for %d papers from OpenAlex", len(papers))
        openalex_ids = [p["openalex_id"] for p in papers]
        topics_by_id = fetch_topics_batch(openalex_ids)
        stored = 0
        for paper in papers:
            topics = topics_by_id.get(paper["openalex_id"], [])
            if topics:
                Database.save_paper_topics(paper["id"], topics)
                stored += 1
        logger.info("Stored topics for %d/%d papers", stored, len(papers))

    # Step 2: Batch-fetch all topics and aggregate per researcher
    all_topics = Database.get_all_researcher_topics()
    enriched = 0
    for researcher_id, topics in all_topics.items():
        codes = aggregate_jel_from_topics(topics)
        if codes:
            Database.add_researcher_jel_codes(researcher_id, codes)
            enriched += 1
            logger.info(
                "Enriched JEL for researcher %d: %s",
                researcher_id,
                ", ".join(codes),
            )

    logger.info(
        "JEL enrichment done: %d/%d researchers enriched",
        enriched,
        len(all_topics),
    )
    return enriched
