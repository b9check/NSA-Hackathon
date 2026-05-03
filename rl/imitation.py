"""Behavior-clone the scripted baseline, then optionally PPO fine-tune."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor

try:
    from rl.baseline import predict_baseline_action
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/imitation.py` from repo root.
    from baseline import predict_baseline_action
    from rl_env import OverwatchEnv


REGIMES: tuple[tuple[str, list[float]], ...] = (
    ("offensive", [1.0, -0.2, 0.3, -0.8]),
    ("defensive", [0.5, -1.0, 0.2, 0.0]),
    ("recon-heavy", [0.3, -0.3, 1.0, -0.2]),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes-per-regime", type=int, default=120)
    parser.add_argument("--steps", type=int, default=35)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--ppo-timesteps", type=int, default=0)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--save-path", default="rl/overwatch_bc_agent")
    parser.add_argument("--log-dir", default="rl/logs/imitation")
    return parser.parse_args()


def collect_demos(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray]:
    observations: list[np.ndarray] = []
    actions: list[np.ndarray] = []
    episode_idx = 0
    for _name, weights in REGIMES:
        for _ in range(args.episodes_per_regime):
            env = OverwatchEnv(max_steps=args.steps)
            env.set_reward_weights(weights)
            obs, _info = env.reset(seed=args.seed + episode_idx)
            for _step in range(args.steps):
                action = predict_baseline_action(env)
                observations.append(obs.copy())
                actions.append(np.asarray(action, dtype=np.int64).copy())
                obs, _reward, terminated, truncated, _info = env.step(action)
                if terminated or truncated:
                    break
            episode_idx += 1
            env.close()
    return np.asarray(observations, dtype=np.float32), np.asarray(actions, dtype=np.int64)


def behavior_clone(
    model: PPO,
    observations: np.ndarray,
    actions: np.ndarray,
    *,
    epochs: int,
    batch_size: int,
    learning_rate: float,
) -> None:
    device = model.policy.device
    obs_tensor = torch.as_tensor(observations, dtype=torch.float32, device=device)
    action_tensor = torch.as_tensor(actions, dtype=torch.long, device=device)
    optimizer = torch.optim.Adam(model.policy.parameters(), lr=learning_rate)
    model.policy.train()

    n = len(observations)
    rng = np.random.default_rng(0)
    for epoch in range(1, epochs + 1):
        indices = rng.permutation(n)
        total_loss = 0.0
        total_items = 0
        for start in range(0, n, batch_size):
            batch_idx = indices[start:start + batch_size]
            dist = model.policy.get_distribution(obs_tensor[batch_idx])
            log_prob = dist.log_prob(action_tensor[batch_idx])
            entropy = dist.entropy().mean()
            loss = -log_prob.mean() - 0.001 * entropy
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.policy.parameters(), 0.5)
            optimizer.step()
            total_loss += float(loss.detach().cpu()) * len(batch_idx)
            total_items += len(batch_idx)
        print(f"bc_epoch={epoch} loss={total_loss / max(total_items, 1):.4f}")


def main() -> None:
    args = parse_args()
    Path(args.log_dir).mkdir(parents=True, exist_ok=True)
    observations, actions = collect_demos(args)
    print(f"collected observations={observations.shape} actions={actions.shape}")

    env = Monitor(OverwatchEnv(), filename=str(Path(args.log_dir) / "monitor.csv"))
    model = PPO("MlpPolicy", env, verbose=1, n_steps=2048, seed=args.seed)
    behavior_clone(
        model,
        observations,
        actions,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
    )
    if args.ppo_timesteps > 0:
        model.learn(total_timesteps=args.ppo_timesteps)
    model.save(args.save_path)
    env.close()


if __name__ == "__main__":
    main()
