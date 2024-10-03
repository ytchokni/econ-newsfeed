from database import Database
from datetime import datetime
from bs4 import BeautifulSoup
from openai import OpenAI
import json
import re
import logging
import os 

class Publication:
    def __init__(self, id, title, authors, year, venue, url):
        self.id = id
        self.title = title
        self.authors = authors
        self.year = year
        self.venue = venue
        self.url = url

    @staticmethod
    def save_publications(url, publications):
        """Save extracted publications to the database."""
        for pub in publications:
            try:
                # Start a transaction
                with Database.get_connection() as conn:
                    cursor = conn.cursor()

                    # Insert publication
                    query = """
                    INSERT INTO publications (url, title, year, venue, timestamp)
                    VALUES (%s, %s, %s, %s, %s)
                    """
                    params = (url, pub['title'], pub['year'], pub['venue'], datetime.now())
                    cursor.execute(query, params)

                    # Get the last inserted ID
                    publication_id = cursor.lastrowid

                    # Process authors
                    for author_order, author in enumerate(pub['authors'], start=1):
                        first_name, last_name = author
                        # Get or create researcher
                        author_id = Database.get_researcher_id(first_name, last_name)
                        
                        # Create authorship entry
                        authorship_query = """
                        INSERT INTO authorship (researcher_id, publication_id, author_order)
                        VALUES (%s, %s, %s)
                        """
                        authorship_params = (author_id, publication_id, author_order)
                        cursor.execute(authorship_query, authorship_params)

                    # Commit the transaction
                    conn.commit()
                    logging.info(f"Publication saved successfully: {pub['title']}")

            except Exception as e:
                logging.error(f"Error saving publication: {str(e)}")
                # If there's an error, rollback the transaction
                if 'conn' in locals():
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
        - Authors as a list of lists: [first name, last name]
        - Year
        - Venue (e.g., journal or conference name)

        Provide the output as a JSON array of objects with the keys: "title", "authors", "year", "venue".
        Content:
        {text_content[:4000]}  # Limit content to 4000 characters
        """
        logging.info(f"Extracting publications from using openai {url}")
        
        client = OpenAI(api_key= os.environ.get('OPENAI_API_KEY'))
        try:
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-3.5-turbo",
            )
            
            response = chat_completion.choices[0].message.content
            parsed_response = Publication.parse_openai_response(response)
            
            if parsed_response is None:
                logging.error(f"Failed to parse OpenAI response for URL: {url}")
                return []
            
            return parsed_response
        except Exception as e:
            logging.error(f"Error in OpenAI API call: {str(e)}")
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
        """Dump invalid JSON responses to a text file."""
        dump_dir = "invalid_json_dumps"
        os.makedirs(dump_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
            FROM publications
        """
        results = Database.fetch_all(query)
        return [Publication(id=row[0], url=row[1], title=row[2], year=row[3], venue=row[4], authors=None) for row in results]