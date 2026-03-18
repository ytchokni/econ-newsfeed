from database import Database
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, ValidationError, field_validator
from typing import Optional
import json
import re
import logging
import os
from urllib.parse import urlparse

OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
_openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))


class PublicationExtraction(BaseModel):
    title: str
    authors: list[list[str]]  # [[first_name, last_name], ...]
    year: Optional[str] = None
    venue: Optional[str] = None
    status: Optional[str] = None
    draft_url: Optional[str] = None
    abstract: Optional[str] = None

    @field_validator('year', mode='before')
    @classmethod
    def coerce_year_to_str(cls, v):
        if v is not None:
            return str(v)
        return v

    @field_validator('status', mode='before')
    @classmethod
    def validate_status(cls, v):
        valid = {'published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit', 'working_paper'}
        if v is not None and v not in valid:
            return None
        return v

    @field_validator('draft_url', mode='before')
    @classmethod
    def validate_draft_url(cls, v):
        if v is None:
            return v
        try:
            parsed = urlparse(str(v))
        except Exception:
            return None
        if parsed.scheme not in ('http', 'https'):
            return None
        return v


class Publication:
    def __init__(self, id, title, authors, year, venue, url):
        self.id = id
        self.title = title
        self.authors = authors
        self.year = year
        self.venue = venue
        self.url = url

    @staticmethod
    def _normalize_title(title):
        """Normalize a publication title for deduplication."""
        return title.lower().strip() if title else ''

    @staticmethod
    def save_publications(url, publications):
        """Save extracted publications to the database, using title_hash for cross-researcher dedup."""
        for pub in publications:
            conn = None
            try:
                title = pub['title'].strip() if pub['title'] else ''
                title_hash = Database.compute_title_hash(pub['title'])

                with Database.get_connection() as conn:
                    cursor = conn.cursor()

                    # INSERT IGNORE leverages uq_title_hash index for cross-researcher dedup
                    cursor.execute(
                        """
                        INSERT IGNORE INTO papers (url, title, title_hash, year, venue, abstract, timestamp, status, draft_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (url, title, title_hash, pub.get('year'), pub.get('venue'),
                         pub.get('abstract'), datetime.now(timezone.utc), pub.get('status'), pub.get('draft_url')),
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
                        author_id = Database.get_researcher_id(first_name, last_name)

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
                logging.error("Error saving publication: %s", type(e).__name__)
                if conn is not None:
                    conn.rollback()

        logging.info(f"{len(publications)} publications processed for {url}")

    @staticmethod
    def extract_relevant_html(html_content):
        """Extract the relevant parts of the HTML that contain the publications."""
        soup = BeautifulSoup(html_content, 'html.parser')
        for element in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            element.decompose()
        main_content = soup.body
        return main_content.get_text(separator='\n', strip=True)

    @staticmethod
    def extract_publications(text_content, url):
        """Use OpenAI to extract publication details from text content."""
        prompt = f"""
        Extract all the publications from the following content from {url}. For each publication, provide:
        - Title
        - Authors as a list of lists: [first name, last name]. Always use full first names when available \
(e.g., "John Smith" not "J. Smith"). If only an initial appears in the source, use the initial as given.
        - Year
        - Venue (e.g., journal or conference name)
        - Status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", or null if unknown
        - Draft URL: a PDF, SSRN, NBER, or working paper link for the paper, or null if not available
        - Abstract: the paper abstract if available, or null if not shown on the page

        Provide the output as a JSON array of objects with the keys: "title", "authors", "year", "venue", "status", "draft_url", "abstract".
        Content:
        {text_content[:4000]}  # Limit content to 4000 characters
        """
        logging.info(f"Extracting publications from {url} using OpenAI ({OPENAI_MODEL})")

        try:
            chat_completion = _openai_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=OPENAI_MODEL,
            )
            
            response = chat_completion.choices[0].message.content
            parsed_response = Publication.parse_openai_response(response)

            if parsed_response is None:
                logging.error(f"Failed to parse OpenAI response for URL: {url}")
                return []

            # Validate each publication through Pydantic
            validated = []
            for item in parsed_response:
                try:
                    pub = PublicationExtraction(**item)
                    validated.append(pub.model_dump())
                except (ValidationError, TypeError) as e:
                    logging.warning(f"Rejected malformed publication from LLM output: {e}")
            return validated
        except Exception as e:
            logging.error("Error in OpenAI API call: %s", type(e).__name__)
            return []

    @staticmethod
    def parse_openai_response(response):
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

    @staticmethod
    def dump_invalid_json(response):
        """Dump invalid JSON responses to a text file, capping at 50 files."""
        dump_dir = "invalid_json_dumps"
        os.makedirs(dump_dir, exist_ok=True)

        MAX_DUMP_FILES = 50
        existing = sorted(f for f in os.listdir(dump_dir) if f.endswith(".txt"))
        while len(existing) >= MAX_DUMP_FILES:
            try:
                os.remove(os.path.join(dump_dir, existing.pop(0)))
            except FileNotFoundError:
                pass  # Another worker already removed this file

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"invalid_json_{timestamp}.txt"
        filepath = os.path.join(dump_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(response)

        logging.warning(f"Invalid JSON dumped to {filepath}")

    @staticmethod
    def is_valid_json(json_string):
        """Check if the provided string is valid JSON."""
        try:
            json.loads(json_string)
            return True
        except json.JSONDecodeError as e:
            logging.error(f"JSON decoding error: {e}")
            return False

    @staticmethod
    def get_all_publications():
        """Retrieve all publications from the database."""
        query = """
            SELECT id, url, title, year, venue
            FROM papers
        """
        results = Database.fetch_all(query)
        return [Publication(id=row[0], url=row[1], title=row[2], year=row[3], venue=row[4], authors=None) for row in results]