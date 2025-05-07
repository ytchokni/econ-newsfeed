import logging
from database import Database
from researcher import Researcher
from publication import Publication
from html_fetcher import HTMLFetcher

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def import_data():
    """Import data from a file into the database."""
    file_path = input("Enter the path to the file: ")
    Database.import_data_from_file(file_path)
    logging.info(f"Data imported from {file_path}")

def download_htmls():
    """Download HTML content for all URLs in the researcher_urls table."""
    researcher_urls = Researcher.get_all_researcher_urls()
    for id, researcher_id, url, page_type in researcher_urls:
        logging.info(f"Downloading HTML for URL ID: {id}, URL: {url}, Page Type: {page_type}")
        HTMLFetcher.fetch_and_save_if_changed(id, url, researcher_id)

def extract_data_from_htmls():
    """Extract publication data from downloaded HTML content."""
    researcher_urls = Researcher.get_all_researcher_urls()
    for id, researcher_id, url, page_type in researcher_urls:
        if page_type in ["PUB", "WP"]:
            logging.info(f"Extracting data from HTML for URL ID: {id}, URL: {url}, Page Type: {page_type}")
            html_content = HTMLFetcher.get_latest_text(id)
            if html_content:
                extracted_publications = Publication.extract_publications(html_content, url)
                if extracted_publications:
                    Publication.save_publications(url, extracted_publications)
                else:
                    logging.warning(f"No publications extracted for URL ID: {id}, URL: {url}")
            else:
                logging.error(f"No HTML content found for URL ID: {id}, URL: {url}")
        else:
            logging.info(f"Skipping extraction for URL ID: {id}, URL: {url}, Page Type: {page_type}")

def main():
    """Main function to handle user input and execute the appropriate actions."""
    actions = {
        '1': ('Import data from a file', import_data),
        '2': ('Download HTML content', download_htmls),
        '3': ('Extract data from HTML content', extract_data_from_htmls),
        '4': ('Exit', lambda: "exit")
    }

    while True:
        print("\nChoose an action:")
        for key, (description, _) in actions.items():
            print(f"{key}: {description}")

        choice = input("Enter your choice: ")
        action = actions.get(choice)

        if action:
            result = action[1]()
            if result == "exit":
                print("Exiting the program.")
                break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()