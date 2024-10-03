from database import Database
from datetime import datetime
from bs4 import BeautifulSoup
from openai import OpenAI
import json
import re
import logging
from db_config import db_config

class Publication:
    def __init__(self, id, title, authors, year, venue, url, researcher_id):
        self.id = id
        self.title = title
        self.authors = authors
        self.year = year
        self.venue = venue
        self.url = url
        self.researcher_id = researcher_id

    @staticmethod
    def get_or_create_publication_record(url, researcher_id):
        """Get or create a publication record and return its ID."""
        query = """
            SELECT id FROM publications
            WHERE url = %s AND researcher_id = %s
        """
        result = Database.fetch_one(query, (url, researcher_id))
        if result:
            return result[0]
        else:
            return Publication.create_publication_record(url, researcher_id)

    @staticmethod
    def create_publication_record(url, researcher_id):
        """Create a new publication record and return its ID."""
        query = """
            INSERT INTO publications (url, researcher_id, timestamp)
            VALUES (%s, %s, %s)
            RETURNING id
        """
        return Database.execute_query(query, (url, researcher_id, datetime.now()))

    @staticmethod
    def extract_publications(html_content, url):
        """Extract publication information from HTML content using OpenAI."""
        text_content = Publication.extract_relevant_html(html_content)
        return Publication.extract_publications_with_openai(text_content, url)

    @staticmethod
    def save_publications(url, publications, researcher_id):
        """Save extracted publications to the database."""
        query = """
            INSERT INTO publications (url, researcher_id, title, authors, year, venue, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        for pub in publications:
            Database.execute_query(query, (
                url,
                researcher_id,
                pub.get('title', ''),
                pub.get('authors', ''),
                pub.get('year', ''),
                pub.get('venue', ''),
                datetime.now()
            ))
        logging.info(f"{len(publications)} publications saved successfully for {url}")

    @staticmethod
    def extract_relevant_html(html_content):
        """Extract the relevant parts of the HTML that contain the publications."""
        soup = BeautifulSoup(html_content, 'html.parser')
        for element in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            element.decompose()
        main_content = soup.body
        return main_content.get_text(separator='\n', strip=True)

    @staticmethod
    def extract_publications_with_openai(text_content, url):
        """Use OpenAI to extract publication details from text content."""
        prompt = f"""
        Extract all the publications from the following content from {url}. For each publication, provide:
        - Title
        - Authors
        - Year
        - Venue (e.g., journal or conference name)

        Provide the output as a JSON array of objects with the keys: "title", "authors", "year", "venue".
        Content:
        {text_content[:4000]}  # Limit content to 4000 characters
        """

        client = OpenAI(api_key=db_config['openai_api_key'])
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-3.5-turbo",
        )
        
        response = chat_completion.choices[0].message.content
        return Publication.parse_openai_response(response)

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
        
        logging.error("Failed to extract valid JSON from OpenAI response")
        return None

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
            SELECT id, url, researcher_id
            FROM publications
        """
        results = Database.fetch_all(query)
        return [Publication(id=row[0], url=row[1], researcher_id=row[2], title=None, authors=None, year=None, venue=None) for row in results]

    @staticmethod
    def update_publication(publication_id, publication_data):
        """Update a publication record with extracted data."""
        query = """
            UPDATE publications
            SET title = %s, authors = %s, year = %s, venue = %s
            WHERE id = %s
        """
        Database.execute_query(query, (
            publication_data.get('title', ''),
            publication_data.get('authors', ''),
            publication_data.get('year', ''),
            publication_data.get('venue', ''),
            publication_id
        ))
        logging.info(f"Publication {publication_id} updated successfully")