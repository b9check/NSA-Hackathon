"""Measure baseline-policy action divergence across regime weight settings.

For matched starting states (and matched mid-game states), compare the actions
produced by the scripted baseline under three commander-intent regimes.
"""
from __future__ import annotations

import numpy as np

from baseline import predict_baseline_action
from rl_env import OverwatchEnv


REGIMES: dict[str, list[float]] = {
    "offensive":   [1.0, -0.2,  0.3, -0.8],
    "defensive":   [0.5, -1.0,  0.2,  0.0],
    "recon-heavy": [0.3, -0.3,  1.0, -0.2],
}

SLOT_NAMES = [
    "recon_move", "recon_pol",
    "strike_move", "strike_pol",
    "sam_move", "sam_pol",
    "radar",
]

PAIRS = [
    ("offensive", "defensive"),
    ("offensive", "recon-heavy"),
    ("defensive", "recon-heavy"),
]


def action_for(weights: list[float], seed: int) -> np.ndarray:
    env = OverwatchEnv()
    env.set_reward_weights(weights)
    env.reset(seed=seed)
    return np.asarray(predict_baseline_action(env), dtype=np.int64)


def reset_state_actions(seeds: list[int]) -> dict[str, np.ndarray]:
    out: dict[str, list[np.ndarray]] = {name: [] for name in REGIMES}
    for s in seeds:
        for name, w in REGIMES.items():
            out[name].append(action_for(w, s))
    return {k: np.stack(v) for k, v in out.items()}


def midgame_actions(seeds: list[int], rollout_steps: int = 5) -> dict[str, np.ndarray]:
    """For each seed, roll forward `rollout_steps` under offensive's own actions,
    then at that mid-game state, query each regime's action."""
    out: dict[str, list[np.ndarray]] = {name: [] for name in REGIMES}
    off_w = REGIMES["offensive"]
    for s in seeds:
        env = OverwatchEnv()
        env.set_reward_weights(off_w)
        env.reset(seed=s)
        terminated = truncated = False
        for _ in range(rollout_steps):
            if terminated or truncated:
                break
            a = predict_baseline_action(env)
            _, _, terminated, truncated, _ = env.step(a)
        # Now query each regime at this mid-game state.
        for name, w in REGIMES.items():
            env.set_reward_weights(w)
            # set_reward_weights only stores fixed_weights; the live w_* attrs
            # the policy reads are written by _set_episode_weights. Mirror it
            # here so the regime swap actually takes effect mid-episode.
            env.w_enemy, env.w_own, env.w_info, env.w_time = [float(v) for v in w]
            out[name].append(np.asarray(predict_baseline_action(env), dtype=np.int64))
    return {k: np.stack(v) for k, v in out.items()}


def divergence_table(actions: dict[str, np.ndarray]) -> str:
    n = next(iter(actions.values())).shape[0]
    header = f"| pair | " + " | ".join(SLOT_NAMES) + " | full_match | full_flip |"
    sep = "|" + "---|" * (len(SLOT_NAMES) + 3)
    lines = [header, sep]
    for a, b in PAIRS:
        A, B = actions[a], actions[b]
        per_slot_match = (A == B).mean(axis=0)
        full_match = float((A == B).all(axis=1).mean())
        full_flip = 1.0 - full_match
        slot_cells = " | ".join(f"{x:.3f}" for x in per_slot_match)
        lines.append(f"| {a} vs {b} | {slot_cells} | {full_match:.3f} | {full_flip:.3f} |")
    lines.append(f"\n(N={n} seeds; cells = per-slot agreement rate.)")
    return "\n".join(lines)


def main() -> None:
    reset_seeds = list(range(200))
    mid_seeds = list(range(50))

    print("== Reset-state divergence (N=200) ==")
    reset_acts = reset_state_actions(reset_seeds)
    reset_table = divergence_table(reset_acts)
    print(reset_table)

    print("\n== Mid-game divergence (N=50, 5 offensive steps) ==")
    mid_acts = midgame_actions(mid_seeds, rollout_steps=5)
    mid_table = divergence_table(mid_acts)
    print(mid_table)

    # Stash for the writeup script (printed; the wrapper captures stdout).
    with open("rl_alt/_demo_divergence_tables.txt", "w", encoding="utf-8") as f:
        f.write("RESET\n")
        f.write(reset_table)
        f.write("\n\nMIDGAME\n")
        f.write(mid_table)
        f.write("\n")


if __name__ == "__main__":
    main()
