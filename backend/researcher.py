from backend.database import fetch_all
from backend.database.optouts import url_not_opted_out_sql
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

        URLs of researchers who opted out are never returned, whichever way
        they became eligible.
        """
        query = (
            "SELECT ru.id, ru.researcher_id, ru.url, ru.page_type FROM researcher_urls ru "
            "WHERE (ru.is_active = TRUE "
            f"OR (ru.deactivation_reason IN ({RECHECK_REASONS_SQL}) "
            "AND ru.deactivated_at < NOW() - INTERVAL %s DAY)) "
            "AND " + url_not_opted_out_sql("ru.url")
        )
        return fetch_all(query, (TOO_LARGE_RECHECK_DAYS,))
