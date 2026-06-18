from database import fetch_all

class Researcher:
    @staticmethod
    def get_all_researcher_urls() -> list[dict]:
        """Retrieve all active researcher URLs from the database."""
        query = "SELECT id, researcher_id, url, page_type FROM researcher_urls WHERE is_active = TRUE"
        return fetch_all(query)
