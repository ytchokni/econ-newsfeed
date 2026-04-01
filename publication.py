from database import Database
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, ValidationError, field_validator
from typing import Literal, Optional
import json
import re
import logging
import os
import threading
from urllib.parse import urlparse

OPENAI_MODEL = os.environ.get('OPENAI_MODEL')
_openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

CONTENT_MAX_CHARS = int(os.environ.get('CONTENT_MAX_CHARS', '4000'))

VALID_STATUSES = frozenset({
    'published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper',
})

_VALID_STATUSES = Literal[
    'published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper'
]


class PublicationExtraction(BaseModel):
    title: str
    authors: list[list[str]]  # [[first_name, last_name], ...]
    year: Optional[str] = None
    venue: Optional[str] = None
    status: Optional[_VALID_STATUSES] = None
    draft_url: Optional[str] = None
    abstract: Optional[str] = None

    @field_validator('year', mode='before')
    @classmethod
    def coerce_year_to_str(cls, v: object) -> str | None:
        if v is not None:
            return str(v)
        return v

    @field_validator('draft_url', mode='before')
    @classmethod
    def validate_draft_url(cls, v: object) -> str | None:
        if v is None:
            return v
        try:
            parsed = urlparse(str(v))
        except Exception:
            return None
        if parsed.scheme not in ('http', 'https'):
            return None
        return v


class PublicationExtractionList(BaseModel):
    """Wrapper for structured output — OpenAI requires a top-level object."""
    publications: list[PublicationExtraction]


class Publication:
    def __init__(self, id: int, title: str, authors: list | None, year: str | None, venue: str | None, url: str | None) -> None:
        self.id = id
        self.title = title
        self.authors = authors
        self.year = year
        self.venue = venue
        self.url = url

    @staticmethod
    def save_publications(
        url: str,
        publications: list[dict],
        is_seed: bool = False,
    ) -> None:
        """Save extracted publications to the database, using title_hash for cross-researcher dedup."""
        with Database.get_connection() as conn:
            for pub in publications:
                try:
                    title = pub['title'].strip() if pub['title'] else ''
                    title_hash = Database.compute_title_hash(pub['title'])

                    cursor = conn.cursor()

                    # INSERT IGNORE leverages uq_title_hash index for cross-researcher dedup.
                    # is_seed is set on INSERT but NOT updated on duplicate (preserves original).
                    cursor.execute(
                        """
                        INSERT IGNORE INTO papers (source_url, title, title_hash, year, venue, abstract, discovered_at, status, draft_url, is_seed)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (url, title, title_hash, pub.get('year'), pub.get('venue'),
                         pub.get('abstract'), datetime.now(timezone.utc), pub.get('status'),
                         pub.get('draft_url'), is_seed),
                    )

                    if cursor.lastrowid:
                        publication_id = cursor.lastrowid
                        # Add source URL to paper_urls
                        cursor.execute(
                            """
                            INSERT IGNORE INTO paper_urls (paper_id, url, discovered_at)
                            VALUES (%s, %s, %s)
                            """,
                            (publication_id, url, datetime.now(timezone.utc)),
                        )
                        # Create new_paper feed event for non-seed papers with known, non-published status
                        pub_status = pub.get('status')
                        if not is_seed and pub_status and pub_status != 'published':
                            cursor.execute(
                                """
                                INSERT INTO feed_events (paper_id, event_type, new_status, created_at)
                                VALUES (%s, 'new_paper', %s, %s)
                                """,
                                (publication_id, pub_status, datetime.now(timezone.utc)),
                            )
                    else:
                        # Duplicate found via title_hash — fetch existing id
                        cursor.execute(
                            "SELECT id FROM papers WHERE title_hash = %s",
                            (title_hash,),
                        )
                        row = cursor.fetchone()
                        if not row:
                            logging.error(f"Could not find publication after INSERT IGNORE: {pub['title']}")
                            cursor.close()
                            continue
                        publication_id = row[0]
                        # Add the new source URL to paper_urls for cross-researcher tracking
                        cursor.execute(
                            """
                            INSERT IGNORE INTO paper_urls (paper_id, url, discovered_at)
                            VALUES (%s, %s, %s)
                            """,
                            (publication_id, url, datetime.now(timezone.utc)),
                        )
                        logging.info(f"Duplicate publication (title_hash match), added source URL: {pub['title']}")

                    # Process authors
                    for author_order, author in enumerate(pub['authors'], start=1):
                        first_name, last_name = author
                        author_id = Database.get_researcher_id(first_name, last_name, conn=conn)

                        # INSERT IGNORE prevents duplicate authorship entries (uq_researcher_pub)
                        cursor.execute(
                            """
                            INSERT IGNORE INTO authorship (researcher_id, publication_id, author_order)
                            VALUES (%s, %s, %s)
                            """,
                            (author_id, publication_id, author_order),
                        )

                    conn.commit()
                    cursor.close()
                    logging.info(f"Publication saved successfully: {pub['title']}")

                except Exception as e:
                    logging.error(
                        "Error saving publication '%s': %s: %s",
                        pub.get('title', '<unknown>'), type(e).__name__, e,
                    )
                    conn.rollback()

        logging.info(f"{len(publications)} publications processed for {url}")

    @staticmethod
    def extract_relevant_html(html_content: str) -> str:
        """Extract the relevant parts of the HTML, preserving hyperlinks as inline text.

        Converts <a href="URL">text</a> to 'text (URL)' before stripping HTML,
        so the LLM can see draft/paper URLs that would otherwise be lost.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        for element in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            element.decompose()
        # Inline hyperlinks before stripping tags
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if href and href.startswith(('http://', 'https://')):
                a.replace_with(f"{text} ({href})" if text else href)
        main_content = soup.body
        return main_content.get_text(separator='\n', strip=True)

    @staticmethod
    def build_extraction_prompt(text_content: str, url: str) -> str:
        """Build the LLM prompt for publication extraction."""
        return f"""Extract all academic publications from the following researcher page content from {url}.

For each publication, extract:
- title: the full publication title
- authors: a list of [first_name, last_name] pairs. Use full first names when available (e.g., "John" not "J."). If only an initial appears, use it as given.
- year: publication year as a string, or null if unknown
- venue: journal or conference name, or null if unknown
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", or null if unknown
- draft_url: a URL to a PDF, SSRN, NBER, or working paper version, or null if not available
- abstract: the paper abstract, or null if not shown on the page

If no publications are found in the content, return an empty list. Do not fabricate publications.

Content:
{text_content[:CONTENT_MAX_CHARS]}"""

    @staticmethod
    def extract_publications(text_content: str, url: str, scrape_log_id: int | None = None) -> list[dict]:
        """Use OpenAI to extract publication details from text content."""
        prompt = Publication.build_extraction_prompt(text_content, url)
        logging.info(f"Extracting publications from {url} using OpenAI ({OPENAI_MODEL})")

        try:
            chat_completion = _openai_client.beta.chat.completions.parse(
                messages=[{"role": "user", "content": prompt}],
                model=OPENAI_MODEL,
                response_format=PublicationExtractionList,
            )
            Database.log_llm_usage(
                "publication_extraction", OPENAI_MODEL, chat_completion.usage,
                context_url=url, scrape_log_id=scrape_log_id,
            )

            message = chat_completion.choices[0].message
            if message.refusal:
                logging.warning(f"Model refused extraction for {url}: {message.refusal}")
                return []

            result = message.parsed
            if result is None:
                logging.error(f"Failed to parse structured output for URL: {url}")
                return []

            return [pub.model_dump() for pub in result.publications]
        except Exception as e:
            logging.error("Error in OpenAI API call for %s: %s: %s", url, type(e).__name__, e)
            return []

    @staticmethod
    def parse_openai_response(response: str) -> list | None:
        """Parse the OpenAI response and extract the JSON content."""
        if Publication.is_valid_json(response):
            return json.loads(response)
        
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', response, re.DOTALL)
        if json_match:
            json_text = json_match.group(0)
            if Publication.is_valid_json(json_text):
                return json.loads(json_text)
        
        # Dump invalid JSON to a file
        Publication.dump_invalid_json(response)
        
        logging.error("Failed to extract valid JSON from OpenAI response")
        return None

    _dump_lock = threading.Lock()

    @staticmethod
    def dump_invalid_json(response: str) -> None:
        """Log invalid JSON responses. Uses structured logging instead of filesystem writes
        to avoid data loss on ephemeral cloud container filesystems."""
        # Truncate to avoid flooding log aggregators with huge payloads
        preview = response[:2000] + ("..." if len(response) > 2000 else "")
        with Publication._dump_lock:
            logging.warning("Invalid JSON from OpenAI (len=%d): %s", len(response), preview)

    @staticmethod
    def is_valid_json(json_string: str) -> bool:
        """Check if the provided string is valid JSON."""
        try:
            json.loads(json_string)
            return True
        except json.JSONDecodeError as e:
            logging.error(f"JSON decoding error: {e}")
            return False

    @staticmethod
    def get_all_publications() -> list["Publication"]:
        """Retrieve all publications from the database."""
        query = """
            SELECT id, source_url, title, year, venue
            FROM papers
        """
        results = Database.fetch_all(query)
        return [Publication(id=row['id'], url=row['source_url'], title=row['title'], year=row['year'], venue=row['venue'], authors=None) for row in results]


def _title_similarity(title_a: str | None, title_b: str | None) -> float:
    """Jaccard similarity on normalized word tokens. Used to detect title renames."""
    tokens_a = set(Database.normalize_title(title_a).split())
    tokens_b = set(Database.normalize_title(title_b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


_SIMILARITY_THRESHOLD = 0.5


def reconcile_title_renames(source_url: str, extracted_pubs: list[dict]) -> None:
    """Detect title renames by comparing extracted papers against DB papers for the same URL.

    For each disappeared+appeared title pair with Jaccard similarity >= 0.5:
    - Update the existing paper's title and title_hash
    - Record the old title in a paper_snapshot
    - Delete any duplicate paper row that save_publications may have created
    - Create a title_change feed_event
    """
    existing = Database.fetch_all(
        "SELECT id, title, title_hash FROM papers WHERE source_url = %s",
        (source_url,),
    )
    if not existing:
        return

    existing_normalized = {
        Database.normalize_title(p['title']): p for p in existing
    }
    extracted_normalized = {
        Database.normalize_title(pub['title']): pub for pub in extracted_pubs if pub.get('title')
    }

    disappeared = set(existing_normalized.keys()) - set(extracted_normalized.keys())
    appeared = set(extracted_normalized.keys()) - set(existing_normalized.keys())

    if not disappeared or not appeared:
        return

    # Greedy best-match: pair each appeared title with its best disappeared match
    matched_disappeared = set()
    renames = []

    for app_norm in appeared:
        best_sim = 0.0
        best_dis = None
        for dis_norm in disappeared:
            if dis_norm in matched_disappeared:
                continue
            sim = _title_similarity(
                existing_normalized[dis_norm]['title'],
                extracted_normalized[app_norm]['title'],
            )
            if sim > best_sim:
                best_sim = sim
                best_dis = dis_norm

        if best_dis is not None and best_sim >= _SIMILARITY_THRESHOLD:
            matched_disappeared.add(best_dis)
            renames.append((
                existing_normalized[best_dis],  # old paper row
                extracted_normalized[app_norm],  # new pub dict
                best_sim,
            ))

    if not renames:
        return

    with Database.get_connection() as conn:
        cursor = conn.cursor(buffered=True)
        try:
            for old_paper, new_pub, sim in renames:
                old_id = old_paper['id']
                old_title = old_paper['title']
                new_title = new_pub['title'].strip()
                new_hash = Database.compute_title_hash(new_title)

                # Record old title in paper_snapshot
                Database.append_paper_snapshot(
                    paper_id=old_id,
                    status=new_pub.get('status'),
                    venue=new_pub.get('venue'),
                    abstract=new_pub.get('abstract'),
                    draft_url=new_pub.get('draft_url'),
                    year=new_pub.get('year'),
                    source_url=source_url,
                    title=old_title,
                )

                # Update the paper in place
                cursor.execute(
                    "UPDATE papers SET title = %s, title_hash = %s WHERE id = %s",
                    (new_title, new_hash, old_id),
                )

                # Delete duplicate paper row if save_publications inserted one
                cursor.execute(
                    "SELECT id FROM papers WHERE title_hash = %s AND id != %s",
                    (new_hash, old_id),
                )
                dup = cursor.fetchone()
                if dup:
                    dup_id = dup[0]
                    # Transfer paper_urls from duplicate to original
                    cursor.execute(
                        "UPDATE IGNORE paper_urls SET paper_id = %s WHERE paper_id = %s",
                        (old_id, dup_id),
                    )
                    # Delete false feed events for the duplicate
                    cursor.execute(
                        "DELETE FROM feed_events WHERE paper_id = %s",
                        (dup_id,),
                    )
                    # Delete the duplicate paper (CASCADE deletes authorship, etc.)
                    cursor.execute(
                        "DELETE FROM papers WHERE id = %s",
                        (dup_id,),
                    )

                # Create title_change feed event
                cursor.execute(
                    """INSERT INTO feed_events
                       (paper_id, event_type, old_title, new_title, created_at)
                       VALUES (%s, 'title_change', %s, %s, %s)""",
                    (old_id, old_title, new_title, datetime.now(timezone.utc)),
                )

                logging.info(
                    "Title rename detected (sim=%.2f): '%s' → '%s' (paper_id=%d)",
                    sim, old_title[:50], new_title[:50], old_id,
                )

            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.error("Error reconciling title renames for %s: %s", source_url, e)
        finally:
            cursor.close()