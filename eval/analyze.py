"""Cost-vs-quality analysis for promptfoo eval runs.

For every task config under ``eval/configs/*.yaml`` this script:

1. Reads ``inputCost`` / ``outputCost`` from the YAML so we know per-model pricing.
2. Finds the most recent matching eval in ``~/.promptfoo/promptfoo.db``
   (matched by the YAML ``description`` field).
3. Aggregates per (task, model): mean rubric score, pass rate, mean prompt /
   completion tokens, mean USD cost per call, and mean latency.
4. Computes the Pareto frontier on (cost ↓, score ↑) and prints a table with
   a ★ next to frontier models.
5. Saves a scatter plot per task to ``eval/results/frontier_<task>.png``
   with the frontier highlighted.

Usage:  poetry run python eval/analyze.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

try:
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - guidance only
    plt = None

CONFIGS_DIR = Path(__file__).parent / "configs"
RESULTS_DIR = Path(__file__).parent / "results"
DB_PATH = Path.home() / ".promptfoo" / "promptfoo.db"


def load_pricing(config_path: Path) -> tuple[str, dict[str, tuple[float, float]]]:
    """Return (task_description, {provider_id: (input_cost, output_cost)})."""
    data = yaml.safe_load(config_path.read_text())
    pricing: dict[str, tuple[float, float]] = {}
    for prov in data["providers"]:
        cfg = prov.get("config") or {}
        in_cost = float(cfg.get("inputCost") or 0)
        out_cost = float(cfg.get("outputCost") or 0)
        pricing[prov["id"]] = (in_cost, out_cost)
    return data["description"], pricing


def latest_eval_id(conn: sqlite3.Connection, description: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM evals WHERE description = ? ORDER BY created_at DESC LIMIT 1",
        (description,),
    ).fetchone()
    return row[0] if row else None


def fetch_rows(conn: sqlite3.Connection, eval_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT provider, score, success, latency_ms, response
        FROM eval_results
        WHERE eval_id = ?
        """,
        (eval_id,),
    )
    rows: list[dict] = []
    for provider_json, score, success, latency_ms, response_json in cur:
        provider = json.loads(provider_json) if provider_json else {}
        response = json.loads(response_json) if response_json else {}
        tu = response.get("tokenUsage") or {}
        rows.append(
            {
                "provider_id": provider.get("id"),
                "label": provider.get("label") or provider.get("id"),
                "score": float(score) if score is not None else 0.0,
                "success": bool(success),
                "latency_ms": latency_ms or 0,
                "prompt_tokens": tu.get("prompt") or 0,
                "completion_tokens": tu.get("completion") or 0,
            }
        )
    return rows


def aggregate(
    rows: list[dict], pricing: dict[str, tuple[float, float]]
) -> list[dict]:
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["provider_id"], []).append(r)

    agg: list[dict] = []
    for pid, items in by_model.items():
        n = len(items)
        in_cost, out_cost = pricing.get(pid, (0.0, 0.0))
        mean_in = sum(r["prompt_tokens"] for r in items) / n
        mean_out = sum(r["completion_tokens"] for r in items) / n
        mean_cost = mean_in * in_cost + mean_out * out_cost
        agg.append(
            {
                "provider_id": pid,
                "label": items[0]["label"],
                "n": n,
                "mean_score": sum(r["score"] for r in items) / n,
                "pass_rate": sum(1 for r in items if r["success"]) / n,
                "mean_in_tok": mean_in,
                "mean_out_tok": mean_out,
                "mean_cost_usd": mean_cost,
                "mean_latency_ms": sum(r["latency_ms"] for r in items) / n,
            }
        )
    return agg


def pareto_frontier(agg: list[dict]) -> set[str]:
    """Return the set of provider_ids on the (cost↓, score↑) frontier."""
    # Sort ascending by cost, tie-break by descending score (prefer better at same cost).
    ordered = sorted(agg, key=lambda r: (r["mean_cost_usd"], -r["mean_score"]))
    frontier: set[str] = set()
    best_score = -1.0
    for r in ordered:
        if r["mean_score"] > best_score:
            frontier.add(r["provider_id"])
            best_score = r["mean_score"]
    return frontier


def print_table(task: str, agg: list[dict], frontier: set[str]) -> None:
    print(f"\n=== {task} ===")
    header = f"{'':2} {'Model':<26} {'Score':>6} {'Pass':>6} {'In tok':>7} {'Out tok':>8} {'$/call':>12} {'Latency':>9}"
    print(header)
    print("-" * len(header))
    for r in sorted(agg, key=lambda x: -x["mean_score"]):
        star = "★" if r["provider_id"] in frontier else " "
        print(
            f"{star:2} {r['label'][:26]:<26} "
            f"{r['mean_score']:>6.3f} "
            f"{r['pass_rate']:>6.2f} "
            f"{r['mean_in_tok']:>7.0f} "
            f"{r['mean_out_tok']:>8.0f} "
            f"${r['mean_cost_usd']:>10.6f} "
            f"{r['mean_latency_ms']:>7.0f}ms"
        )


def plot_frontier(task: str, agg: list[dict], frontier: set[str], out_path: Path) -> None:
    if plt is None:
        print("  (matplotlib not installed; skipping plot)")
        return

    fig, ax = plt.subplots(figsize=(9, 6))
    for r in agg:
        is_front = r["provider_id"] in frontier
        ax.scatter(
            max(r["mean_cost_usd"], 1e-8),
            r["mean_score"],
            s=140 if is_front else 80,
            c="#1f77b4" if is_front else "#bbbbbb",
            edgecolors="black" if is_front else "none",
            linewidths=1.0,
            zorder=3 if is_front else 2,
        )
        ax.annotate(
            r["label"],
            (max(r["mean_cost_usd"], 1e-8), r["mean_score"]),
            xytext=(6, 4),
            textcoords="offset points",
            fontsize=8,
        )

    # Draw frontier line.
    front_pts = sorted(
        ((r["mean_cost_usd"], r["mean_score"]) for r in agg if r["provider_id"] in frontier),
        key=lambda p: p[0],
    )
    if len(front_pts) >= 2:
        fx, fy = zip(*front_pts)
        ax.plot(fx, fy, color="#1f77b4", linewidth=1.5, linestyle="--", zorder=2)

    ax.set_xscale("log")
    ax.set_xlabel("Mean cost per call (USD, log scale)")
    ax.set_ylabel("Mean rubric score")
    ax.set_title(f"Cost-vs-quality: {task}")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, 1.05)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"  plot saved → {out_path}")


def main() -> int:
    if not DB_PATH.exists():
        print(f"No promptfoo DB at {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)

    for yaml_path in sorted(CONFIGS_DIR.glob("*.yaml")):
        task, pricing = load_pricing(yaml_path)
        eval_id = latest_eval_id(conn, task)
        if not eval_id:
            print(f"\n=== {task} ===\n  (no eval runs found)")
            continue
        rows = fetch_rows(conn, eval_id)
        if not rows:
            print(f"\n=== {task} ===\n  (eval {eval_id} has no rows)")
            continue
        agg = aggregate(rows, pricing)
        frontier = pareto_frontier(agg)
        print_table(task, agg, frontier)
        slug = yaml_path.stem
        plot_frontier(task, agg, frontier, RESULTS_DIR / f"frontier_{slug}.png")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
