from database import Database

class Researcher:
    @staticmethod
    def get_all_researcher_urls() -> list[dict]:
        """Retrieve all researcher URLs from the database."""
        query = "SELECT id, researcher_id, url, page_type FROM researcher_urls"
        return Database.fetch_all(query)