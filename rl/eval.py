"""Evaluate one trained policy under several commander intent regimes."""
from __future__ import annotations

import argparse
import time
from collections import Counter
from pathlib import Path

import numpy as np
from stable_baselines3 import PPO

try:
    from rl.baseline import predict_baseline_action
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/eval.py` from repo root.
    from baseline import predict_baseline_action
    from rl_env import OverwatchEnv


REGIMES: dict[str, list[float]] = {
    "offensive": [1.0, -0.2, 0.3, -0.8],
    "defensive": [0.5, -1.0, 0.2, 0.0],
    "recon-heavy": [0.3, -0.3, 1.0, -0.2],
}

MOVE_NAMES = {
    0: "hold",
    1: "north",
    2: "south",
    3: "west",
    4: "east",
}

POLICY_NAMES = {
    0: "strike",
    1: "surveil",
    2: "retreat",
}

UNIT_ORDER = ("recon", "strike", "sam")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="rl/overwatch_agent")
    parser.add_argument(
        "--policy",
        choices=["ppo", "baseline"],
        default="ppo",
        help="Evaluate the trained PPO policy or the scripted intent baseline.",
    )
    parser.add_argument("--steps", type=int, default=75)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--weight-obs-scale", type=float, default=1.0)
    parser.add_argument(
        "--regime",
        choices=["all", *REGIMES.keys()],
        default="all",
        help="Run all regimes or just one commander intent.",
    )
    parser.add_argument(
        "--stochastic",
        action="store_true",
        help="Sample from the policy instead of using deterministic actions.",
    )
    return parser.parse_args()


def decode_action(action: np.ndarray | list[int]) -> dict[str, str]:
    values = [int(v) for v in np.asarray(action).tolist()]
    decoded: dict[str, str] = {}
    for idx, unit in enumerate(UNIT_ORDER):
        move = MOVE_NAMES[values[idx * 2]]
        policy = POLICY_NAMES[values[idx * 2 + 1]]
        decoded[unit] = f"{move}/{policy}"
    decoded["radar"] = "emit" if values[6] else "silent"
    return decoded


def format_action(action: np.ndarray | list[int]) -> str:
    decoded = decode_action(action)
    return (
        "action: "
        f"recon={decoded['recon']}  "
        f"strike={decoded['strike']}  "
        f"sam={decoded['sam']}  "
        f"radar={decoded['radar']}"
    )


def format_events(events: list[dict]) -> str:
    if not events:
        return "events: none"
    return "events: " + ", ".join(
        f"{event['attacker']}->{event['target']}:{'hit' if event['hit'] else 'miss'}"
        + (" destroyed" if event["destroyed"] else "")
        for event in events
    )


def render_board(env: OverwatchEnv) -> str:
    """Render the board without the env's compact event footer.

    Eval prints decoded combat events separately so the action/reward timeline
    is easier to scan.
    """
    return "\n".join(
        line for line in str(env.render()).splitlines()
        if not line.startswith("events: ")
    )


def selected_regimes(name: str) -> dict[str, list[float]]:
    if name == "all":
        return REGIMES
    return {name: REGIMES[name]}


def main() -> None:
    args = parse_args()
    model = None
    if args.policy == "ppo":
        model_path = Path(args.model)
        zip_path = model_path if model_path.suffix == ".zip" else model_path.with_suffix(".zip")
        if not zip_path.exists():
            raise FileNotFoundError(f"Missing trained model: {zip_path}")
        model = PPO.load(str(model_path))

    for name, weights in selected_regimes(args.regime).items():
        print("=" * 80)
        print(f"{name.upper()} policy={args.policy} weights={weights}")
        env = OverwatchEnv(
            max_steps=args.steps,
            weight_obs_scale=args.weight_obs_scale,
        )
        env.set_reward_weights(weights)
        obs, info = env.reset(seed=args.seed)
        start_info = info
        print(render_board(env))

        total_reward = 0.0
        totals = Counter()
        action_counts: Counter[str] = Counter()
        final_reason = "step_limit"

        for step in range(1, args.steps + 1):
            if args.policy == "baseline":
                action = predict_baseline_action(env)
            else:
                assert model is not None
                action, _ = model.predict(obs, deterministic=not args.stochastic)
            decoded = decode_action(action)
            for unit in UNIT_ORDER:
                action_counts[f"{unit}:{decoded[unit]}"] += 1
            action_counts[f"radar:{decoded['radar']}"] += 1

            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            totals["enemy_value_destroyed"] += info["enemy_value_destroyed"]
            totals["own_value_lost"] += info["own_value_lost"]
            totals["new_cells_revealed"] += info["new_cells_revealed"]
            totals["radar_emit_turns"] += 1 if info["blue_sam_emit"] else 0

            print()
            print(f"step={step}")
            print(format_action(action))
            print(render_board(env))
            print(format_events(info["combat_events"]))
            print(f"reward={reward:.2f} total={total_reward:.2f}")

            if args.sleep > 0:
                time.sleep(args.sleep)
            if terminated or truncated:
                final_reason = "terminated" if terminated else "truncated"
                break

        print()
        print("summary:")
        print(f"  reason: {final_reason}")
        print(f"  start blue/red value: {start_info['blue_value']:.1f}/{start_info['red_value']:.1f}")
        print(f"  final blue/red value: {info['blue_value']:.1f}/{info['red_value']:.1f}")
        print(f"  total reward: {total_reward:.2f}")
        print(f"  enemy value destroyed: {totals['enemy_value_destroyed']:.1f}")
        print(f"  own value lost: {totals['own_value_lost']:.1f}")
        print(f"  new cells revealed: {totals['new_cells_revealed']:.0f}")
        print(f"  radar emit turns: {int(totals['radar_emit_turns'])}")
        print("  most common actions:")
        for action_name, count in action_counts.most_common(6):
            print(f"    {action_name}: {count}")
        env.close()


if __name__ == "__main__":
    main()
