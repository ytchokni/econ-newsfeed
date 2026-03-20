import logging
from database import Database

class Researcher:
    def __init__(self, id: int, name: str, position: str | None, affiliation: str | None, urls: list) -> None:
        self.id = id
        self.name = name
        self.position = position
        self.affiliation = affiliation
        self.urls = urls

    @staticmethod
    def get_all_researchers() -> list["Researcher"]:
        """Retrieve all researchers from the database."""
        query = """
            SELECT r.id AS researcher_id, r.last_name, r.first_name, r.position, r.affiliation, ru.id AS url_id, ru.url
            FROM researchers r
            LEFT JOIN researcher_urls ru ON r.id = ru.researcher_id
        """
        results = Database.fetch_all(query)
        researchers = {}
        for row in results:
            researcher_id = row['researcher_id']
            last_name = row['last_name']
            first_name = row['first_name']
            position = row['position']
            affiliation = row['affiliation']
            url_id = row['url_id']
            url = row['url']
            if researcher_id not in researchers:
                researchers[researcher_id] = Researcher(
                    researcher_id,
                    f"{first_name} {last_name}",
                    position,
                    affiliation,
                    []
                )
            if url:
                researchers[researcher_id].urls.append((url_id, url))
        return list(researchers.values())

    @staticmethod
    def get_all_researcher_urls() -> list[dict]:
        """Retrieve all researcher URLs from the database."""
        query = "SELECT id, researcher_id, url, page_type FROM researcher_urls"
        return Database.fetch_all(query)

    @staticmethod
    def add_researcher(last_name: str, first_name: str, position: str, affiliation: str) -> int:
        """Add a new researcher to the database."""
        query = """
            INSERT INTO researchers (last_name, first_name, position, affiliation)
            VALUES (%s, %s, %s, %s)
        """
        return Database.execute_query(query, (last_name, first_name, position, affiliation))

    @staticmethod
    def add_url_to_researcher(researcher_id: int, url: str, page_type: str = "HOME") -> None:
        """Associate a URL with a researcher in the database, preventing duplication."""
        Database.add_researcher_url(researcher_id, page_type, url)