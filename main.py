from database import Database
from researcher import Researcher
from publication import Publication
from html_fetcher import HTMLFetcher
import requests

def import_data():
    """
    Import data from a file into the database.
    """
    file_path = input("Enter the path to the file: ")
    Database.import_data_from_file(file_path)

def fetch_and_save_urls():
    """
    Fetch HTML content for all URLs in the database and save it.
    """
    urls = Researcher.get_all_urls()
    for url in urls:
        print(f"Fetching HTML for: {url}")
        html_content = HTMLFetcher.fetch_html(url)
        if html_content:
            HTMLFetcher.save_html(url, html_content)
        print("---")

def fetch_save_and_extract_publications():
    """
    Fetch HTML content for all URLs, save it, extract publications, and save the publications.
    """
    urls = Researcher.get_all_urls()
    for url in urls:
        print(f"Fetching HTML for: {url}")
        html_content = HTMLFetcher.fetch_html(url)
        if html_content:
            HTMLFetcher.save_html(url, html_content)
            publications = Publication.extract_publications(html_content, url)
            if publications:
                Publication.save_publications(url, publications)
            else:
                print(f"No publications found for {url}")
        print("---")

def process_url(url):
    html_content = requests.get(url).text
    publications = Publication.extract_publications(html_content, url)
    if publications:
        Publication.save_publications(url, publications)
    else:
        print(f"No publications extracted for {url}")



def main():
    """
    Main function to handle user input and execute the appropriate actions.
    """
    while True:
        choice = input("Enter '1' to import data from a file, '2' to fetch and save HTML, '3' to fetch, save, and extract publications, or '4' to exit: ")
        if choice == '1':
            import_data()
        elif choice == '2':
            fetch_and_save_urls()
        elif choice == '3':
            fetch_save_and_extract_publications()
        elif choice == '4':
            print("Exiting the program.")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()