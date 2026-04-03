# Model Cost Optimization Eval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up a promptfoo-based evaluation framework to test 12 LLM models (via OpenRouter) across all 4 LLM tasks, mapping cost vs quality.

**Architecture:** A standalone `eval/` directory with prompt templates, a Python export script for test cases, and a promptfoo YAML config. No production code changes. All models accessed via OpenRouter. Claude Sonnet 4.6 generates ground truth; GPT-5.4-mini judges candidates.

**Tech Stack:** promptfoo (via npx), Python (mysql-connector-python, json), OpenRouter API

**Spec:** `docs/superpowers/specs/2026-04-03-model-cost-optimization-design.md`

---

### Task 1: Create eval directory structure and gitignore entries

**Files:**
- Create: `eval/prompts/` (directory)
- Create: `eval/test_cases/.gitkeep`
- Create: `eval/output/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Create the eval directory tree**

```bash
mkdir -p eval/prompts eval/test_cases eval/output
touch eval/test_cases/.gitkeep eval/output/.gitkeep
```

- [ ] **Step 2: Add gitignore entries for test data and output**

Add these lines to the end of `.gitignore`:

```
# Eval framework
eval/test_cases/*.json
eval/output/
```

- [ ] **Step 3: Commit**

```bash
git add eval/ .gitignore
git commit -m "scaffold eval directory structure for model cost optimization"
```

---

### Task 2: Create prompt templates

Extract prompts verbatim from the codebase, replacing Python f-string variables with promptfoo `{{variable}}` syntax.

**Files:**
- Create: `eval/prompts/publication_extraction.txt`
- Create: `eval/prompts/description_extraction.txt`
- Create: `eval/prompts/researcher_disambiguation.txt`
- Create: `eval/prompts/jel_classification.txt`

- [ ] **Step 1: Create publication extraction prompt**

Create `eval/prompts/publication_extraction.txt`:

```
Extract all academic publications from the following researcher page content from {{url}}.

For each publication, extract:
- title: the full publication title
- authors: a list of [first_name, last_name] pairs. Use full first names when available (e.g., "John" not "J."). If only an initial appears, use it as given.
- year: publication year as a string, or null if unknown
- venue: journal or conference name, or null if unknown
- status: one of "published", "accepted", "revise_and_resubmit", "reject_and_resubmit", "working_paper", or null if unknown
- draft_url: a URL to a PDF, SSRN, NBER, or working paper version, or null if not available
- abstract: the paper abstract, or null if not shown on the page

Return your response as a JSON object with a single key "publications" containing a list of objects. Each object must have these keys: title, authors, year, venue, status, draft_url, abstract.

If no publications are found in the content, return: {"publications": []}

Do not fabricate publications.

Content:
{{text_content}}
```

Note: The prompt adds explicit JSON format instructions since we're not using structured output mode. The original codebase relies on OpenAI's `response_format=PublicationExtractionList` to enforce the schema — here we need the model to produce valid JSON on its own.

- [ ] **Step 2: Create description extraction prompt**

Create `eval/prompts/description_extraction.txt`:

```
From the following text from a researcher's homepage at {{url}}, extract a professional description (up to 200 words) describing who this person is, their research interests, and their current position/affiliation. Return only the description text, nothing else. If no clear description can be extracted, reply with exactly: null

Content:
{{text_content}}
```

- [ ] **Step 3: Create researcher disambiguation prompt**

Create `eval/prompts/researcher_disambiguation.txt`:

```
You are disambiguating researcher names. A publication lists the author as: "{{first_name}} {{last_name}}"

The database contains these existing researchers with the same last name:
{{candidates_text}}

Is the author the same person as any of these researchers? Consider:
- "J. Smith" and "John Smith" are likely the same person
- An abbreviated first name may match a full first name
- Only match if you are confident

Respond with JSON only: {"match_id": <id or null>}
```

- [ ] **Step 4: Create JEL classification prompt**

Create `eval/prompts/jel_classification.txt`:

```
Classify the following economics researcher into JEL (Journal of Economic Literature) codes based on their bio/description.

Researcher: {{first_name}} {{last_name}}

Bio/Description:
{{description}}

Assign one or more top-level JEL codes from this list:

A - General Economics and Teaching
B - History of Economic Thought, Methodology, and Heterodox Approaches
C - Mathematical and Quantitative Methods
D - Microeconomics
E - Macroeconomics and Monetary Economics
F - International Economics
G - Financial Economics
H - Public Economics
I - Health, Education, and Welfare
J - Labor and Demographic Economics
K - Law and Economics
L - Industrial Organization
M - Business Administration and Business Economics; Marketing; Accounting; Personnel Economics
N - Economic History
O - Economic Development, Innovation, Technological Change, and Growth
P - Economic Systems
Q - Agricultural and Natural Resource Economics; Environmental and Ecological Economics
R - Urban, Rural, Regional, Real Estate, and Transportation Economics
Z - Other Special Topics

Return your response as a JSON object with a single key "jel_codes" containing a list of objects, each with "code" (the letter) and "reasoning" (brief explanation).

Rules:
- Assign between 1 and 5 codes that best represent the researcher's primary fields.
- Only assign codes where the bio provides clear evidence.
- Provide brief reasoning for each code.
- If the bio is too vague to classify, return: {"jel_codes": []}
- Do NOT assign "Y - Miscellaneous Categories" unless truly nothing else fits.
```

Note: Like publication extraction, the JEL prompt adds explicit JSON format instructions since the original relies on `response_format=JelClassificationResult`.

- [ ] **Step 5: Commit**

```bash
git add eval/prompts/
git commit -m "add prompt templates for all 4 LLM eval tasks"
```

---

### Task 3: Create the test case export script

**Files:**
- Create: `eval/export_test_cases.py`

This script connects to MySQL using the project's existing `db_config`, samples data, and writes JSON files for promptfoo.

- [ ] **Step 1: Create export_test_cases.py**

Create `eval/export_test_cases.py`:

```python
"""Export test cases from the database for promptfoo evaluation.

Usage: poetry run python eval/export_test_cases.py

Requires: DB_HOST, DB_USER, DB_PASSWORD, DB_NAME env vars (reads from .env).
"""
import json
import os
import sys

# Add project root to path so we can import db_config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_config import db_config
import mysql.connector

CONTENT_MAX_CHARS = int(os.environ.get('CONTENT_MAX_CHARS', '20000'))
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_cases')


def get_connection():
    return mysql.connector.connect(**db_config)


def export_publication_extraction():
    """Sample pages from html_content that have associated papers."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            # Get URLs that have both html_content and papers — confirmed real publications
            cur.execute("""
                SELECT DISTINCT hc.url_id, hc.text_content, u.url
                FROM html_content hc
                JOIN urls u ON u.id = hc.url_id
                JOIN papers p ON p.url_id = hc.url_id
                WHERE hc.text_content IS NOT NULL
                  AND LENGTH(hc.text_content) > 100
                ORDER BY RAND()
                LIMIT 50
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    test_cases = []
    for row in rows:
        text = row['text_content']
        if len(text) > CONTENT_MAX_CHARS:
            text = text[:CONTENT_MAX_CHARS]
        test_cases.append({
            'vars': {
                'text_content': text,
                'url': row['url'],
            },
            'metadata': {
                'url_id': row['url_id'],
            },
        })

    path = os.path.join(OUTPUT_DIR, 'publication_extraction.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} publication extraction test cases to {path}")
    return test_cases


def export_description_extraction():
    """Same pages as publication extraction — researchers' homepages."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT DISTINCT hc.url_id, hc.text_content, u.url
                FROM html_content hc
                JOIN urls u ON u.id = hc.url_id
                WHERE hc.text_content IS NOT NULL
                  AND LENGTH(hc.text_content) > 100
                ORDER BY RAND()
                LIMIT 50
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    test_cases = []
    for row in rows:
        text = row['text_content']
        if len(text) > CONTENT_MAX_CHARS:
            text = text[:CONTENT_MAX_CHARS]
        test_cases.append({
            'vars': {
                'text_content': text,
                'url': row['url'],
            },
            'metadata': {
                'url_id': row['url_id'],
            },
        })

    path = os.path.join(OUTPUT_DIR, 'description_extraction.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} description extraction test cases to {path}")
    return test_cases


def export_jel_classification():
    """Researchers who have descriptions in the DB."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT id, first_name, last_name, description
                FROM researchers
                WHERE description IS NOT NULL
                  AND LENGTH(description) > 20
                ORDER BY RAND()
                LIMIT 50
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    test_cases = []
    for row in rows:
        test_cases.append({
            'vars': {
                'first_name': row['first_name'],
                'last_name': row['last_name'],
                'description': row['description'],
            },
            'metadata': {
                'researcher_id': row['id'],
            },
        })

    path = os.path.join(OUTPUT_DIR, 'jel_classification.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} JEL classification test cases to {path}")
    return test_cases


def export_researcher_disambiguation():
    """Find real same-last-name researcher pairs for disambiguation testing."""
    conn = get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            # Find last names shared by multiple researchers
            cur.execute("""
                SELECT last_name
                FROM researchers
                GROUP BY last_name
                HAVING COUNT(*) >= 2
                ORDER BY RAND()
                LIMIT 30
            """)
            shared_last_names = [row['last_name'] for row in cur.fetchall()]

            test_cases = []
            for last_name in shared_last_names:
                cur.execute("""
                    SELECT id, first_name, last_name
                    FROM researchers
                    WHERE last_name = %s
                """, (last_name,))
                candidates = cur.fetchall()
                if len(candidates) < 2:
                    continue

                # Use the first researcher as the "query" author
                query = candidates[0]
                # All researchers with the same last name are candidates
                candidates_text = "\n".join(
                    f"- ID {c['id']}: {c['first_name']} {c['last_name']}"
                    for c in candidates
                )
                test_cases.append({
                    'vars': {
                        'first_name': query['first_name'],
                        'last_name': query['last_name'],
                        'candidates_text': candidates_text,
                    },
                    'metadata': {
                        'query_researcher_id': query['id'],
                        'candidate_ids': [c['id'] for c in candidates],
                    },
                })
    finally:
        conn.close()

    path = os.path.join(OUTPUT_DIR, 'researcher_disambiguation.json')
    with open(path, 'w') as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(test_cases)} researcher disambiguation test cases to {path}")
    return test_cases


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("Exporting test cases from database...\n")

    pub = export_publication_extraction()
    desc = export_description_extraction()
    jel = export_jel_classification()
    disambig = export_researcher_disambiguation()

    print(f"\nDone. Total test cases: {len(pub) + len(desc) + len(jel) + len(disambig)}")
```

- [ ] **Step 2: Verify the script runs without errors**

```bash
poetry run python eval/export_test_cases.py
```

Expected: Prints export counts for each task type and creates JSON files in `eval/test_cases/`. If the database is not accessible, you'll see a connection error — that's expected if running without the local DB.

- [ ] **Step 3: Spot-check one exported file**

```bash
poetry run python -c "import json; data = json.load(open('eval/test_cases/publication_extraction.json')); print(f'{len(data)} cases'); print(json.dumps(data[0]['vars'].keys().__str__()))"
```

Expected: Shows the count and confirms each test case has `text_content` and `url` keys.

- [ ] **Step 4: Commit**

```bash
git add eval/export_test_cases.py
git commit -m "add test case export script for model eval"
```

---

### Task 4: Create the promptfoo configuration

**Files:**
- Create: `eval/promptfooconfig.yaml`

This is the main promptfoo config defining providers, prompts, test suites, and assertions.

- [ ] **Step 1: Create promptfooconfig.yaml**

Create `eval/promptfooconfig.yaml`:

```yaml
# Model Cost Optimization Eval
# Usage:
#   1. Export test cases:  poetry run python eval/export_test_cases.py
#   2. Run eval:           npx promptfoo@latest eval
#   3. View results:       npx promptfoo@latest view

description: "LLM Model Cost Optimization — econ-newsfeed"

# --- Providers ---
# All models via OpenRouter using OPENROUTER_API_KEY env var.
# Ground truth: Claude Sonnet 4.6
# Judge: GPT-5.4-mini (configured in defaultTest assertions)

providers:
  # Ground truth
  - id: openrouter:anthropic/claude-sonnet-4.6
    label: "Sonnet 4.6 (ground truth)"

  # Budget tier
  - id: openrouter:anthropic/claude-haiku-4.5
    label: "Haiku 4.5"
  - id: openrouter:openai/gpt-5.4-nano
    label: "GPT-5.4 Nano"
  - id: openrouter:google/gemini-3-flash-preview
    label: "Gemini 3 Flash"
  - id: openrouter:google/gemini-3.1-flash-lite-preview
    label: "Gemini 3.1 Flash Lite"

  # Ultra-cheap tier
  - id: openrouter:deepseek/deepseek-chat
    label: "DeepSeek V3.2"
  - id: openrouter:minimax/minimax-m2.7
    label: "MiniMax M2.7"
  - id: openrouter:xiaomi/mimo-v2-pro
    label: "MiMo-V2-Pro"
  - id: openrouter:qwen/qwen3.5-flash
    label: "Qwen 3.5 Flash"
  - id: openrouter:xiaomi/mimo-v2-flash
    label: "MiMo-V2-Flash"

  # Free tier
  - id: openrouter:stepfun/step-3.5-flash
    label: "Step 3.5 Flash (free)"
  - id: openrouter:qwen/qwen3.6-plus
    label: "Qwen 3.6 Plus (free)"

# --- Prompts ---
prompts:
  - id: file://prompts/publication_extraction.txt
    label: publication_extraction
  - id: file://prompts/description_extraction.txt
    label: description_extraction
  - id: file://prompts/researcher_disambiguation.txt
    label: researcher_disambiguation
  - id: file://prompts/jel_classification.txt
    label: jel_classification

# --- Test Suites ---
tests:
  # === Publication Extraction ===
  - description: "Publication Extraction"
    vars: file://test_cases/publication_extraction.json
    options:
      prompt: publication_extraction
    assert:
      - type: llm-rubric
        value: |
          You are evaluating an LLM's ability to extract academic publications from a researcher's webpage.

          The model was given webpage text and asked to extract publications as JSON with keys: title, authors, year, venue, status, draft_url, abstract.

          Evaluate the output on these criteria:

          1. FORMAT VALIDITY: Is the output valid JSON with a "publications" key containing a list of objects? Each object should have the required keys. Score 0 if not valid JSON, 1 if valid.

          2. COMPLETENESS (0-1): Compare the number of publications found to what a thorough extraction would yield from this content. Did it find most papers? Did it populate fields (year, venue, status) when the information was available?

          3. ACCURACY (0-1): Are the extracted values correct? Check titles for truncation or errors, author names for correctness, years and venues for accuracy, statuses for appropriate categorization.

          Return a score from 0 to 1 where:
          - 0.0 = completely wrong or invalid output
          - 0.5 = partially correct (some papers found, some errors)
          - 1.0 = excellent extraction matching ground truth quality
        provider: openrouter:openai/gpt-5.4-mini

  # === Description Extraction ===
  - description: "Description Extraction"
    vars: file://test_cases/description_extraction.json
    options:
      prompt: description_extraction
    assert:
      - type: llm-rubric
        value: |
          You are evaluating an LLM's ability to extract a professional description of a researcher from their homepage text.

          The model was asked to produce a ~200 word description covering: who the person is, their research interests, and their position/affiliation. If no description was possible, it should return "null".

          Evaluate on:
          1. RELEVANCE (0-1): Does the description accurately capture the researcher's role, affiliation, and research interests from the source text?
          2. CONCISENESS (0-1): Is it roughly 200 words or fewer? Does it avoid filler and irrelevant content?
          3. CORRECTNESS: Does it avoid hallucinating information not in the source text?

          Return a score from 0 to 1.
        provider: openrouter:openai/gpt-5.4-mini

  # === Researcher Disambiguation ===
  - description: "Researcher Disambiguation"
    vars: file://test_cases/researcher_disambiguation.json
    options:
      prompt: researcher_disambiguation
    assert:
      - type: is-json
      - type: llm-rubric
        value: |
          You are evaluating an LLM's ability to disambiguate researcher names.

          The model was given an author name and a list of candidate researchers with the same last name. It should return JSON: {"match_id": <id or null>}.

          Evaluate on:
          1. FORMAT: Is the output valid JSON with a "match_id" key? (0 or 1)
          2. CORRECTNESS: Does the match_id make sense given the names? "J. Smith" should match "John Smith". Completely different first names should return null. (0 or 1)

          Return a score from 0 to 1.
        provider: openrouter:openai/gpt-5.4-mini

  # === JEL Classification ===
  - description: "JEL Classification"
    vars: file://test_cases/jel_classification.json
    options:
      prompt: jel_classification
    assert:
      - type: llm-rubric
        value: |
          You are evaluating an LLM's ability to classify an economics researcher into JEL codes.

          The model was given a researcher's bio and asked to return JSON: {"jel_codes": [{"code": "X", "reasoning": "..."}]}.

          Evaluate on:
          1. FORMAT VALIDITY: Is the output valid JSON with a "jel_codes" key? Are codes single uppercase letters (A-Z)? (0 or 1)
          2. CODE RELEVANCE (0-1): Do the assigned JEL codes make sense given the researcher's described research areas? Are obviously relevant codes included? Are irrelevant codes excluded?
          3. REASONING QUALITY (0-1): Does the reasoning clearly connect the bio content to each assigned code?

          Return a score from 0 to 1.
        provider: openrouter:openai/gpt-5.4-mini

# Output directory for results
outputPath: eval/output
```

- [ ] **Step 2: Verify the config is valid YAML**

```bash
npx promptfoo@latest validate --config eval/promptfooconfig.yaml
```

If `validate` is not a command, just check YAML parsing:

```bash
python3 -c "import yaml; yaml.safe_load(open('eval/promptfooconfig.yaml')); print('Valid YAML')"
```

- [ ] **Step 3: Commit**

```bash
git add eval/promptfooconfig.yaml
git commit -m "add promptfoo config with 12 models and 4 eval tasks"
```

---

### Task 5: Run a dry-run eval to verify the setup

**Files:** None (validation only)

- [ ] **Step 1: Ensure the OPENROUTER_API_KEY is set**

promptfoo reads `OPENROUTER_API_KEY` from the environment. Verify it's available:

```bash
grep -c 'OPEN_ROUTER_API_KEY' .env
```

Note: The project's `.env` uses `OPEN_ROUTER_API_KEY` but promptfoo expects `OPENROUTER_API_KEY`. Either:
- Add `OPENROUTER_API_KEY` to `.env` (copy the value from `OPEN_ROUTER_API_KEY`), or
- Export it before running: `export OPENROUTER_API_KEY=$(grep OPEN_ROUTER_API_KEY .env | cut -d= -f2)`

- [ ] **Step 2: Run a small test with one model and one test case**

To avoid burning through credits on a dry run, test with a single provider and a subset:

```bash
cd eval && npx promptfoo@latest eval \
  --config promptfooconfig.yaml \
  --providers "openrouter:qwen/qwen3.6-plus" \
  --prompts "file://prompts/description_extraction.txt" \
  --tests-file "test_cases/description_extraction.json" \
  --max-concurrency 1 \
  --limit 2
```

Expected: promptfoo runs 2 test cases against the free Qwen model, shows results in the terminal.

- [ ] **Step 3: Open the UI to verify results display**

```bash
npx promptfoo@latest view
```

Expected: Browser opens with the promptfoo comparison UI showing the 2 test results.

- [ ] **Step 4: Run the full eval**

Once the dry run works, run the complete eval:

```bash
cd eval && OPENROUTER_API_KEY=$(grep OPEN_ROUTER_API_KEY ../.env | cut -d= -f2) \
  npx promptfoo@latest eval --config promptfooconfig.yaml
```

This will take a while (~2400 model calls + ~1760 judge calls). Monitor progress in the terminal.

- [ ] **Step 5: View results and analyze the cost-quality frontier**

```bash
npx promptfoo@latest view
```

Browse the comparison UI. For each task, identify:
- Which models produce acceptable quality (score > 0.7)?
- Among those, which is cheapest?
- Is there a clear "knee" in the cost-quality curve?

---

### Task 6: Add a Makefile target for convenience

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add eval targets to the Makefile**

Add at the end of the Makefile:

```makefile
# Eval
eval-export:
	poetry run python eval/export_test_cases.py

eval-run:
	cd eval && OPENROUTER_API_KEY=$$(grep OPEN_ROUTER_API_KEY ../.env | cut -d= -f2) npx promptfoo@latest eval --config promptfooconfig.yaml

eval-view:
	cd eval && npx promptfoo@latest view

eval: eval-export eval-run eval-view
```

- [ ] **Step 2: Commit**

```bash
git add Makefile
git commit -m "add make targets for model cost eval (eval-export, eval-run, eval-view)"
```
