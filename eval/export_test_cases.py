"""Export test cases from the database for promptfoo evaluation.

Usage: poetry run python eval/export_test_cases.py

Requires: DB_HOST, DB_USER, DB_PASSWORD, DB_NAME env vars (reads from .env).
"""
import json
import os
import sys

# Add project root to path so we can import db_config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_config import db_config
import mysql.connector

CONTENT_MAX_CHARS = int(os.environ.get('CONTENT_MAX_CHARS', '20000'))
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_cases')


def get_connection():
    return mysql.connector.connect(**db_config)


def export_publication_extraction():
    """Sample pages from html_content that have associated papers."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            # Get URLs that have both html_content and papers — confirmed real publications
            cur.execute("""
                SELECT DISTINCT hc.url_id, hc.content, ru.url
                FROM html_content hc
                JOIN researcher_urls ru ON ru.id = hc.url_id
                JOIN papers p ON p.source_url = ru.url
                WHERE hc.content IS NOT NULL
                  AND LENGTH(hc.content) > 100
                ORDER BY RAND()
                LIMIT 50
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    test_cases = []
    for row in rows:
        text = row['content']
        if len(text) > CONTENT_MAX_CHARS:
            text = text[:CONTENT_MAX_CHARS]
        test_cases.append({
            'vars': {
                'text_content': text,
                'url': row['url'],
            },
            'metadata': {
                'url_id': row['url_id'],
            },
        })

    path = os.path.join(OUTPUT_DIR, 'publication_extraction.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} publication extraction test cases to {path}")
    return test_cases


def export_description_extraction():
    """Same pages as publication extraction — researchers' homepages."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT DISTINCT hc.url_id, hc.content, ru.url
                FROM html_content hc
                JOIN researcher_urls ru ON ru.id = hc.url_id
                WHERE hc.content IS NOT NULL
                  AND LENGTH(hc.content) > 100
                ORDER BY RAND()
                LIMIT 50
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    test_cases = []
    for row in rows:
        text = row['content']
        if len(text) > CONTENT_MAX_CHARS:
            text = text[:CONTENT_MAX_CHARS]
        test_cases.append({
            'vars': {
                'text_content': text,
                'url': row['url'],
            },
            'metadata': {
                'url_id': row['url_id'],
            },
        })

    path = os.path.join(OUTPUT_DIR, 'description_extraction.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} description extraction test cases to {path}")
    return test_cases


def export_jel_classification():
    """Researchers who have descriptions in the DB."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT id, first_name, last_name, description
                FROM researchers
                WHERE description IS NOT NULL
                  AND LENGTH(description) > 20
                ORDER BY RAND()
                LIMIT 50
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    test_cases = []
    for row in rows:
        test_cases.append({
            'vars': {
                'first_name': row['first_name'],
                'last_name': row['last_name'],
                'description': row['description'],
            },
            'metadata': {
                'researcher_id': row['id'],
            },
        })

    path = os.path.join(OUTPUT_DIR, 'jel_classification.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} JEL classification test cases to {path}")
    return test_cases


def export_researcher_disambiguation():
    """Find real same-last-name researcher pairs for disambiguation testing."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            # Find last names shared by multiple researchers
            cur.execute("""
                SELECT last_name
                FROM researchers
                GROUP BY last_name
                HAVING COUNT(*) >= 2
                ORDER BY RAND()
                LIMIT 30
            """)
            shared_last_names = [row['last_name'] for row in cur.fetchall()]

            test_cases = []
            for last_name in shared_last_names:
                cur.execute("""
                    SELECT id, first_name, last_name
                    FROM researchers
                    WHERE last_name = %s
                """, (last_name,))
                candidates = cur.fetchall()
                if len(candidates) < 2:
                    continue

                # Use the first researcher as the "query" author
                query = candidates[0]
                # All researchers with the same last name are candidates
                candidates_text = "\n".join(
                    f"- ID {c['id']}: {c['first_name']} {c['last_name']}"
                    for c in candidates
                )
                test_cases.append({
                    'vars': {
                        'first_name': query['first_name'],
                        'last_name': query['last_name'],
                        'candidates_text': candidates_text,
                    },
                    'metadata': {
                        'query_researcher_id': query['id'],
                        'candidate_ids': [c['id'] for c in candidates],
                    },
                })
    finally:
        conn.close()

    path = os.path.join(OUTPUT_DIR, 'researcher_disambiguation.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} researcher disambiguation test cases to {path}")
    return test_cases


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("Exporting test cases from database...\n")

    pub = export_publication_extraction()
    desc = export_description_extraction()
    jel = export_jel_classification()
    disambig = export_researcher_disambiguation()

    print(f"\nDone. Total test cases: {len(pub) + len(desc) + len(jel) + len(disambig)}")
