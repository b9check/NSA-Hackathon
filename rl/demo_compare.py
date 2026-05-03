"""Compact behavior-first comparison for the toy RL demo."""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from rl.aggregate_eval import EvalConfig, load_ppo, print_table, run_episode, selected_regimes, summarize
except ModuleNotFoundError:  # Allows `python rl/demo_compare.py` from repo root.
    from aggregate_eval import EvalConfig, load_ppo, print_table, run_episode, selected_regimes, summarize


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare scripted baseline against the best learned toy policy."
    )
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--steps", type=int, default=35)
    parser.add_argument(
        "--regime",
        choices=["all", "offensive", "defensive", "recon-heavy"],
        default="all",
    )
    parser.add_argument(
        "--include-alternates",
        action="store_true",
        help="Also show older/diagnostic learned checkpoints.",
    )
    return parser.parse_args()


def configs(include_alternates: bool) -> list[EvalConfig]:
    selected = [
        EvalConfig("baseline", "baseline", 1.0),
        EvalConfig(
            "time_balanced",
            "ppo",
            3.0,
            include_time_feature=True,
            path=Path("rl/overwatch_conditioned_bc_scale3_time_balanced"),
        ),
    ]
    if include_alternates:
        selected.extend(
            [
                EvalConfig(
                    "bc_200k",
                    "ppo",
                    1.0,
                    path=Path("rl/overwatch_bc_200k"),
                ),
                EvalConfig(
                    "phase_recon",
                    "ppo",
                    3.0,
                    include_time_feature=True,
                    include_time_phase=True,
                    path=Path("rl/overwatch_conditioned_bc_scale3_phase_recon"),
                ),
            ]
        )
    return selected


def main() -> None:
    args = parse_args()
    if args.seeds <= 0:
        raise ValueError("--seeds must be positive")
    if args.steps <= 0:
        raise ValueError("--steps must be positive")

    rows = []
    regimes = selected_regimes(args.regime)
    for config in configs(args.include_alternates):
        model = load_ppo(config.path) if config.kind == "ppo" and config.path is not None else None
        for regime_name, weights in regimes.items():
            for seed in range(args.seeds):
                rows.append(
                    run_episode(
                        config=config,
                        model=model,
                        regime_name=regime_name,
                        weights=weights,
                        seed=seed,
                        steps=args.steps,
                        deterministic=True,
                    )
                )
    print_table(summarize(rows))


if __name__ == "__main__":
    main()
