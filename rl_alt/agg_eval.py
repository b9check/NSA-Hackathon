"""Aggregated multi-seed eval for the scripted baseline regimes."""
from __future__ import annotations

from statistics import mean, pstdev

from baseline import predict_baseline_action
from rl_env import OverwatchEnv


REGIMES: dict[str, list[float]] = {
    "offensive": [1.0, -0.2, 0.3, -0.8],
    "defensive": [0.5, -1.0, 0.2, 0.0],
    "recon-heavy": [0.3, -0.3, 1.0, -0.2],
}

NUM_SEEDS = 30
MAX_STEPS = 35

NUMERIC_METRICS = [
    "total_reward",
    "enemy_value_destroyed",
    "own_value_lost",
    "new_cells_revealed",
    "radar_emit_turns",
    "episode_len",
    "final_blue_value",
    "final_red_value",
]


def run_episode(weights: list[float], seed: int) -> dict:
    env = OverwatchEnv(max_steps=MAX_STEPS)
    env.set_reward_weights(weights)
    obs, info = env.reset(seed=seed)
    total_reward = 0.0
    enemy_destroyed = 0.0
    own_lost = 0.0
    new_cells = 0
    radar_emits = 0
    steps = 0
    terminated = False
    truncated = False
    last_info = info
    for step in range(1, MAX_STEPS + 1):
        action = predict_baseline_action(env)
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += float(reward)
        enemy_destroyed += float(info["enemy_value_destroyed"])
        own_lost += float(info["own_value_lost"])
        new_cells += int(info["new_cells_revealed"])
        if info["blue_sam_emit"]:
            radar_emits += 1
        steps = step
        last_info = info
        if terminated or truncated:
            break
    env.close()
    return {
        "total_reward": total_reward,
        "enemy_value_destroyed": enemy_destroyed,
        "own_value_lost": own_lost,
        "new_cells_revealed": float(new_cells),
        "radar_emit_turns": float(radar_emits),
        "episode_len": float(steps),
        "final_blue_value": float(last_info["blue_value"]),
        "final_red_value": float(last_info["red_value"]),
        "terminated_or_truncated": "terminated" if terminated else "truncated",
    }


def stats(values: list[float]) -> tuple[float, float, float, float]:
    return mean(values), pstdev(values), min(values), max(values)


def fmt(v: float) -> str:
    return f"{v:8.2f}"


def main() -> None:
    results: dict[str, list[dict]] = {name: [] for name in REGIMES}
    for name, weights in REGIMES.items():
        for s in range(NUM_SEEDS):
            results[name].append(run_episode(weights, s))

    # Per-regime detailed table
    for name in REGIMES:
        print("=" * 78)
        print(f"REGIME: {name}  weights={REGIMES[name]}  N={NUM_SEEDS}  max_steps={MAX_STEPS}")
        print("-" * 78)
        print(f"{'metric':<24}{'mean':>12}{'std':>12}{'min':>12}{'max':>12}")
        for m in NUMERIC_METRICS:
            vals = [r[m] for r in results[name]]
            mu, sd, mn, mx = stats(vals)
            print(f"{m:<24}{fmt(mu)}{fmt(sd)}{fmt(mn)}{fmt(mx)}")
        term = sum(1 for r in results[name] if r["terminated_or_truncated"] == "terminated")
        trunc = NUM_SEEDS - term
        print(f"{'terminated':<24}{term:>12}")
        print(f"{'truncated':<24}{trunc:>12}")
        print()

    # Side-by-side summary
    print("=" * 78)
    print("SIDE-BY-SIDE SUMMARY (mean +/- std)")
    print("-" * 78)
    headline = [
        "final_blue_value",
        "final_red_value",
        "total_reward",
        "enemy_value_destroyed",
        "own_value_lost",
        "new_cells_revealed",
        "radar_emit_turns",
    ]
    header = f"{'metric':<24}" + "".join(f"{name:>18}" for name in REGIMES)
    print(header)
    for m in headline:
        row = f"{m:<24}"
        for name in REGIMES:
            vals = [r[m] for r in results[name]]
            mu, sd, _, _ = stats(vals)
            row += f"{mu:>9.2f}+/-{sd:<6.2f}"
        print(row)
    print()

    # Termination summary
    print("=" * 78)
    print("TERMINATION COUNTS")
    print("-" * 78)
    print(f"{'regime':<24}{'terminated':>14}{'truncated':>14}{'survival_rate':>18}")
    for name in REGIMES:
        term = sum(1 for r in results[name] if r["terminated_or_truncated"] == "terminated")
        trunc = NUM_SEEDS - term
        # "survival" = truncated (made it to step limit)
        survival = trunc / NUM_SEEDS
        print(f"{name:<24}{term:>14}{trunc:>14}{survival:>17.1%}")


if __name__ == "__main__":
    main()
