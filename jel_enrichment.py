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


def aggregate_jel_for_researcher(researcher_id: int) -> list[str]:
    """Aggregate JEL codes from a researcher's paper topics.

    Returns up to 5 JEL codes, ranked by weighted frequency across papers.
    """
    topics = Database.get_paper_topics_for_researcher(researcher_id)
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

    # Step 2: Aggregate and merge per researcher
    researchers = Database.fetch_all(
        """SELECT DISTINCT r.id, r.first_name, r.last_name
           FROM researchers r
           JOIN authorship a ON a.researcher_id = r.id
           JOIN papers p ON p.id = a.publication_id
           JOIN paper_topics pt ON pt.paper_id = p.id
           ORDER BY r.id"""
    )
    enriched = 0
    for r in researchers:
        codes = aggregate_jel_for_researcher(r["id"])
        if codes:
            Database.add_researcher_jel_codes(r["id"], codes)
            enriched += 1
            logger.info(
                "Enriched JEL for %s %s (id=%d): %s",
                r["first_name"],
                r["last_name"],
                r["id"],
                ", ".join(codes),
            )

    logger.info(
        "JEL enrichment done: %d/%d researchers enriched", enriched, len(researchers)
    )
    return enriched
