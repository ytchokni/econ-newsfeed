"""Shared helpers for search query construction."""
from __future__ import annotations

import os

def escape_like(value: str) -> str:
    """Escape LIKE-special characters so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


FT_MIN_TOKEN_SIZE = int(os.environ.get("FT_MIN_TOKEN_SIZE", "3"))

_FT_BOOLEAN_OPERATORS = str.maketrans("", "", '+-~<>()@*"')


def escape_fulltext(value: str) -> str:
    """Strip BOOLEAN MODE operators so user input is matched literally."""
    return value.translate(_FT_BOOLEAN_OPERATORS)


TOP20_DEPT_KEYWORDS = [
    "MIT", "Massachusetts Institute of Technology",
    "Harvard", "Princeton", "Stanford",
    "University of Chicago",
    "UC Berkeley", "University of California, Berkeley",
    "Columbia", "Yale", "Northwestern",
    "University of Pennsylvania",
    "New York University", "NYU",
    "Duke",
    "University of Michigan",
    "University of Minnesota",
    "Cornell",
    "UCLA", "University of California, Los Angeles",
    "UC San Diego", "University of California, San Diego",
    "University of Wisconsin",
    "Boston University",
    "Carnegie Mellon",
]

TOP5_JOURNAL_KEYWORDS = [
    "American Economic Review",
    "AER",
    "Econometrica",
    "Journal of Political Economy",
    "JPE",
    "Quarterly Journal of Economics",
    "QJE",
    "Review of Economic Studies",
    "REStud",
]

TOP5_JOURNAL_EXCLUSIONS = [
    "European Journal of Political Economy",
]


def top5_venue_clause(
    col: str = "p.venue",
) -> tuple[str, list[str]]:
    """Return (sql_fragment, params) for a top-5 venue filter with exclusions."""
    likes = " OR ".join([f"{col} LIKE %s"] * len(TOP5_JOURNAL_KEYWORDS))
    not_likes = " AND ".join([f"{col} NOT LIKE %s"] * len(TOP5_JOURNAL_EXCLUSIONS))
    clause = f"(({likes}) AND {not_likes})"
    params: list[str] = [f"%{escape_like(kw)}%" for kw in TOP5_JOURNAL_KEYWORDS]
    params.extend(f"%{escape_like(kw)}%" for kw in TOP5_JOURNAL_EXCLUSIONS)
    return clause, params

TOP100_REPEC_KEYWORDS = [
    # Top 5
    "Econometrica",
    "Quarterly Journal of Economics",
    "American Economic Review",
    "Journal of Political Economy",
    "Review of Economic Studies",
    # 4-10
    "Journal of Financial Economics",
    "Journal of Economic Literature",
    "Brookings Papers on Economic Activity",
    "Journal of Economic Perspectives",
    "Journal of Finance",
    # 11-20 (American Economic Journal covers all 4 AEJ sub-journals + AER: Insights)
    "American Economic Journal",
    "Journal of Economic Growth",
    "Journal of Econometrics",
    "Annual Review of Economics",
    "Journal of Monetary Economics",
    "Journal of Labor Economics",
    "Review of Economics and Statistics",
    "Journal of the European Economic Association",
    # 21-30
    "The Economic Journal",
    "RAND Journal of Economics",
    "Journal of International Economics",
    "Journal of Accounting and Economics",
    "Journal of Public Economics",
    "Strategic Management Journal",
    "Journal of Business & Economic Statistics",
    "Journal of Applied Econometrics",
    # 31-40
    "Journal of Development Economics",
    "Journal of Economic Theory",
    "Journal of Money, Credit and Banking",
    "Journal of Financial Intermediation",
    "Review of Economic Dynamics",
    "European Economic Review",
    "International Economic Review",
    "Journal of Business Venturing",
    "Experimental Economics",
    "Research Policy",
    # 41-50
    "Journal of Human Resources",
    "Journal of Banking & Finance",
    "Journal of International Business Studies",
    "Management Science",
    "World Bank Economic Review",
    "Organization Science",
    "Journal of Accounting Research",
    "Journal of Law and Economics",
    "Journal of Economic Dynamics and Control",
    # 51-60
    "Annual Review of Financial Economics",
    "International Journal of Central Banking",
    "Journal of Environmental Economics and Management",
    "Journal of Financial and Quantitative Analysis",
    "Energy Economics",
    "Oxford Bulletin of Economics and Statistics",
    "Journal of Urban Economics",
    "IMF Economic Review",
    "Review of Finance",
    "Journal of Economic Surveys",
    # 61-70
    "Journal of Risk and Uncertainty",
    "World Bank Research Observer",
    "Journal of Law, Economics, and Organization",
    "Journal of Health Economics",
    "Econometrics Journal",
    "Journal of International Money and Finance",
    "Journal of Corporate Finance",
    "Journal of Consumer Research",
    "Quantitative Economics",
    "Review of Environmental Economics and Policy",
    # 71-80
    "Labour Economics",
    "Econometric Theory",
    "World Development",
    "Journal of Financial Markets",
    "Games and Economic Behavior",
    "Energy Policy",
    # 81-90
    "Marketing Science",
    "Economics Letters",
    "Journal of Economic Behavior & Organization",
    "Journal of Economic Geography",
    "Journal of Industrial Economics",
    "Mathematical Finance",
    "Journal of Empirical Finance",
    "Ecological Economics",
    # 91-100
    "Journal of Population Economics",
    "Scandinavian Journal of Economics",
    "Entrepreneurship Theory and Practice",
    "European Journal of Operational Research",
    "Journal of Economics & Management Strategy",
    "Journal of the Association of Environmental and Resource Economists",
    # Common abbreviations
    "QJE", "AER", "JPE", "REStud", "JFE", "RFS",
    "REStat", "JEEA", "AEJ",
]
