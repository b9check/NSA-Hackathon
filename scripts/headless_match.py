"""Headless match runner — drives full games at the engine level (no HTTP).

Loops `play_side(blue) + play_side(red) → resolve_turn` until victory or
turn cap, optionally fires `reflect_and_persist` at game end. Writes one
CSV row per game + per-game JSONL trace.

Usage:
    python -m scripts.headless_match \\
        --scenario runs/phase1/scenario.yaml \\
        --blue llm --red llm \\
        --start-game 1 --end-game 20 \\
        --reflect on \\
        --memory-path runs/phase1/lessons.jsonl \\
        --checkpoints 0,5,10,20 \\
        --checkpoint-dir runs/phase1/checkpoints \\
        --out runs/phase1

Resumability: all outputs are append-style. To extend an existing run,
just rerun with new --start-game / --end-game; lessons keep accreting,
summary.csv gets new rows, checkpoints unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env BEFORE any ai.* import — anthropic.AsyncAnthropic() reads
# ANTHROPIC_API_KEY at construction time.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# We may want a different lessons file for this run; set it before importing
# memory so any module-level cache picks the right path.
def _set_memory_path(path: Path) -> None:
    os.environ["AI_MEMORY_PATH"] = str(path.resolve())


CSV_COLUMNS = [
    "game_id", "seed", "blue_kind", "red_kind", "model",
    "memory_mode", "lessons_at_start", "lessons_at_end",
    "turns", "winner", "win_reason",
    "blue_hp_pct", "red_hp_pct",
    "blue_units_alive", "red_units_alive",
    "input_tokens", "output_tokens", "cache_read_tokens",
    "fallback_turns", "drops_total",
    "wallclock_s",
]


async def play_one_game(
    *,
    seed: int,
    scenario_path: Path,
    blue_kind: str,
    red_kind: str,
    reflect: bool,
    out_dir: Path,
    memory_mode: str,
) -> Dict[str, Any]:
    # Imports here so AI_MEMORY_PATH env var is honored.
    from engine.scenario import load_scenario
    from engine.resolve import resolve_turn, compute_scores
    from ai.runner import play_side
    from ai.reflect import reflect_and_persist
    from ai.memory import load_lessons

    state = load_scenario(scenario_path)
    state.seed = seed
    gid = f"g{seed}_{uuid.uuid4().hex[:4]}"
    log: List[Dict[str, Any]] = []
    t0 = time.monotonic()

    # Track usage across the whole game.
    total_input = total_output = total_cache_read = 0
    fallback_turns = 0
    drops_total = 0
    lessons_at_start = len(load_lessons())

    starting_hp_blue = sum(u.hp for u in state.units if u.side == "blue") + \
                       sum(b.hp for b in state.bases if b.side == "blue")
    starting_hp_red = sum(u.hp for u in state.units if u.side == "red") + \
                      sum(b.hp for b in state.bases if b.side == "red")

    while state.winner is None and state.turn < state.turn_limit:
        # Both controllers run in parallel — they don't observe each other's
        # in-flight reasoning, only state at start of turn.
        blue_task = asyncio.create_task(play_side("blue", blue_kind, state))
        red_task = asyncio.create_task(play_side("red", red_kind, state))
        (b_orders, b_meta), (r_orders, r_meta) = await asyncio.gather(blue_task, red_task)

        events = resolve_turn(state, b_orders, r_orders)
        bs, rs = compute_scores(state)

        # Aggregate usage.
        for meta in (b_meta, r_meta):
            usage = meta.get("usage") or {}
            total_input += int(usage.get("input", 0) or 0)
            total_output += int(usage.get("output", 0) or 0)
            total_cache_read += int(usage.get("cache_read", 0) or 0)
            if meta.get("fallback"):
                fallback_turns += 1
            drops_total += len(meta.get("drops") or [])

        log.append({
            "turn": state.turn - 1,  # resolve_turn increments before returning
            "blue_summary": str(b_meta.get("summary", ""))[:300],
            "red_summary": str(r_meta.get("summary", ""))[:300],
            "blue_decisions": b_meta.get("decisions", []),
            "red_decisions": r_meta.get("decisions", []),
            "scores": {"blue": bs, "red": rs},
            "events": [e.model_dump(mode="json") for e in events],
            "blue_usage": b_meta.get("usage"),
            "red_usage": r_meta.get("usage"),
        })

    end_blue_hp = sum(u.hp for u in state.units if u.side == "blue") + \
                  sum(b.hp for b in state.bases if b.side == "blue")
    end_red_hp = sum(u.hp for u in state.units if u.side == "red") + \
                 sum(b.hp for b in state.bases if b.side == "red")

    if reflect and state.winner is not None:
        try:
            await reflect_and_persist(
                log, state.model_dump(mode="json"), game_id=gid,
            )
        except Exception as e:  # don't kill the harness if reflect blows up
            print(f"[{gid}] reflect failed: {e!r}", flush=True)

    lessons_at_end = len(load_lessons())

    # Persist trace.
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{gid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in log), encoding="utf-8",
    )

    return {
        "game_id": gid,
        "seed": seed,
        "blue_kind": blue_kind,
        "red_kind": red_kind,
        "model": "claude-sonnet-4-6",  # informational; pulled from controller default
        "memory_mode": memory_mode,
        "lessons_at_start": lessons_at_start,
        "lessons_at_end": lessons_at_end,
        "turns": state.turn,
        "winner": state.winner or "draw",
        "win_reason": state.win_reason or "turn_cap",
        "blue_hp_pct": round(end_blue_hp / max(1, starting_hp_blue), 4),
        "red_hp_pct": round(end_red_hp / max(1, starting_hp_red), 4),
        "blue_units_alive": sum(1 for u in state.units if u.side == "blue"),
        "red_units_alive": sum(1 for u in state.units if u.side == "red"),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "fallback_turns": fallback_turns,
        "drops_total": drops_total,
        "wallclock_s": round(time.monotonic() - t0, 2),
    }


def append_csv_row(csv_path: Path, row: Dict[str, Any]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def maybe_snapshot(
    game_idx: int,
    checkpoints: List[int],
    memory_path: Path,
    checkpoint_dir: Path,
) -> Optional[Path]:
    """If `game_idx` (number of games COMPLETED) hits a checkpoint, freeze
    the lessons file. Returns path of snapshot if taken."""
    if game_idx not in checkpoints:
        return None
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    snap = checkpoint_dir / f"lessons_after_{game_idx}.jsonl"
    if memory_path.exists():
        shutil.copy(memory_path, snap)
    else:
        snap.write_text("", encoding="utf-8")  # empty file — pre-game-1
    return snap


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", required=True, type=Path)
    p.add_argument("--blue", required=True,
                   help="controller kind for blue: random, llm, llm-no-memory")
    p.add_argument("--red", required=True,
                   help="controller kind for red: random, llm, llm-no-memory, scripted")
    p.add_argument("--start-game", type=int, default=1)
    p.add_argument("--end-game", type=int, default=10)
    p.add_argument("--seed-base", type=int, default=1000,
                   help="seed = seed-base + game_index")
    p.add_argument("--reflect", choices=["on", "off"], default="on")
    p.add_argument("--memory-path", type=Path, required=True,
                   help="lessons.jsonl path — set via AI_MEMORY_PATH so retrieval reads from here")
    p.add_argument("--memory-mode", default="grow",
                   help="label only: grow / frozen / empty / etc.")
    p.add_argument("--checkpoints", default="",
                   help="comma-separated game indices to snapshot lessons (e.g. 0,5,10,20)")
    p.add_argument("--checkpoint-dir", type=Path, default=None)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()

    if not args.scenario.exists():
        print(f"error: scenario file not found: {args.scenario}", file=sys.stderr)
        return 2

    _set_memory_path(args.memory_path)

    # Honor "scripted" red as a non-controller-kind: route through a
    # private import path, return orders directly.
    blue_is_scripted = args.blue == "scripted"
    red_is_scripted = args.red == "scripted"
    if blue_is_scripted or red_is_scripted:
        from rl_alt import scripted_red as scripted

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / "summary.csv"
    checkpoints = [int(x) for x in args.checkpoints.split(",") if x.strip()] if args.checkpoints else []
    ckpt_dir = args.checkpoint_dir or (args.out / "checkpoints")

    # Snapshot at game-0 (i.e. before any game runs) if requested.
    if 0 in checkpoints and args.start_game == 1:
        maybe_snapshot(0, checkpoints, args.memory_path, ckpt_dir)
        print(f"[ckpt] snapshot @ game 0 → {ckpt_dir}/lessons_after_0.jsonl", flush=True)

    print(
        f"[start] scenario={args.scenario.name} blue={args.blue} red={args.red} "
        f"games={args.start_game}..{args.end_game} memory={args.memory_path.name}",
        flush=True,
    )

    if blue_is_scripted or red_is_scripted:
        # Delegate to a wrapper-aware loop: scripted side bypasses ai.runner.
        return await _scripted_loop(args, csv_path, scripted, ckpt_dir, checkpoints)

    # Standard: both sides via ai.runner.play_side.
    for idx in range(args.start_game, args.end_game + 1):
        seed = args.seed_base + idx
        try:
            row = await play_one_game(
                seed=seed,
                scenario_path=args.scenario,
                blue_kind=args.blue,
                red_kind=args.red,
                reflect=(args.reflect == "on"),
                out_dir=args.out,
                memory_mode=args.memory_mode,
            )
            append_csv_row(csv_path, row)
            print(
                f"[game {idx:>3}/{args.end_game}] {row['game_id']} "
                f"winner={row['winner']:<5} turns={row['turns']:>2} "
                f"hp_b={row['blue_hp_pct']:.2f} hp_r={row['red_hp_pct']:.2f} "
                f"lessons {row['lessons_at_start']}→{row['lessons_at_end']} "
                f"{row['wallclock_s']:.1f}s",
                flush=True,
            )
        except Exception as e:
            print(f"[game {idx}] ERROR: {e!r}", flush=True)
            import traceback
            traceback.print_exc()

        snap = maybe_snapshot(idx, checkpoints, args.memory_path, ckpt_dir)
        if snap is not None:
            print(f"[ckpt] snapshot @ game {idx} → {snap.name}", flush=True)

        # Reset the LLM client between games so a long run can't accumulate
        # stale httpx CLOSE_WAIT sockets that eventually choke the loop.
        try:
            from ai.llm_agent import reset_client
            await reset_client()
        except Exception as e:
            print(f"[warn] reset_client failed: {e!r}", flush=True)

    print(f"[done] csv → {csv_path}", flush=True)
    return 0


async def _scripted_loop(args, csv_path, scripted, ckpt_dir, checkpoints) -> int:
    """Variant where one side is scripted_red — bypasses ai.runner for that
    side so we can use it as a static evaluation opponent."""
    from engine.scenario import load_scenario
    from engine.resolve import resolve_turn, compute_scores
    from ai.runner import play_side
    from ai.memory import load_lessons

    for idx in range(args.start_game, args.end_game + 1):
        seed = args.seed_base + idx
        t0 = time.monotonic()
        state = load_scenario(args.scenario)
        state.seed = seed
        gid = f"g{seed}_{uuid.uuid4().hex[:4]}"
        log: List[Dict[str, Any]] = []
        total_input = total_output = total_cache_read = 0
        fallback_turns = 0
        drops_total = 0
        lessons_at_start = len(load_lessons())

        starting_hp_blue = sum(u.hp for u in state.units if u.side == "blue") + \
                           sum(b.hp for b in state.bases if b.side == "blue")
        starting_hp_red = sum(u.hp for u in state.units if u.side == "red") + \
                          sum(b.hp for b in state.bases if b.side == "red")

        try:
            while state.winner is None and state.turn < state.turn_limit:
                tt = time.monotonic()
                if args.blue == "scripted":
                    b_orders = scripted.choose_orders(state, "blue")
                    b_meta: Dict[str, Any] = {"summary": "scripted", "decisions": []}
                else:
                    b_orders, b_meta = await play_side("blue", args.blue, state)
                print(f"  t{state.turn} blue→{len(b_orders)} orders ({time.monotonic()-tt:.1f}s)", flush=True)
                tt = time.monotonic()
                if args.red == "scripted":
                    r_orders = scripted.choose_orders(state, "red")
                    r_meta = {"summary": "scripted", "decisions": []}
                else:
                    r_orders, r_meta = await play_side("red", args.red, state)
                print(f"  t{state.turn} red→{len(r_orders)} orders ({time.monotonic()-tt:.1f}s)", flush=True)
                events = resolve_turn(state, b_orders, r_orders)
                bs, rs = compute_scores(state)
                for meta in (b_meta, r_meta):
                    usage = meta.get("usage") or {}
                    total_input += int(usage.get("input", 0) or 0)
                    total_output += int(usage.get("output", 0) or 0)
                    total_cache_read += int(usage.get("cache_read", 0) or 0)
                    if meta.get("fallback"):
                        fallback_turns += 1
                    drops_total += len(meta.get("drops") or [])
                log.append({
                    "turn": state.turn - 1,
                    "blue_summary": str(b_meta.get("summary", ""))[:300],
                    "red_summary": str(r_meta.get("summary", ""))[:300],
                    "scores": {"blue": bs, "red": rs},
                    "events": [e.model_dump(mode="json") for e in events],
                })
        except Exception as e:
            print(f"[game {idx}] ERROR mid-game: {e!r}", flush=True)
            import traceback
            traceback.print_exc()

        end_blue_hp = sum(u.hp for u in state.units if u.side == "blue") + \
                      sum(b.hp for b in state.bases if b.side == "blue")
        end_red_hp = sum(u.hp for u in state.units if u.side == "red") + \
                     sum(b.hp for b in state.bases if b.side == "red")

        # No reflect for scripted opponent matches — we want eval, not training.
        lessons_at_end = len(load_lessons())
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{gid}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in log), encoding="utf-8",
        )
        row = {
            "game_id": gid, "seed": seed,
            "blue_kind": args.blue, "red_kind": args.red,
            "model": "claude-sonnet-4-6",
            "memory_mode": args.memory_mode,
            "lessons_at_start": lessons_at_start,
            "lessons_at_end": lessons_at_end,
            "turns": state.turn,
            "winner": state.winner or "draw",
            "win_reason": state.win_reason or "turn_cap",
            "blue_hp_pct": round(end_blue_hp / max(1, starting_hp_blue), 4),
            "red_hp_pct": round(end_red_hp / max(1, starting_hp_red), 4),
            "blue_units_alive": sum(1 for u in state.units if u.side == "blue"),
            "red_units_alive": sum(1 for u in state.units if u.side == "red"),
            "input_tokens": total_input, "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "fallback_turns": fallback_turns, "drops_total": drops_total,
            "wallclock_s": round(time.monotonic() - t0, 2),
        }
        append_csv_row(csv_path, row)
        print(
            f"[game {idx:>3}/{args.end_game}] {gid} winner={row['winner']:<5} "
            f"turns={row['turns']:>2} hp_b={row['blue_hp_pct']:.2f} "
            f"hp_r={row['red_hp_pct']:.2f} {row['wallclock_s']:.1f}s",
            flush=True,
        )

        # Same client reset between games (see standard loop above).
        try:
            from ai.llm_agent import reset_client
            await reset_client()
        except Exception as e:
            print(f"[warn] reset_client failed: {e!r}", flush=True)

    print(f"[done] csv → {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
