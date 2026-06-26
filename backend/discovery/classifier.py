"""Gemma-based classifier to pick the best personal website from search results."""
import logging

from pydantic import BaseModel

from backend.llm.client import extract_json

logger = logging.getLogger(__name__)


class WebsiteClassification(BaseModel):
    url: str | None
    confidence: float
    reasoning: str


_PROMPT_TEMPLATE = """You are classifying Google search results to find an economist's personal academic website.

Researcher: {name}
Affiliation: {affiliation}

Search results:
{results}

Pick the single best URL that is the researcher's PERSONAL academic website. Valid personal websites include:
- Sites on sites.google.com/site/ or sites.google.com/view/ (Google Sites)
- Custom domains like firstname-lastname.com or similar
- GitHub Pages (github.io)
- Weebly, WordPress.com, Squarespace, Wix personal sites

Do NOT pick:
- Google Scholar profiles (scholar.google.com)
- LinkedIn profiles
- ResearchGate profiles
- SSRN author pages
- University/institutional faculty directory pages (university.edu/faculty/...)
- RePEc/IDEAS pages
- NBER pages
- Wikipedia pages
- Twitter/X profiles

If none of the results is a personal website, set url to null.
Set confidence between 0 and 1: high (0.8+) if the URL clearly belongs to this specific researcher, low (<0.5) if uncertain about identity match."""


def classify_search_results(
    first_name: str,
    last_name: str,
    affiliation: str | None,
    search_results: list[dict],
) -> WebsiteClassification | None:
    """Use Gemma to pick the best personal website from search results.

    Returns None if LLM call fails entirely.
    """
    if not search_results:
        return WebsiteClassification(url=None, confidence=0.0, reasoning="No search results")

    results_text = "\n".join(
        f"{i+1}. {r['title']}\n   URL: {r['url']}\n   {r['snippet']}"
        for i, r in enumerate(search_results)
    )

    prompt = _PROMPT_TEMPLATE.format(
        name=f"{first_name} {last_name}",
        affiliation=affiliation or "Unknown",
        results=results_text,
    )

    response = extract_json(
        prompt,
        WebsiteClassification,
    )

    return response.parsed
