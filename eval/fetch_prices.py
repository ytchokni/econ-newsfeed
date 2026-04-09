"""Fetch per-model prompt/completion prices from OpenRouter and inject them
into every eval/configs/*.yaml so promptfoo can compute USD cost.

Usage:  poetry run python eval/fetch_prices.py

OpenRouter's /api/v1/models returns pricing as USD-per-token strings in
``pricing.prompt`` and ``pricing.completion``. Promptfoo reads ``inputCost``
and ``outputCost`` (USD per token) from each provider's ``config`` block and
computes ``cost = inputCost*promptTokens + outputCost*completionTokens`` from
the usage returned in the response. See promptfoo ProviderOptions reference.

The script is idempotent: running it again refreshes the prices in place.
It also auto-corrects two stale model slugs that were invalidated upstream.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

MODELS_URL = "https://openrouter.ai/api/v1/models"
CONFIGS_DIR = Path(__file__).parent / "configs"

# Slug corrections: the left-hand ID has been retired on OpenRouter and must
# be rewritten to the right-hand ID before we can look up pricing.
SLUG_REWRITES = {
    "qwen/qwen3.5-flash": "qwen/qwen3.5-flash-02-23",
    "qwen/qwen3.6-plus:free": "qwen/qwen3.6-plus",  # no free tier exists
}

ID_LINE = re.compile(r"^(\s*)- id:\s*openrouter:(\S+)\s*$")
LABEL_LINE = re.compile(r"^\s*label:\s*")
CONFIG_LINE = re.compile(r"^\s*config:\s*$")


def fetch_prices() -> dict[str, tuple[str, str]]:
    with urllib.request.urlopen(MODELS_URL, timeout=30) as resp:
        data = json.load(resp)["data"]
    prices: dict[str, tuple[str, str]] = {}
    for model in data:
        pricing = model.get("pricing") or {}
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        if prompt is not None and completion is not None:
            prices[model["id"]] = (prompt, completion)
    return prices


def rewrite_config(path: Path, prices: dict[str, tuple[str, str]]) -> None:
    original = path.read_text().splitlines(keepends=False)
    out: list[str] = []
    i = 0
    changed = 0
    while i < len(original):
        line = original[i]
        match = ID_LINE.match(line)
        if not match:
            out.append(line)
            i += 1
            continue

        indent, raw_slug = match.group(1), match.group(2)
        canonical_slug = SLUG_REWRITES.get(raw_slug, raw_slug)
        price = prices.get(canonical_slug)
        if price is None:
            print(f"  WARN  {path.name}: no price for {raw_slug!r} (tried {canonical_slug!r})")
            out.append(line)
            i += 1
            continue

        # Emit id (rewritten if needed) + any label line, then fresh config block.
        if canonical_slug != raw_slug:
            out.append(f"{indent}- id: openrouter:{canonical_slug}")
        else:
            out.append(line)
        i += 1

        # Preserve an immediately-following label line if present.
        if i < len(original) and LABEL_LINE.match(original[i]):
            out.append(original[i])
            i += 1

        # Drop any pre-existing config block (so rerunning refreshes prices).
        if i < len(original) and CONFIG_LINE.match(original[i]):
            block_indent = len(original[i]) - len(original[i].lstrip())
            i += 1
            while i < len(original):
                nxt = original[i]
                if nxt.strip() == "":
                    i += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent > block_indent:
                    i += 1
                else:
                    break

        prompt_cost, completion_cost = price
        # Siblings of `- id:` (like `label:`) sit at `indent + "  "` because
        # the "- " marker occupies two columns.
        child_indent = indent + "  "
        out.append(f"{child_indent}config:")
        out.append(f"{child_indent}  inputCost: {prompt_cost}")
        out.append(f"{child_indent}  outputCost: {completion_cost}")
        changed += 1

    path.write_text("\n".join(out) + "\n")
    print(f"  {path.name}: updated {changed} providers")


def main() -> int:
    print(f"Fetching prices from {MODELS_URL} ...")
    prices = fetch_prices()
    print(f"  got {len(prices)} models")

    yamls = sorted(CONFIGS_DIR.glob("*.yaml"))
    if not yamls:
        print(f"No YAMLs found in {CONFIGS_DIR}", file=sys.stderr)
        return 1
    for yaml_path in yamls:
        rewrite_config(yaml_path, prices)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
