"""Test whether gpt-5.4-nano can handle the known-papers exclusion prompt.

Pulls real HTML content and papers from the DB, injects random fake papers
BETWEEN existing papers in the text, and checks if the model correctly
identifies only the new ones. Tests both nano and mini for comparison.
"""
import json
import os
import random
import re

from dotenv import load_dotenv
load_dotenv()

from database import Database
from html_fetcher import HTMLFetcher
from publication import Publication, PublicationExtractionList
from openai import OpenAI

MODELS = ["gpt-5.4-nano", "gpt-5.4-mini"]
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ── Fake paper generator ──

FAKE_TITLES = [
    "The Macroeconomic Effects of Universal Basic Income: Evidence from Finland",
    "Digital Currency Adoption and Monetary Policy Transmission in Emerging Markets",
    "Climate Migration and Housing Markets: A Spatial Equilibrium Approach",
    "AI Automation and Wage Inequality: New Evidence from European Labor Markets",
    "Supply Chain Fragmentation and Trade Policy Uncertainty After COVID-19",
]

FAKE_AUTHORS = [
    [["Maria", "Gonzalez"], ["Thomas", "Weber"]],
    [["Yuki", "Tanaka"], ["Priya", "Sharma"]],
    [["Erik", "Lindqvist"]],
    [["Fatima", "Al-Rashid"], ["James", "O'Brien"], ["Chen", "Wei"]],
    [["Isabella", "Romano"], ["David", "Nakamura"]],
]


def generate_fake_paper_entry(index):
    """Generate a single fake paper entry as plain text, mimicking how papers appear on researcher pages."""
    title = FAKE_TITLES[index]
    authors = FAKE_AUTHORS[index]
    year = str(random.choice([2024, 2025, 2026]))
    venue = random.choice(["American Economic Review", "Quarterly Journal of Economics",
                            "Review of Economic Studies", "Working Paper", None])
    author_str = ", ".join(f"{a[0]} {a[1]}" for a in authors)

    # Vary the formatting to mimic real pages
    style = random.choice(["quoted", "bold_sim", "plain"])
    if style == "quoted":
        line = f'"{title}" {author_str}, {year}.'
    elif style == "bold_sim":
        line = f'{title}\n{author_str} ({year})'
    else:
        line = f'{author_str}. {year}. {title}.'

    if venue and venue != "Working Paper":
        line += f" {venue}."
    elif venue == "Working Paper":
        line += " Working Paper."

    paper = {"title": title, "authors": authors, "year": year, "venue": venue}
    return line, paper


def inject_fakes_into_text(text, n=2):
    """Inject n fake papers at random positions BETWEEN existing lines in the text."""
    indices = random.sample(range(len(FAKE_TITLES)), min(n, len(FAKE_TITLES)))
    lines = text.split("\n")
    fake_papers = []

    for idx in indices:
        fake_line, paper = generate_fake_paper_entry(idx)
        # Insert at a random position in the middle portion of the text
        # Avoid very top (headers) and very bottom (footers)
        min_pos = max(1, len(lines) // 4)
        max_pos = min(len(lines) - 1, 3 * len(lines) // 4)
        if min_pos >= max_pos:
            insert_pos = len(lines) // 2
        else:
            insert_pos = random.randint(min_pos, max_pos)
        lines.insert(insert_pos, fake_line)
        fake_papers.append(paper)

    return "\n".join(lines), fake_papers


def build_exclusion_prompt(text_content, url, known_papers):
    """Build the modified prompt that includes known papers."""
    known_section = ""
    if known_papers:
        known_lines = []
        for p in known_papers:
            entry = f"- {p['title']}"
            if p.get('year'):
                entry += f" ({p['year']})"
            known_lines.append(entry)
        known_section = (
            "\n\nThe following publications are ALREADY KNOWN and stored in our database. "
            "Do NOT include these in your response unless their metadata "
            "(status, venue, year, or abstract) has clearly changed on the page:\n"
            + "\n".join(known_lines)
        )

    return f"""Extract NEW or CHANGED academic publications from the following researcher page content from {url}.

For each publication, extract:
- title: the full publication title
- authors: a list of [first_name, last_name] pairs. Use full first names when available (e.g., "John" not "J."). If only an initial appears, use it as given.
- year: publication year as a string, or null if unknown
- venue: journal or conference name, or null if unknown
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", or null if unknown
- draft_url: a URL to a PDF, SSRN, NBER, or working paper version, or null if not available
- abstract: the paper abstract, or null if not shown on the page

Return ONLY publications that are NOT in the known list below, or where metadata has visibly changed.
If there are no new or changed publications, return an empty list.
Do not fabricate publications.{known_section}

Content:
{text_content}"""


def build_original_prompt(text_content, url):
    """Build the original prompt (no exclusion list, no truncation)."""
    return f"""Extract all academic publications from the following researcher page content from {url}.

For each publication, extract:
- title: the full publication title
- authors: a list of [first_name, last_name] pairs. Use full first names when available (e.g., "John" not "J."). If only an initial appears, use it as given.
- year: publication year as a string, or null if unknown
- venue: journal or conference name, or null if unknown
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", or null if unknown
- draft_url: a URL to a PDF, SSRN, NBER, or working paper version, or null if not available
- abstract: the paper abstract, or null if not shown on the page

If no publications are found in the content, return an empty list. Do not fabricate publications.

Content:
{text_content}"""


def call_model(model, prompt):
    """Call a model and return (parsed_pubs, usage) or (None, None) on error."""
    try:
        resp = client.beta.chat.completions.parse(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            response_format=PublicationExtractionList,
        )
        result = resp.choices[0].message.parsed
        pubs = [p.model_dump() for p in result.publications] if result else []
        return pubs, resp.usage
    except Exception as e:
        print(f"  ERROR ({model}): {e}")
        return None, None


def evaluate(pubs, fake_papers, known_papers):
    """Evaluate returned pubs against expected fakes and known papers."""
    if pubs is None:
        return None
    fake_titles_lower = {fp["title"].lower().strip() for fp in fake_papers}
    known_titles_lower = {kp["title"].lower().strip() for kp in known_papers}
    returned_titles = {p["title"].lower().strip() for p in pubs}

    fake_found = fake_titles_lower & returned_titles
    known_leaked = known_titles_lower & returned_titles
    unknown = returned_titles - fake_titles_lower - known_titles_lower

    return {
        "fake_found": len(fake_found),
        "known_leaked": len(known_leaked),
        "unknown_returned": len(unknown),
        "fake_found_titles": fake_found,
        "known_leaked_titles": known_leaked,
        "unknown_titles": unknown,
    }


def run_test(url_id, url, researcher_id, num_fake=2):
    """Run a single test across all models."""
    text = HTMLFetcher.get_latest_text(url_id)
    if not text:
        print(f"  SKIP: No stored text for url_id={url_id}")
        return None

    # Get known papers for this URL
    known_rows = Database.fetch_all(
        """SELECT DISTINCT p.title, p.year, p.venue, p.status
           FROM papers p
           JOIN paper_urls pu ON pu.paper_id = p.id
           WHERE pu.url = %s""",
        (url,),
    )
    known_papers = [
        {"title": r[0], "year": r[1], "venue": r[2], "status": r[3]}
        for r in known_rows
    ]

    # Inject fake papers into the middle of the text
    augmented_text, fake_papers = inject_fakes_into_text(text, num_fake)

    print(f"\n{'='*70}")
    print(f"URL: {url}")
    print(f"Text length: {len(text)} chars -> {len(augmented_text)} chars (with fakes)")
    print(f"Known papers in DB: {len(known_papers)}")
    print(f"Injected fake papers: {len(fake_papers)}")
    for fp in fake_papers:
        print(f"  -> {fp['title']}")

    model_results = {}

    for model in MODELS:
        print(f"\n--- {model}: exclusion prompt ---")
        excl_prompt = build_exclusion_prompt(augmented_text, url, known_papers)
        excl_pubs, excl_usage = call_model(model, excl_prompt)
        if excl_pubs is not None:
            print(f"  Returned {len(excl_pubs)} publications")
            for p in excl_pubs:
                print(f"    - {p['title']}")
            print(f"  Tokens: prompt={excl_usage.prompt_tokens}, completion={excl_usage.completion_tokens}")
            excl_eval = evaluate(excl_pubs, fake_papers, known_papers)
            print(f"  Fakes found: {excl_eval['fake_found']}/{len(fake_papers)}, "
                  f"Known leaked: {excl_eval['known_leaked']}, "
                  f"Unknown: {excl_eval['unknown_returned']}")

        print(f"\n--- {model}: original prompt (baseline) ---")
        orig_prompt = build_original_prompt(augmented_text, url)
        orig_pubs, orig_usage = call_model(model, orig_prompt)
        if orig_pubs is not None:
            print(f"  Returned {len(orig_pubs)} publications")
            print(f"  Tokens: prompt={orig_usage.prompt_tokens}, completion={orig_usage.completion_tokens}")

        model_results[model] = {
            "excl_pubs": excl_pubs,
            "excl_usage": excl_usage,
            "excl_eval": excl_eval if excl_pubs is not None else None,
            "orig_pubs": orig_pubs,
            "orig_usage": orig_usage,
        }

    return {
        "url": url,
        "known_count": len(known_papers),
        "fake_count": len(fake_papers),
        "text_len": len(augmented_text),
        "models": model_results,
    }


def main():
    # Get URLs that have stored text — prefer ones with known papers
    candidates = Database.fetch_all(
        """SELECT ru.id, ru.researcher_id, ru.url, ru.page_type,
                  (SELECT COUNT(*) FROM paper_urls pu
                   JOIN papers p ON p.id = pu.paper_id
                   WHERE pu.url = ru.url) AS paper_count
           FROM researcher_urls ru
           JOIN html_content hc ON hc.url_id = ru.id
           WHERE hc.content IS NOT NULL
           ORDER BY paper_count DESC
           LIMIT 5"""
    )

    if not candidates:
        print("No URLs with stored content found in DB")
        return

    print(f"Testing {len(candidates)} URLs across models: {', '.join(MODELS)}")
    all_results = []
    for url_id, researcher_id, url, page_type, paper_count in candidates:
        r = run_test(url_id, url, researcher_id, num_fake=2)
        if r:
            all_results.append(r)

    # Summary per model
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")

    for model in MODELS:
        print(f"\n  Model: {model}")
        total_fake = 0
        total_found = 0
        total_leaked = 0
        total_unknown = 0
        total_excl_prompt_tok = 0
        total_excl_compl_tok = 0
        total_orig_prompt_tok = 0
        total_orig_compl_tok = 0

        for r in all_results:
            mr = r["models"].get(model)
            if not mr or not mr["excl_eval"]:
                continue
            total_fake += r["fake_count"]
            total_found += mr["excl_eval"]["fake_found"]
            total_leaked += mr["excl_eval"]["known_leaked"]
            total_unknown += mr["excl_eval"]["unknown_returned"]
            if mr["excl_usage"]:
                total_excl_prompt_tok += mr["excl_usage"].prompt_tokens
                total_excl_compl_tok += mr["excl_usage"].completion_tokens
            if mr["orig_usage"]:
                total_orig_prompt_tok += mr["orig_usage"].prompt_tokens
                total_orig_compl_tok += mr["orig_usage"].completion_tokens

        recall = (total_found / total_fake * 100) if total_fake else 0
        print(f"    Fake papers detected:   {total_found}/{total_fake} ({recall:.0f}% recall)")
        print(f"    Known papers leaked:    {total_leaked}")
        print(f"    Unknown papers returned:{total_unknown}")
        print(f"    Exclusion tokens:  {total_excl_prompt_tok:,} prompt + {total_excl_compl_tok:,} completion")
        print(f"    Original tokens:   {total_orig_prompt_tok:,} prompt + {total_orig_compl_tok:,} completion")
        if total_orig_compl_tok > 0:
            savings = (1 - total_excl_compl_tok / total_orig_compl_tok) * 100
            print(f"    Completion savings: {savings:.0f}%")


if __name__ == "__main__":
    main()
