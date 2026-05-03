"""Behavior-clone the scripted baseline, then optionally PPO fine-tune."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor

try:
    from rl.baseline import predict_baseline_action
    from rl.policy import policy_kwargs_for_arch
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/imitation.py` from repo root.
    from baseline import predict_baseline_action
    from policy import policy_kwargs_for_arch
    from rl_env import OverwatchEnv


REGIMES: tuple[tuple[str, list[float]], ...] = (
    ("offensive", [1.0, -0.2, 0.3, -0.8]),
    ("defensive", [0.5, -1.0, 0.2, 0.0]),
    ("recon-heavy", [0.3, -0.3, 1.0, -0.2]),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes-per-regime", type=int, default=120)
    parser.add_argument("--offensive-episodes", type=int, default=None)
    parser.add_argument("--defensive-episodes", type=int, default=None)
    parser.add_argument("--recon-heavy-episodes", type=int, default=None)
    parser.add_argument("--steps", type=int, default=35)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument(
        "--head-loss-weights",
        default="1,1,1,1,1,1,1",
        help="Comma-separated weights for recon move/policy, strike move/policy, SAM move/policy, radar.",
    )
    parser.add_argument("--ppo-timesteps", type=int, default=0)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--save-path", default="rl/overwatch_bc_agent")
    parser.add_argument("--log-dir", default="rl/logs/imitation")
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
        "--policy-arch",
        choices=["mlp", "conditioned"],
        default="mlp",
        help="Use plain MlpPolicy features or a separate reward-weight encoder.",
    )
    return parser.parse_args()


def parse_head_loss_weights(raw: str) -> np.ndarray:
    weights = np.asarray([float(part.strip()) for part in raw.split(",")], dtype=np.float32)
    if weights.shape != (7,):
        raise ValueError("--head-loss-weights must contain exactly 7 comma-separated values")
    if np.any(weights <= 0):
        raise ValueError("--head-loss-weights values must be positive")
    return weights


def episodes_for_regime(args: argparse.Namespace, regime_name: str) -> int:
    override = getattr(args, f"{regime_name.replace('-', '_')}_episodes")
    if override is not None:
        if override < 0:
            raise ValueError(f"{regime_name} episode override must be non-negative")
        return int(override)
    return args.episodes_per_regime


def collect_demos(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray]:
    observations: list[np.ndarray] = []
    actions: list[np.ndarray] = []
    episode_idx = 0
    for name, weights in REGIMES:
        for _ in range(episodes_for_regime(args, name)):
            env = OverwatchEnv(
                max_steps=args.steps,
                weight_obs_scale=args.weight_obs_scale,
                include_time_feature=args.include_time_feature,
                include_time_phase=args.include_time_phase,
            )
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
    head_loss_weights: np.ndarray,
) -> None:
    device = model.policy.device
    obs_tensor = torch.as_tensor(observations, dtype=torch.float32, device=device)
    action_tensor = torch.as_tensor(actions, dtype=torch.long, device=device)
    head_weights = torch.as_tensor(head_loss_weights, dtype=torch.float32, device=device)
    head_weight_norm = torch.clamp(head_weights.sum(), min=1.0)
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
            per_head_losses = []
            for head_idx, categorical in enumerate(dist.distribution):
                per_head_losses.append(
                    F.cross_entropy(
                        categorical.logits,
                        action_tensor[batch_idx, head_idx],
                        reduction="none",
                    )
                    * head_weights[head_idx]
                )
            action_loss = torch.stack(per_head_losses, dim=1).sum(dim=1) / head_weight_norm
            entropy = dist.entropy().mean()
            loss = action_loss.mean() - 0.001 * entropy
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
    head_loss_weights = parse_head_loss_weights(args.head_loss_weights)
    observations, actions = collect_demos(args)
    print(f"collected observations={observations.shape} actions={actions.shape}")
    print(f"head_loss_weights={head_loss_weights.tolist()}")

    env = Monitor(
        OverwatchEnv(
            weight_obs_scale=args.weight_obs_scale,
            include_time_feature=args.include_time_feature,
            include_time_phase=args.include_time_phase,
        ),
        filename=str(Path(args.log_dir) / "monitor.csv"),
    )
    policy_kwargs = policy_kwargs_for_arch(args.policy_arch)
    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        n_steps=2048,
        seed=args.seed,
        policy_kwargs=policy_kwargs,
    )
    behavior_clone(
        model,
        observations,
        actions,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        head_loss_weights=head_loss_weights,
    )
    if args.ppo_timesteps > 0:
        model.learn(total_timesteps=args.ppo_timesteps)
    model.save(args.save_path)
    env.close()


if __name__ == "__main__":
    main()
