from database import Database

class Researcher:
    @staticmethod
    def add_researcher(last_name, first_name, position, affiliation):
        """
        Add a new researcher to the database.
        Returns the ID of the newly inserted researcher.
        """
        query = """
            INSERT INTO researchers (last_name, first_name, position, affiliation)
            VALUES (%s, %s, %s, %s)
        """
        return Database.execute_query(query, (last_name, first_name, position, affiliation))

    @staticmethod
    def add_url_to_researcher(researcher_id, url):
        """
        Associate a URL with a researcher in the database.
        """
        query = "INSERT INTO researcher_urls (researcher_id, url) VALUES (%s, %s)"
        return Database.execute_query(query, (researcher_id, url))

    @staticmethod
    def get_all_urls():
        """
        Retrieve all unique URLs associated with researchers from the database.
        Returns a list of URLs.
        """
        query = "SELECT DISTINCT url FROM researcher_urls"
        return [url[0] for url in Database.fetch_all(query)]