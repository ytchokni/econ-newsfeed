# Econ Newsfeed

A web scraping system for economics research papers from personal websites. This tool automatically collects, processes, and stores publication information from economists' personal websites.

## Features

- **Database Management**: MySQL database for storing researcher information, publications, and HTML content
- **Web Scraping**: Fetch HTML content from researcher websites and process publication using Open AI API
- **Change Detection**: Only send content after significant website changes

## Requirements

- Requires API Key for OpenAI
- MySQL or MariaDB database

## Setup

1. Clone the repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file with your database and OpenAI API credentials
4. Create the database structure:
   ```
   python database.py
   ```

## Usage

Run the main program:
```
python main.py
```

This will present a menu with options to:
1. Import researcher data from a file
2. Download HTML content from researcher websites
3. Extract publication data from the HTML content
4. Exit the program

## Data Import Format

Import researcher URLs via CSV file with the following columns:
- First name
- Last name
- Position
- Affiliation
- Page type (e.g., "PUB" for publications, "WP" for working papers)
- URL

## Database Structure

- **researchers**: Basic researcher information
- **researcher_urls**: URLs associated with researchers
- **publications**: Publication details (title, year, venue)
- **html_content**: Stored HTML content with hashing for change detection
- **authorship**: Links researchers to publications

