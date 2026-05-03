"""Build the demo's hero charts from headless_match output.

Inputs:
    runs/phase1/summary.csv          -- self-play games, lessons grew here
    runs/phase2/summary.csv          -- evals at each lesson checkpoint
    runs/phase1/checkpoints/*.jsonl  -- frozen lesson snapshots

Outputs (PNG @ 1600x1000, white bg, dark text):
    runs/plots/learning_curve.png    -- THE money chart (win-rate vs lessons)
    runs/plots/lesson_growth.png     -- lesson count over self-play games
    runs/plots/cost_economy.png      -- per-game $ + cache_read fraction
    runs/plots/order_validity.png    -- fallback / drops per turn

No statistical claims with tiny N — captions explicitly mark sample sizes.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
PHASE1 = ROOT / "runs" / "phase1"
PHASE2 = ROOT / "runs" / "phase2"
PLOTS = ROOT / "runs" / "plots"


def read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def to_float(x: str, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def to_int(x: str, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def plot_learning_curve(out: Path) -> None:
    """For each Phase 2 checkpoint, plot blue win-rate (out of N games)."""
    rows = read_csv(PHASE2 / "summary.csv")
    if not rows:
        print(f"[skip] learning_curve: no rows in {PHASE2/'summary.csv'}")
        return

    # Group by checkpoint label encoded in memory_mode (e.g. "ckpt_10").
    by_ckpt: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        m = re.search(r"ckpt[_\-]?(\d+)", r.get("memory_mode", ""))
        if not m:
            continue
        by_ckpt.setdefault(int(m.group(1)), []).append(r)

    if not by_ckpt:
        print(f"[skip] learning_curve: no ckpt_<n> rows")
        return

    xs = sorted(by_ckpt.keys())
    wins = [sum(1 for r in by_ckpt[k] if r["winner"] == "blue") for k in xs]
    totals = [len(by_ckpt[k]) for k in xs]
    rates = [w / max(1, t) for w, t in zip(wins, totals)]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(xs, rates, marker="o", linewidth=2.5, markersize=10, color="#FFB84D")
    for x, w, t in zip(xs, wins, totals):
        ax.annotate(f"{w}/{t}", (x, w/max(1,t)),
                    textcoords="offset points", xytext=(0, 12), ha="center",
                    fontsize=11, color="#222")
    ax.set_xlabel("Lessons in memory at game start", fontsize=12)
    ax.set_ylabel("Blue win rate vs scripted opponent", fontsize=12)
    ax.set_title(
        "Learning curve — same agent, more experience",
        fontsize=14, weight="bold",
    )
    ax.set_ylim(-0.05, 1.05)
    ax.set_xticks(xs)
    ax.grid(alpha=0.25)
    fig.text(0.5, 0.01,
             f"N = {totals[0]} eval games per checkpoint · Sonnet 4.6 · Galician Approach",
             ha="center", fontsize=10, color="#666", style="italic")
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120, facecolor="white")
    plt.close(fig)
    print(f"[ok] {out}")


def plot_lesson_growth(out: Path) -> None:
    rows = read_csv(PHASE1 / "summary.csv")
    if not rows:
        print(f"[skip] lesson_growth: no rows")
        return
    xs = list(range(1, len(rows) + 1))
    ys = [to_int(r.get("lessons_at_end", "0")) for r in rows]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(xs, ys, marker="o", linewidth=2.5, color="#4DA3FF")
    ax.fill_between(xs, 0, ys, alpha=0.15, color="#4DA3FF")
    ax.set_xlabel("Self-play game #", fontsize=12)
    ax.set_ylabel("Lessons in memory after game", fontsize=12)
    ax.set_title("Memory growth during self-play", fontsize=14, weight="bold")
    ax.grid(alpha=0.25)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=120, facecolor="white")
    plt.close(fig)
    print(f"[ok] {out}")


def plot_cost_economy(out: Path) -> None:
    rows = read_csv(PHASE1 / "summary.csv") + read_csv(PHASE2 / "summary.csv")
    if not rows:
        print(f"[skip] cost_economy: no rows")
        return
    # Sonnet 4.6 pricing (https://www.anthropic.com/pricing): in $3/MTok, out $15/MTok, cache_read $0.30/MTok.
    PRICE_IN, PRICE_OUT, PRICE_CACHE_READ = 3.0, 15.0, 0.30
    per_game_costs: List[float] = []
    cache_fracs: List[float] = []
    for r in rows:
        i = to_int(r.get("input_tokens", "0"))
        o = to_int(r.get("output_tokens", "0"))
        c = to_int(r.get("cache_read_tokens", "0"))
        cost = (i / 1e6) * PRICE_IN + (o / 1e6) * PRICE_OUT + (c / 1e6) * PRICE_CACHE_READ
        per_game_costs.append(cost)
        total_in = i + c
        cache_fracs.append(c / max(1, total_in))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    ax1.hist(per_game_costs, bins=12, color="#FFB84D", edgecolor="#222")
    ax1.set_xlabel("Cost per game (USD)", fontsize=12)
    ax1.set_ylabel("Games", fontsize=12)
    ax1.set_title(
        f"Per-game cost · mean ${sum(per_game_costs)/max(1,len(per_game_costs)):.2f} "
        f"· total ${sum(per_game_costs):.2f}",
        fontsize=12, weight="bold",
    )
    ax1.grid(alpha=0.2)
    ax2.hist([f * 100 for f in cache_fracs], bins=12, color="#4DA3FF", edgecolor="#222")
    ax2.set_xlabel("% input tokens served from prompt cache", fontsize=12)
    ax2.set_ylabel("Games", fontsize=12)
    ax2.set_title("Prompt cache hit ratio", fontsize=12, weight="bold")
    ax2.grid(alpha=0.2)

    fig.suptitle("Cost economy — Sonnet 4.6 with prompt caching",
                 fontsize=14, weight="bold")
    fig.text(0.5, 0.01,
             f"{len(rows)} games · all costs derived from per-turn token usage",
             ha="center", fontsize=10, color="#666", style="italic")
    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    fig.savefig(out, dpi=120, facecolor="white")
    plt.close(fig)
    print(f"[ok] {out}")


def plot_order_validity(out: Path) -> None:
    """% of games with zero fallback turns + drops distribution."""
    rows = read_csv(PHASE1 / "summary.csv") + read_csv(PHASE2 / "summary.csv")
    if not rows:
        print(f"[skip] order_validity: no rows")
        return
    fallbacks = [to_int(r.get("fallback_turns", "0")) for r in rows]
    drops = [to_int(r.get("drops_total", "0")) for r in rows]
    clean_games = sum(1 for f, d in zip(fallbacks, drops) if f == 0 and d == 0)
    total = len(rows)

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(
        ["Clean games", "Games w/ at least 1 drop", "Games w/ fallback turn"],
        [
            clean_games,
            sum(1 for d in drops if d > 0),
            sum(1 for f in fallbacks if f > 0),
        ],
        color=["#4DA3FF", "#FFB84D", "#FF4D5E"],
        edgecolor="#222",
    )
    for b, v in zip(bars, [clean_games,
                           sum(1 for d in drops if d > 0),
                           sum(1 for f in fallbacks if f > 0)]):
        ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.3,
                str(v), ha="center", fontsize=12, weight="bold")
    ax.set_ylabel("Games", fontsize=12)
    ax.set_title(
        f"Order-validity — {clean_games}/{total} games "
        f"clean ({100*clean_games/max(1,total):.0f}%)",
        fontsize=14, weight="bold",
    )
    ax.grid(axis="y", alpha=0.25)
    fig.text(0.5, 0.01,
             "menu+repair pipeline catches invalid LLM tool outputs before they hit the engine",
             ha="center", fontsize=10, color="#666", style="italic")
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(out, dpi=120, facecolor="white")
    plt.close(fig)
    print(f"[ok] {out}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", type=Path, default=PLOTS)
    a = p.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    plot_learning_curve(a.out_dir / "learning_curve.png")
    plot_lesson_growth(a.out_dir / "lesson_growth.png")
    plot_cost_economy(a.out_dir / "cost_economy.png")
    plot_order_validity(a.out_dir / "order_validity.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
