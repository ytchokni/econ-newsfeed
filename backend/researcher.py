from backend.database import fetch_all
from backend.database.researchers import RECHECK_REASONS_SQL, TOO_LARGE_RECHECK_DAYS


class Researcher:
    @staticmethod
    def get_all_researcher_urls() -> list[dict]:
        """Retrieve researcher URLs the scrape job should fetch.

        Active URLs, plus URLs deactivated as ``response_too_large`` or
        ``login_required`` at least TOO_LARGE_RECHECK_DAYS ago. Both are a
        property of the site's current build, not of the researcher: a site
        moved off a heavy host (e.g. Google Sites) shrinks, a private one can
        be published again, and without a recheck it would stay off forever.
        A successful fetch reactivates it; a failing one resets the clock.
        """
        query = (
            "SELECT id, researcher_id, url, page_type FROM researcher_urls "
            "WHERE is_active = TRUE "
            f"OR (deactivation_reason IN ({RECHECK_REASONS_SQL}) "
            "AND deactivated_at < NOW() - INTERVAL %s DAY)"
        )
        return fetch_all(query, (TOO_LARGE_RECHECK_DAYS,))
