from backend.database import fetch_all
from backend.database.researchers import TOO_LARGE_RECHECK_DAYS


class Researcher:
    @staticmethod
    def get_all_researcher_urls() -> list[dict]:
        """Retrieve researcher URLs the scrape job should fetch.

        Active URLs, plus URLs deactivated as ``response_too_large`` at least
        TOO_LARGE_RECHECK_DAYS ago. Page size is a property of the site's
        current build, not of the researcher: a site moved off a heavy host
        (e.g. Google Sites) shrinks, and without a recheck it would stay off
        forever. A successful fetch reactivates it; a still-too-large one
        resets the deactivation clock.
        """
        query = (
            "SELECT id, researcher_id, url, page_type FROM researcher_urls "
            "WHERE is_active = TRUE "
            "OR (deactivation_reason = 'response_too_large' "
            "AND deactivated_at < NOW() - INTERVAL %s DAY)"
        )
        return fetch_all(query, (TOO_LARGE_RECHECK_DAYS,))
