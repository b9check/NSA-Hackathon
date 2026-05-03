"""Evaluate one trained policy under several commander intent regimes."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from stable_baselines3 import PPO

try:
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/eval.py` from repo root.
    from rl_env import OverwatchEnv


REGIMES: dict[str, list[float]] = {
    "offensive": [1.0, -0.2, 0.3, -0.8],
    "defensive": [0.5, -1.0, 0.2, 0.0],
    "recon-heavy": [0.3, -0.3, 1.0, -0.2],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="rl/overwatch_agent")
    parser.add_argument("--steps", type=int, default=75)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=11)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_path = Path(args.model)
    zip_path = model_path if model_path.suffix == ".zip" else model_path.with_suffix(".zip")
    if not zip_path.exists():
        raise FileNotFoundError(f"Missing trained model: {zip_path}")

    model = PPO.load(str(model_path))
    for name, weights in REGIMES.items():
        print("=" * 80)
        print(f"{name.upper()} weights={weights}")
        env = OverwatchEnv(max_steps=args.steps)
        env.set_reward_weights(weights)
        obs, info = env.reset(seed=args.seed)
        print(env.render())
        total_reward = 0.0
        for _ in range(args.steps):
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            print()
            print(env.render())
            print(f"reward={reward:.2f} total={total_reward:.2f}")
            if args.sleep > 0:
                time.sleep(args.sleep)
            if terminated or truncated:
                break
        env.close()


if __name__ == "__main__":
    main()
