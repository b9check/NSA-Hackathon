"""Train the reward-conditioned PPO agent."""
from __future__ import annotations

import argparse
from pathlib import Path

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.monitor import Monitor

try:
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/train.py` from repo root.
    from rl_env import OverwatchEnv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=500_000)
    parser.add_argument("--save-path", default="rl/overwatch_agent")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--log-dir", default="rl/logs")
    parser.add_argument("--checkpoint-freq", type=int, default=50_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    Path(args.log_dir).mkdir(parents=True, exist_ok=True)
    env = Monitor(OverwatchEnv(), filename=str(Path(args.log_dir) / "monitor.csv"))
    model = PPO("MlpPolicy", env, verbose=1, n_steps=2048, seed=args.seed)
    callback = None
    if args.checkpoint_freq > 0:
        checkpoint_dir = Path(args.log_dir) / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        callback = CheckpointCallback(
            save_freq=args.checkpoint_freq,
            save_path=str(checkpoint_dir),
            name_prefix="overwatch_agent",
        )
    model.learn(total_timesteps=args.timesteps, callback=callback)
    model.save(args.save_path)


if __name__ == "__main__":
    main()
