from database import Database
from datetime import datetime
from bs4 import BeautifulSoup
from openai import OpenAI
import requests
from db_config import db_config
import json
import re

class Publication:
    @staticmethod
    def extract_publications(html_content, url):
        """
        Use ScrapeGraph AI to extract publication information from HTML content.
        Returns a list of publications.
        """
        text_content = Publication.extract_relevant_html(html_content)
        publications = Publication.extract_publications_with_openai(text_content, url)
        return publications

    @staticmethod
    def save_publications(url, publications):
        """
        Save extracted publications to the database.
        """
        query = """
            INSERT INTO publications (url, title, authors, year, journal, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        for pub in publications:
            Database.execute_query(query, (
                url,
                pub.get('title', ''),
                pub.get('authors', ''),
                pub.get('year', ''),
                pub.get('venue', ''),  # Changed from 'journal' to 'venue' to match OpenAI output
                datetime.now()
            ))
        print(f"{len(publications)} publications saved successfully for {url}")

    @staticmethod
    def is_valid_json(json_string):
        """
        Check if the provided string is valid JSON.
        """
        try:
            json.loads(json_string)
        except json.JSONDecodeError as e:
            print(f"JSON decoding error: {e}")
            return False
        return True

    @staticmethod
    def extract_relevant_html(html_content):
        """
        Use BeautifulSoup to extract the relevant parts of the HTML that contain the publications.
        """
        soup = BeautifulSoup(html_content, 'html.parser')

        # Remove scripts and styles
        for element in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            element.decompose()

        # Attempt to find the main content area
        main_content = soup.body
            
        # Extract the text content
        text_content = main_content.get_text(separator='\n', strip=True)
        return text_content

    @staticmethod
    def extract_publications_with_openai(text_content, url):
        prompt = f"""
        You are an AI assistant that extracts academic publication details from HTML content.

        Extract all the publications from the following HTML content from {url}. For each publication, provide:
        - Title
        - Authors
        - Year
        - Journal (e.g., journal or conference name)

        Provide the output as a JSON array of objects with the keys: "title", "authors", "year", "venue".
        HTML Content:
        {text_content}
        """

        client = OpenAI(
            api_key=db_config['openai_api_key']
        )

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="gpt-3.5-turbo",
        )
        
        response = chat_completion.choices[0].message.content

        # Check if the response is valid JSON
        if Publication.is_valid_json(response):
            publications = json.loads(response)
            return publications
        else:
            print("Initial response is not valid JSON. Attempting to extract JSON content...")

            # Attempt to extract JSON array from the response using regex
            json_text_match = re.search(r'\[\s*\{.*?\}\s*\]', response, re.DOTALL)
            if json_text_match:
                json_text = json_text_match.group(0)
                if Publication.is_valid_json(json_text):
                    publications = json.loads(json_text)
                    return publications
                else:
                    print("Extracted content is still not valid JSON.")
                    return None
            else:
                print("No JSON array found in the response.")
                return None

html_content = requests.get(URL).text
text_content = extract_relevant_html(html_content)
publications = extract_publications(text_content)