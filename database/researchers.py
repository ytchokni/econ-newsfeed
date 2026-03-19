"""Researcher data access: find/create, URL management, CSV import."""
import csv
import json
import logging
import os
import re

from database.connection import execute_query, fetch_one, fetch_all
from database.llm import log_llm_usage


def _disambiguate_researcher(first_name, last_name, candidates):
    """Use LLM to check if any same-last-name candidate is the same person.
    Returns the matching researcher id (int) or None if no match.
    candidates: list of dicts with keys id, first_name, last_name."""
    candidates_text = "\n".join(f"- ID {c['id']}: {c['first_name']} {c['last_name']}" for c in candidates)
    prompt = (
        f'You are disambiguating researcher names. A publication lists the author as: '
        f'"{first_name} {last_name}"\n\n'
        f'The database contains these existing researchers with the same last name:\n'
        f'{candidates_text}\n\n'
        f'Is the author the same person as any of these researchers? Consider:\n'
        f'- "J. Smith" and "John Smith" are likely the same person\n'
        f'- An abbreviated first name may match a full first name\n'
        f'- Only match if you are confident\n\n'
        f'Respond with JSON only: {{"match_id": <id or null>}}'
    )
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model,
        )
        log_llm_usage("researcher_disambiguation", model, response.usage)
        content = response.choices[0].message.content
        match = re.search(r'\{.*?\}', content, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            match_id = data.get('match_id')
            if match_id is not None:
                candidate_ids = {c['id'] for c in candidates}
                match_id_int = int(match_id)
                if match_id_int in candidate_ids:
                    return match_id_int
                logging.warning(
                    f"LLM returned match_id={match_id} not in candidate IDs {candidate_ids}; ignoring"
                )
    except Exception as e:
        logging.error(f"LLM researcher disambiguation error: {e}")
    return None


def get_researcher_id(first_name, last_name, position=None, affiliation=None, conn=None):
    """Get the researcher ID based on name. Uses LLM disambiguation for ambiguous matches.
    Accepts optional conn to reuse an existing DB connection (avoids pool exhaustion)."""
    def _fetch_one(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True)
            c.execute(query, params)
            row = c.fetchone()
            c.close()
            return row
        return fetch_one(query, params)

    def _fetch_all(query, params):
        if conn is not None:
            c = conn.cursor(dictionary=True)
            c.execute(query, params)
            rows = c.fetchall()
            c.close()
            return rows
        return fetch_all(query, params)

    def _execute(query, params):
        if conn is not None:
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            lid = c.lastrowid
            c.close()
            return lid
        return execute_query(query, params)

    # 1. Exact match
    result = _fetch_one(
        "SELECT id FROM researchers WHERE first_name = %s AND last_name = %s",
        (first_name, last_name),
    )
    if result:
        return result['id']

    # 2. Same-last-name candidates — let LLM decide if any is the same person
    candidates = _fetch_all(
        "SELECT id, first_name, last_name FROM researchers WHERE last_name = %s",
        (last_name,),
    )
    if candidates:
        match_id = _disambiguate_researcher(first_name, last_name, candidates)
        if match_id is not None:
            logging.info(
                f"LLM matched '{first_name} {last_name}' to existing researcher id={match_id}"
            )
            return match_id

    # 3. No match found — insert new researcher
    new_id = _execute(
        "INSERT INTO researchers (first_name, last_name, position, affiliation) VALUES (%s, %s, %s, %s)",
        (first_name, last_name, position, affiliation),
    )
    return new_id


def update_researcher_bio(researcher_id, bio):
    """Legacy: update researcher description only if the current description is NULL."""
    execute_query(
        "UPDATE researchers SET description = %s WHERE id = %s AND description IS NULL",
        (bio, researcher_id),
    )


def add_researcher_url(researcher_id, page_type, url):
    """Insert a new URL for a researcher into the researcher_urls table."""
    execute_query(
        "INSERT IGNORE INTO researcher_urls (researcher_id, page_type, url) VALUES (%s, %s, %s)",
        (researcher_id, page_type, url),
    )


def import_data_from_file(file_path):
    """Import data from a CSV or TXT file into the database."""
    try:
        with open(file_path, mode='r', encoding='utf-8-sig') as file:
            reader = csv.reader(file)
            next(reader, None)  # Skip header
            for row in reader:
                if len(row) < 6:
                    logging.warning(f"Skipping incomplete row: {row}")
                    continue
                first_name, last_name, position, affiliation, page_type, url = row
                researcher_id = get_researcher_id(first_name, last_name, position, affiliation)
                add_researcher_url(researcher_id, page_type, url)
        logging.info("Data imported successfully from file")
    except Exception as e:
        logging.error("Error importing data from file: %s", type(e).__name__)
