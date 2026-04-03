# Model Cost Optimization Eval — Design Spec

**Date:** 2026-04-03
**Goal:** Map the cost-quality frontier across LLM providers for all 4 LLM tasks in econ-newsfeed, using promptfoo with OpenRouter.

## Context

The project uses OpenAI (currently `gpt-5.4-mini`) for 4 LLM tasks:

1. **Publication extraction** — structured output (Pydantic schema) extracting papers from researcher homepages
2. **Description extraction** — freeform text summarizing a researcher's bio (~200 words)
3. **Researcher disambiguation** — JSON output matching author names to existing DB records
4. **JEL classification** — structured output classifying researchers into JEL codes

All tasks currently use a single `OPENAI_MODEL` env var. An `OPEN_ROUTER_API_KEY` is available in `.env` but not yet integrated into the codebase.

## Approach

**Pure promptfoo** — define all 4 tasks as promptfoo test suites. A small Python script exports test cases from the MySQL database. Promptfoo handles model invocation, scoring, and comparison UI. No modifications to production code.

## Project Structure

```
eval/
├── promptfooconfig.yaml       # Main config: providers, test suites
├── export_test_cases.py       # Python script to sample from html_content → JSON
├── test_cases/                # Exported test data (gitignored)
│   ├── publication_extraction.json
│   ├── description_extraction.json
│   ├── researcher_disambiguation.json
│   └── jel_classification.json
├── prompts/
│   ├── publication_extraction.txt
│   ├── description_extraction.txt
│   ├── researcher_disambiguation.txt
│   └── jel_classification.txt
└── output/                    # promptfoo results (gitignored)
```

- `test_cases/` and `output/` are gitignored (contain DB data and results)
- Prompts, config, and export script are committed

## Test Case Export

`export_test_cases.py` connects to MySQL and samples existing data:

- **Publication extraction:** ~30-50 pages from `html_content` that have associated `papers` (confirmed to contain real publications). Each test case: `{text_content, url}`.
- **Description extraction:** Same HTML pages paired with URLs. Each test case: `{text_content, url}`.
- **JEL classification:** Researchers with descriptions in DB. Each test case: `{first_name, last_name, description}`.
- **Researcher disambiguation:** Real same-last-name scenarios from DB. Each test case: `{first_name, last_name, candidates_text}`.

Run once manually before evals: `poetry run python eval/export_test_cases.py`

## Models

All accessed via OpenRouter using `OPEN_ROUTER_API_KEY`.

| Tier | Model | OpenRouter ID | $/1M in | $/1M out |
|------|-------|---------------|---------|----------|
| **Ground truth** | Claude Sonnet 4.6 | `anthropic/claude-sonnet-4.6` | $3 | $15 |
| **Budget** | Claude Haiku 4.5 | `anthropic/claude-haiku-4.5` | $1 | $5 |
| **Budget** | GPT-5.4 Nano | `openai/gpt-5.4-nano` | $0.20 | $1.25 |
| **Budget** | Gemini 3 Flash Preview | `google/gemini-3-flash-preview` | $0.50 | $3 |
| **Budget** | Gemini 3.1 Flash Lite | `google/gemini-3.1-flash-lite-preview` | $0.25 | $1.50 |
| **Ultra-cheap** | DeepSeek V3.2 | `deepseek/deepseek-chat` | $0.26 | $0.38 |
| **Ultra-cheap** | MiniMax M2.7 | `minimax/minimax-m2.7` | $0.30 | $1.20 |
| **Ultra-cheap** | Xiaomi MiMo-V2-Pro | `xiaomi/mimo-v2-pro` | $1 | $3 |
| **Ultra-cheap** | Qwen 3.5 Flash | `qwen/qwen3.5-flash` | $0.065 | $0.26 |
| **Ultra-cheap** | Xiaomi MiMo-V2-Flash | `xiaomi/mimo-v2-flash` | $0.09 | $0.29 |
| **Free** | StepFun Step 3.5 Flash | `stepfun/step-3.5-flash` | $0 | $0 |
| **Free** | Qwen 3.6 Plus | `qwen/qwen3.6-plus` | $0 | $0 |

12 models total: 1 ground truth + 11 candidates.

## Evaluation Strategy

### Roles

- **Ground truth generator:** Claude Sonnet 4.6 — run once, outputs cached as reference answers
- **Judge LLM:** GPT-5.4-mini — scores each candidate model's output against the Opus reference

### Per-Task Scoring

**Publication extraction:**
- Format validity (pass/fail): Does output parse as valid JSON matching the schema (title, authors, year, venue, status, draft_url, abstract)?
- Completeness (0-1): Judge compares extracted publication count and field coverage against ground truth
- Accuracy (0-1): Judge scores correctness of extracted values — titles, author names, years, venues, statuses

**Description extraction:**
- Relevance (0-1): Judge checks if the description accurately captures role, affiliation, and interests
- Conciseness (0-1): Roughly 200 words, no filler

**Researcher disambiguation:**
- Exact match (pass/fail): Does the model return the same `match_id` as Opus ground truth?
- Format validity (pass/fail): Is output valid `{"match_id": ...}` JSON?

**JEL classification:**
- Format validity (pass/fail): Parses as valid JEL codes with reasoning?
- Code overlap (0-1): Jaccard similarity between model's codes and Opus ground truth codes
- Reasoning quality (0-1): Judge evaluates whether reasoning supports assigned codes

## Workflow

```bash
# 1. Export test cases from DB
poetry run python eval/export_test_cases.py

# 2. Generate ground truth (Opus, one-time)
npx promptfoo eval --config eval/promptfooconfig.yaml --suite ground-truth

# 3. Run all candidate models + judging
npx promptfoo eval --config eval/promptfooconfig.yaml --suite candidates

# 4. View results
npx promptfoo view
```

## Cost Estimate

- Ground truth (Sonnet, ~160 calls): ~$3-6
- Candidates (11 models x 160 calls): ~$3-12
- Judging (GPT-5.4-mini, ~1760 scoring calls): ~$1-3
- **Total: ~$7-21 for a complete eval**

Ground truth is cached — adding a new model later only costs the candidate calls + judging.

## Prompts

Prompts are extracted verbatim from the existing codebase:

- `publication_extraction.txt` — from `Publication.build_extraction_prompt()` in `publication.py`
- `description_extraction.txt` — from `HTMLFetcher._extract_description()` in `html_fetcher.py`
- `researcher_disambiguation.txt` — from `_disambiguate_researcher()` in `database/researchers.py`
- `jel_classification.txt` — from `build_classification_prompt()` in `jel_classifier.py`

Each prompt uses promptfoo's `{{variable}}` syntax for test case interpolation (e.g., `{{text_content}}`, `{{url}}`).

## Out of Scope

- Modifying production code or the existing OpenAI integration
- Prompt optimization (testing the same prompts across models, not rewriting them)
- Automatic model switching in production (manual decision after reviewing results)
- Structured output enforcement via provider APIs (testing raw prompt compliance instead)
