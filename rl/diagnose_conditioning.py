"""Check whether a trained policy actually uses reward-weight inputs.

The diagnostic holds battlefield features fixed, swaps only obs[-4:] among the
commander-intent regimes, and compares the resulting MultiDiscrete action
distributions. If probabilities barely move, the policy is regime-blind.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import torch
from stable_baselines3 import PPO

try:
    from rl.baseline import predict_baseline_action
    from rl.eval import REGIMES, decode_action
    from rl.policy import RewardConditionedExtractor as _RewardConditionedExtractor
    from rl.rl_env import OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/diagnose_conditioning.py`.
    from baseline import predict_baseline_action
    from eval import REGIMES, decode_action
    from policy import RewardConditionedExtractor as _RewardConditionedExtractor
    from rl_env import OverwatchEnv


@dataclass
class Comparison:
    left: str
    right: str
    mean_prob_delta: float
    max_prob_delta: float
    argmax_flip_rate: float
    full_action_flip_rate: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="rl/overwatch_bc_200k")
    parser.add_argument("--states", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--steps-per-seed", type=int, default=20)
    parser.add_argument(
        "--collector",
        choices=["model", "baseline", "random"],
        default="model",
        help="Policy used to collect fixed battlefield states.",
    )
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
    return parser.parse_args()


def collect_states(args: argparse.Namespace, model: PPO) -> list[np.ndarray]:
    states: list[np.ndarray] = []
    for seed in range(args.seeds):
        env = OverwatchEnv(
            max_steps=args.steps_per_seed,
            weight_obs_scale=args.weight_obs_scale,
            include_time_feature=args.include_time_feature,
            include_time_phase=args.include_time_phase,
        )
        # Use neutral-ish weights while collecting states; the diagnostic swaps
        # the tail afterward.
        env.set_reward_weights([0.5, -0.5, 0.5, -0.2])
        obs, _info = env.reset(seed=seed)
        for _ in range(args.steps_per_seed):
            states.append(obs.copy())
            if len(states) >= args.states:
                env.close()
                return states
            if args.collector == "baseline":
                action = predict_baseline_action(env)
            elif args.collector == "random":
                action = env.action_space.sample()
            else:
                action, _ = model.predict(obs, deterministic=True)
            obs, _reward, terminated, truncated, _info = env.step(action)
            if terminated or truncated:
                break
        env.close()
    return states


def distribution_probs(model: PPO, obs: np.ndarray) -> list[np.ndarray]:
    obs_tensor = torch.as_tensor(obs[None], dtype=torch.float32, device=model.policy.device)
    with torch.no_grad():
        dist = model.policy.get_distribution(obs_tensor)
    return [
        categorical.probs.detach().cpu().numpy()[0]
        for categorical in dist.distribution
    ]


def argmax_action(probs: list[np.ndarray]) -> tuple[int, ...]:
    return tuple(int(np.argmax(head)) for head in probs)


def scaled_weights(weights: list[float], scale: float) -> np.ndarray:
    return np.asarray(weights, dtype=np.float32) * scale


def compare_regimes(model: PPO, states: list[np.ndarray], scale: float) -> list[Comparison]:
    regime_names = list(REGIMES)
    regime_probs: dict[str, list[list[np.ndarray]]] = {name: [] for name in regime_names}
    regime_argmax: dict[str, list[tuple[int, ...]]] = {name: [] for name in regime_names}

    for base_obs in states:
        for name, weights in REGIMES.items():
            obs = base_obs.copy()
            obs[-4:] = scaled_weights(weights, scale)
            probs = distribution_probs(model, obs)
            regime_probs[name].append(probs)
            regime_argmax[name].append(argmax_action(probs))

    comparisons: list[Comparison] = []
    for i, left in enumerate(regime_names):
        for right in regime_names[i + 1:]:
            per_head_deltas: list[float] = []
            max_delta = 0.0
            argmax_flips = 0
            full_action_flips = 0
            total_heads = 0
            for idx in range(len(states)):
                left_action = regime_argmax[left][idx]
                right_action = regime_argmax[right][idx]
                if left_action != right_action:
                    full_action_flips += 1
                for head_idx, (lp, rp) in enumerate(zip(regime_probs[left][idx], regime_probs[right][idx])):
                    delta = float(np.abs(lp - rp).mean())
                    per_head_deltas.append(delta)
                    max_delta = max(max_delta, float(np.abs(lp - rp).max()))
                    if left_action[head_idx] != right_action[head_idx]:
                        argmax_flips += 1
                    total_heads += 1
            comparisons.append(
                Comparison(
                    left=left,
                    right=right,
                    mean_prob_delta=float(np.mean(per_head_deltas)) if per_head_deltas else 0.0,
                    max_prob_delta=max_delta,
                    argmax_flip_rate=argmax_flips / max(total_heads, 1),
                    full_action_flip_rate=full_action_flips / max(len(states), 1),
                )
            )
    return comparisons


def print_initial_state_probe(
    model: PPO,
    scale: float,
    include_time_feature: bool,
    include_time_phase: bool,
) -> None:
    env = OverwatchEnv(
        weight_obs_scale=scale,
        include_time_feature=include_time_feature,
        include_time_phase=include_time_phase,
    )
    obs, _info = env.reset(seed=0)
    print("single_initial_state_probe:")
    for name, weights in REGIMES.items():
        test_obs = obs.copy()
        test_obs[-4:] = scaled_weights(weights, scale)
        probs = distribution_probs(model, test_obs)
        action = argmax_action(probs)
        decoded = decode_action(np.asarray(action, dtype=np.int64))
        first_heads = " ".join(
            f"h{idx}={np.round(head, 3).tolist()}"
            for idx, head in enumerate(probs)
        )
        print(f"  {name}: argmax={decoded} {first_heads}")
    env.close()


def main() -> None:
    args = parse_args()
    model = PPO.load(args.model)
    print(f"weight_obs_scale={args.weight_obs_scale}")
    print(f"include_time_feature={args.include_time_feature}")
    print(f"include_time_phase={args.include_time_phase}")
    print_initial_state_probe(
        model,
        args.weight_obs_scale,
        args.include_time_feature,
        args.include_time_phase,
    )
    states = collect_states(args, model)
    print(f"\ncollected_states={len(states)} collector={args.collector}")
    for comparison in compare_regimes(model, states, args.weight_obs_scale):
        print(
            f"{comparison.left} vs {comparison.right}: "
            f"mean_prob_delta={comparison.mean_prob_delta:.6f} "
            f"max_prob_delta={comparison.max_prob_delta:.6f} "
            f"argmax_flip_rate={comparison.argmax_flip_rate:.3f} "
            f"full_action_flip_rate={comparison.full_action_flip_rate:.3f}"
        )


if __name__ == "__main__":
    main()
