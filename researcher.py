from database import Database

class Researcher:
    def __init__(self, id, name, position, affiliation, urls):
        self.id = id
        self.name = name
        self.position = position
        self.affiliation = affiliation
        self.urls = urls

    @staticmethod
    def get_all_researchers():
        """Retrieve all researchers from the database."""
        query = """
            SELECT r.id, r.last_name, r.first_name, r.position, r.affiliation, ru.id, ru.url
            FROM researchers r
            LEFT JOIN researcher_urls ru ON r.id = ru.researcher_id
        """
        results = Database.fetch_all(query)
        researchers = {}
        for row in results:
            researcher_id, last_name, first_name, position, affiliation, url_id, url = row
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
    def get_all_researcher_urls():
        """Retrieve all researcher URLs from the database."""
        query = """
            SELECT id, researcher_id, url
            FROM researcher_urls
        """
        return Database.fetch_all(query)

    @staticmethod
    def add_researcher(last_name, first_name, position, affiliation):
        """Add a new researcher to the database."""
        query = """
            INSERT INTO researchers (last_name, first_name, position, affiliation)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """
        return Database.execute_query(query, (last_name, first_name, position, affiliation))

    @staticmethod
    def add_url_to_researcher(researcher_id, url):
        """Associate a URL with a researcher in the database."""
        query = "INSERT INTO researcher_urls (researcher_id, url) VALUES (%s, %s) RETURNING id"
        return Database.execute_query(query, (researcher_id, url))