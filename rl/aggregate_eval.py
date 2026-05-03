"""Aggregate RL demo evaluation across seeds and commander-intent regimes."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

try:
    from rl.baseline import predict_baseline_action
    from rl.eval import REGIMES
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/aggregate_eval.py` from repo root.
    from baseline import predict_baseline_action
    from eval import REGIMES
    from rl_env import OverwatchEnv


DEFAULT_PPO_CONFIGS = (
    ("bc_200k", Path("rl/overwatch_bc_200k"), 1.0, False, False),
    ("conditioned_bc_scale3_30e", Path("rl/overwatch_conditioned_bc_scale3_30e"), 3.0, False, False),
    (
        "conditioned_bc_scale3_time_balanced",
        Path("rl/overwatch_conditioned_bc_scale3_time_balanced"),
        3.0,
        True,
        False,
    ),
    ("bc_scale10_teacher_100k", Path("rl/overwatch_bc_scale10_teacher_100k"), 10.0, False, False),
)
SUMMARY_METRICS = (
    "final_blue",
    "final_red",
    "enemy_destroyed",
    "own_lost",
    "new_cells",
    "radar_emits",
    "episode_length",
    "total_reward",
)


@dataclass(frozen=True)
class EvalConfig:
    name: str
    kind: str
    scale: float
    include_time_feature: bool = False
    include_time_phase: bool = False
    path: Path | None = None


def load_ppo(path: Path) -> Any:
    try:
        try:
            from rl.policy import RewardConditionedExtractor as _RewardConditionedExtractor
        except ModuleNotFoundError:  # Allows `python rl/aggregate_eval.py`.
            from policy import RewardConditionedExtractor as _RewardConditionedExtractor
        from stable_baselines3 import PPO
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "stable_baselines3 is required for PPO configs. "
            "Install rl/requirements.txt or run only baseline configs."
        ) from exc
    _ = _RewardConditionedExtractor
    return PPO.load(str(path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate baseline/PPO evals across seeds and regimes."
    )
    parser.add_argument(
        "--seeds",
        default="0,1,2",
        help="Comma-separated seeds, or N to use range(N). Default: 0,1,2.",
    )
    parser.add_argument("--steps", type=int, default=75)
    parser.add_argument(
        "--regime",
        choices=["all", *REGIMES.keys()],
        default="all",
    )
    parser.add_argument(
        "--config",
        action="append",
        help=(
            "Repeatable config. Use name=baseline@scale or name=path@scale. "
            "Append @time and/or @phase for matching time features. "
            "If omitted, runs baseline@1 and existing default PPO models."
        ),
    )
    parser.add_argument(
        "--stochastic",
        action="store_true",
        help="Sample PPO actions instead of deterministic actions.",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write per-episode rows to rl/logs/aggregate_eval.csv.",
    )
    parser.add_argument(
        "--csv-path",
        default="rl/logs/aggregate_eval.csv",
        help="CSV output path used with --csv.",
    )
    return parser.parse_args()


def parse_seeds(raw: str) -> list[int]:
    raw = raw.strip()
    if not raw:
        raise ValueError("--seeds cannot be empty")
    if "," not in raw and raw.isdigit():
        count = int(raw)
        if count <= 0:
            raise ValueError("--seeds count must be positive")
        return list(range(count))
    seeds = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not seeds:
        raise ValueError("--seeds did not contain any integers")
    return seeds


def zip_exists(path: Path) -> bool:
    zip_path = path if path.suffix == ".zip" else path.with_suffix(".zip")
    return zip_path.exists()


def parse_config(raw: str) -> EvalConfig:
    if "=" not in raw:
        raise ValueError(f"Invalid --config {raw!r}; expected name=baseline@scale or name=path@scale")
    name, target = raw.split("=", 1)
    name = name.strip()
    target = target.strip()
    if not name or not target:
        raise ValueError(f"Invalid --config {raw!r}; name and target are required")

    scale = 1.0
    include_time_feature = False
    include_time_phase = False
    if "@" in target:
        parts = target.split("@")
        target = parts[0]
        for part in parts[1:]:
            if part in {"time", "t", "include_time"}:
                include_time_feature = True
            elif part in {"phase", "include_phase"}:
                include_time_phase = True
            elif part:
                scale = float(part)
    if target == "baseline":
        return EvalConfig(
            name=name,
            kind="baseline",
            scale=scale,
            include_time_feature=include_time_feature,
            include_time_phase=include_time_phase,
        )
    return EvalConfig(
        name=name,
        kind="ppo",
        scale=scale,
        include_time_feature=include_time_feature,
        include_time_phase=include_time_phase,
        path=Path(target),
    )


def default_configs() -> list[EvalConfig]:
    configs = [EvalConfig("baseline", "baseline", 1.0)]
    for name, path, scale, include_time_feature, include_time_phase in DEFAULT_PPO_CONFIGS:
        if zip_exists(path):
            configs.append(
                EvalConfig(
                    name,
                    "ppo",
                    scale,
                    include_time_feature=include_time_feature,
                    include_time_phase=include_time_phase,
                    path=path,
                )
            )
    return configs


def selected_regimes(name: str) -> dict[str, list[float]]:
    if name == "all":
        return REGIMES
    return {name: REGIMES[name]}


def run_episode(
    config: EvalConfig,
    model: Any,
    regime_name: str,
    weights: list[float],
    seed: int,
    steps: int,
    deterministic: bool,
) -> dict[str, Any]:
    env = OverwatchEnv(
        max_steps=steps,
        weight_obs_scale=config.scale,
        include_time_feature=config.include_time_feature,
        include_time_phase=config.include_time_phase,
    )
    env.set_reward_weights(weights)
    obs, info = env.reset(seed=seed)

    total_reward = 0.0
    enemy_destroyed = 0.0
    own_lost = 0.0
    new_cells = 0.0
    radar_emits = 0
    episode_length = 0

    try:
        for episode_length in range(1, steps + 1):
            if config.kind == "baseline":
                action = predict_baseline_action(env)
            else:
                assert model is not None
                action, _state = model.predict(obs, deterministic=deterministic)

            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            enemy_destroyed += float(info["enemy_value_destroyed"])
            own_lost += float(info["own_value_lost"])
            new_cells += float(info["new_cells_revealed"])
            radar_emits += 1 if info["blue_sam_emit"] else 0
            if terminated or truncated:
                break
    finally:
        env.close()

    return {
        "config": config.name,
        "policy": config.kind,
        "scale": config.scale,
        "time_feature": config.include_time_feature,
        "time_phase": config.include_time_phase,
        "regime": regime_name,
        "seed": seed,
        "total_reward": total_reward,
        "enemy_destroyed": enemy_destroyed,
        "own_lost": own_lost,
        "new_cells": new_cells,
        "radar_emits": radar_emits,
        "episode_length": episode_length,
        "final_blue": float(info["blue_value"]),
        "final_red": float(info["red_value"]),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    groups: dict[tuple[str, str, float, bool, bool, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["config"],
            row["policy"],
            row["scale"],
            row["time_feature"],
            row["time_phase"],
            row["regime"],
        )
        groups.setdefault(key, []).append(row)

    summary_rows: list[dict[str, str]] = []
    for (config, policy, scale, time_feature, time_phase, regime), group in sorted(groups.items()):
        summary = {
            "config": config,
            "policy": policy,
            "scale": f"{scale:g}",
            "time": "yes" if time_feature else "no",
            "phase": "yes" if time_phase else "no",
            "regime": regime,
            "n": str(len(group)),
        }
        for metric in SUMMARY_METRICS:
            values = [float(row[metric]) for row in group]
            summary[metric] = f"{mean(values):.2f} +/- {pstdev(values):.2f}"
        summary_rows.append(summary)
    return summary_rows


def print_table(rows: list[dict[str, str]]) -> None:
    columns = ("config", "policy", "scale", "time", "phase", "regime", "n", *SUMMARY_METRICS)
    widths = {
        column: max(len(column), *(len(row[column]) for row in rows))
        for column in columns
    }
    print("  ".join(column.ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(row[column].ljust(widths[column]) for column in columns))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "config",
        "policy",
        "scale",
        "time_feature",
        "time_phase",
        "regime",
        "seed",
        *SUMMARY_METRICS,
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    if args.steps <= 0:
        raise ValueError("--steps must be positive")
    seeds = parse_seeds(args.seeds)
    regimes = selected_regimes(args.regime)
    configs = [parse_config(raw) for raw in args.config] if args.config else default_configs()
    if not configs:
        raise RuntimeError("No configs selected")

    rows: list[dict[str, Any]] = []
    for config in configs:
        model = None
        if config.kind == "ppo":
            assert config.path is not None
            if not zip_exists(config.path):
                print(f"skip missing model: {config.name} ({config.path.with_suffix('.zip')})")
                continue
            model = load_ppo(config.path)
        for regime_name, weights in regimes.items():
            for seed in seeds:
                rows.append(
                    run_episode(
                        config=config,
                        model=model,
                        regime_name=regime_name,
                        weights=weights,
                        seed=seed,
                        steps=args.steps,
                        deterministic=not args.stochastic,
                    )
                )

    if not rows:
        raise RuntimeError("No episodes were evaluated")

    print_table(summarize(rows))
    if args.csv:
        csv_path = Path(args.csv_path)
        write_csv(csv_path, rows)
        print(f"\nwrote {len(rows)} episode rows to {csv_path}")


if __name__ == "__main__":
    main()
