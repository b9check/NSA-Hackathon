"""Measure action agreement between the scripted teacher and a PPO student."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from stable_baselines3 import PPO

try:
    from rl.baseline import predict_baseline_action
    from rl.eval import REGIMES
    from rl.policy import RewardConditionedExtractor as _RewardConditionedExtractor
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/action_agreement.py` from repo root.
    from baseline import predict_baseline_action
    from eval import REGIMES
    from policy import RewardConditionedExtractor as _RewardConditionedExtractor
    from rl_env import OverwatchEnv


HEAD_NAMES = (
    "recon_move",
    "recon_policy",
    "strike_move",
    "strike_policy",
    "sam_move",
    "sam_policy",
    "radar_emit",
)


@dataclass
class AgreementStats:
    steps: int = 0
    full_matches: int = 0
    head_matches: np.ndarray = field(default_factory=lambda: np.zeros(len(HEAD_NAMES), dtype=np.int64))
    head_totals: np.ndarray = field(default_factory=lambda: np.zeros(len(HEAD_NAMES), dtype=np.int64))
    regime_steps: Counter[str] = field(default_factory=Counter)
    regime_episodes: Counter[str] = field(default_factory=Counter)
    regime_full_matches: Counter[str] = field(default_factory=Counter)
    regime_head_matches: dict[str, np.ndarray] = field(default_factory=dict)
    regime_head_totals: dict[str, np.ndarray] = field(default_factory=dict)
    mismatch_pairs: Counter[tuple[str, str, int, int]] = field(default_factory=Counter)

    def add(self, regime: str, teacher_action: np.ndarray, student_action: np.ndarray) -> None:
        matches = teacher_action == student_action
        full_match = bool(np.all(matches))
        self.steps += 1
        self.full_matches += int(full_match)
        self.head_matches += matches.astype(np.int64)
        self.head_totals += 1
        self.regime_steps[regime] += 1
        self.regime_full_matches[regime] += int(full_match)
        self.regime_head_matches.setdefault(
            regime,
            np.zeros(len(HEAD_NAMES), dtype=np.int64),
        )
        self.regime_head_totals.setdefault(
            regime,
            np.zeros(len(HEAD_NAMES), dtype=np.int64),
        )
        self.regime_head_matches[regime] += matches.astype(np.int64)
        self.regime_head_totals[regime] += 1
        for idx, matched in enumerate(matches):
            if not matched:
                self.mismatch_pairs[
                    (regime, HEAD_NAMES[idx], int(teacher_action[idx]), int(student_action[idx]))
                ] += 1

    def add_episode(self, regime: str) -> None:
        self.regime_episodes[regime] += 1

    @property
    def full_match_rate(self) -> float:
        return self.full_matches / max(self.steps, 1)

    def head_rate(self, idx: int) -> float:
        return float(self.head_matches[idx] / max(self.head_totals[idx], 1))

    def regime_rate(self, regime: str) -> float:
        return self.regime_full_matches[regime] / max(self.regime_steps[regime], 1)

    def regime_head_rate(self, regime: str, idx: int) -> float:
        totals = self.regime_head_totals.get(regime)
        matches = self.regime_head_matches.get(regime)
        if totals is None or matches is None:
            return 0.0
        return float(matches[idx] / max(totals[idx], 1))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare scripted baseline teacher actions with deterministic PPO "
            "student actions on matched teacher-rollout episodes."
        )
    )
    parser.add_argument("--model", default="rl/overwatch_agent")
    parser.add_argument("--steps-per-seed", type=int, default=75)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--weight-obs-scale", type=float, default=1.0)
    parser.add_argument(
        "--include-time-feature",
        action="store_true",
        help="Append normalized episode time before the four reward weights.",
    )
    parser.add_argument(
        "--include-time-phase",
        action="store_true",
        help="Append one-hot t mod 3 before the four reward weights.",
    )
    parser.add_argument(
        "--regime",
        choices=["all", *REGIMES.keys()],
        default="all",
        help="Run all regimes or just one commander intent.",
    )
    parser.add_argument(
        "--top-mismatches",
        type=int,
        default=5,
        help="Show top teacher->student mismatches per regime/head. Use 0 to hide.",
    )
    return parser.parse_args()


def selected_regimes(name: str) -> dict[str, list[float]]:
    if name == "all":
        return REGIMES
    return {name: REGIMES[name]}


def load_model(path: str) -> PPO:
    model_path = Path(path)
    zip_path = model_path if model_path.suffix == ".zip" else model_path.with_suffix(".zip")
    if not zip_path.exists():
        raise FileNotFoundError(f"Missing trained model: {zip_path}")
    return PPO.load(str(model_path))


def normalize_action(action: np.ndarray | list[int]) -> np.ndarray:
    action_arr = np.asarray(action, dtype=np.int64)
    if action_arr.shape != (len(HEAD_NAMES),):
        action_arr = action_arr.reshape(len(HEAD_NAMES))
    return action_arr


def run_episode(
    *,
    model: PPO,
    stats: AgreementStats,
    regime: str,
    weights: list[float],
    seed: int,
    steps_per_seed: int,
    weight_obs_scale: float,
    include_time_feature: bool,
    include_time_phase: bool,
) -> None:
    env = OverwatchEnv(
        max_steps=steps_per_seed,
        weight_obs_scale=weight_obs_scale,
        include_time_feature=include_time_feature,
        include_time_phase=include_time_phase,
    )
    env.set_reward_weights(weights)
    obs, _info = env.reset(seed=seed)
    stats.add_episode(regime)

    for _step in range(steps_per_seed):
        teacher_action = normalize_action(predict_baseline_action(env))
        student_action, _state = model.predict(obs, deterministic=True)
        student_action = normalize_action(student_action)
        stats.add(regime, teacher_action, student_action)

        obs, _reward, terminated, truncated, _info = env.step(teacher_action)
        if terminated or truncated:
            break

    env.close()


def print_report(stats: AgreementStats, regimes: dict[str, list[float]], args: argparse.Namespace) -> None:
    print("action_agreement")
    print(f"model={args.model}")
    print(f"regime={args.regime} seeds={args.seeds} steps_per_seed={args.steps_per_seed}")
    print(f"weight_obs_scale={args.weight_obs_scale}")
    print(f"include_time_feature={args.include_time_feature}")
    print(f"include_time_phase={args.include_time_phase}")
    print()
    print(f"total_steps={stats.steps}")
    print(f"full_action_match_rate={stats.full_match_rate:.4f} ({stats.full_matches}/{stats.steps})")
    print("per_head_match_rates:")
    for idx, name in enumerate(HEAD_NAMES):
        print(f"  {name}: {stats.head_rate(idx):.4f} ({stats.head_matches[idx]}/{stats.head_totals[idx]})")
    print("counts_by_regime:")
    for regime in regimes:
        print(
            f"  {regime}: episodes={stats.regime_episodes[regime]} "
            f"steps={stats.regime_steps[regime]} "
            f"full_match_rate={stats.regime_rate(regime):.4f} "
            f"({stats.regime_full_matches[regime]}/{stats.regime_steps[regime]})"
        )
    print("per_regime_head_match_rates:")
    for regime in regimes:
        print(f"  {regime}:")
        for idx, name in enumerate(HEAD_NAMES):
            totals = stats.regime_head_totals.get(regime, np.zeros(len(HEAD_NAMES), dtype=np.int64))
            matches = stats.regime_head_matches.get(regime, np.zeros(len(HEAD_NAMES), dtype=np.int64))
            print(
                f"    {name}: {stats.regime_head_rate(regime, idx):.4f} "
                f"({matches[idx]}/{totals[idx]})"
            )
    if args.top_mismatches > 0:
        print("top_mismatches:")
        for regime in regimes:
            print(f"  {regime}:")
            regime_items = [
                (key, count)
                for key, count in stats.mismatch_pairs.items()
                if key[0] == regime
            ]
            for idx, name in enumerate(HEAD_NAMES):
                head_items = [
                    (teacher, student, count)
                    for (_regime, head, teacher, student), count in regime_items
                    if head == name
                ]
                if not head_items:
                    continue
                formatted = ", ".join(
                    f"{teacher}->{student}:{count}"
                    for teacher, student, count in sorted(
                        head_items,
                        key=lambda item: item[2],
                        reverse=True,
                    )[: args.top_mismatches]
                )
                print(f"    {name}: {formatted}")


def main() -> None:
    args = parse_args()
    if args.steps_per_seed <= 0:
        raise ValueError("--steps-per-seed must be positive")
    if args.seeds <= 0:
        raise ValueError("--seeds must be positive")

    model = load_model(args.model)
    regimes = selected_regimes(args.regime)
    stats = AgreementStats()

    for regime, weights in regimes.items():
        for seed in range(args.seeds):
            run_episode(
                model=model,
                stats=stats,
                regime=regime,
                weights=weights,
                seed=seed,
                steps_per_seed=args.steps_per_seed,
                weight_obs_scale=args.weight_obs_scale,
                include_time_feature=args.include_time_feature,
                include_time_phase=args.include_time_phase,
            )

    print_report(stats, regimes, args)


if __name__ == "__main__":
    main()
