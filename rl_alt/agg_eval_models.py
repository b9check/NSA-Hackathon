"""Aggregated multi-seed eval comparing scripted baseline vs trained PPO students."""
from __future__ import annotations

import os
import sys
from statistics import mean, pstdev

from baseline import predict_baseline_action
from rl_env import OverwatchEnv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

HEADLINE_METRICS = [
    "final_blue_value",
    "final_red_value",
    "enemy_value_destroyed",
    "own_value_lost",
    "radar_emit_turns",
]

POLICIES = [
    {
        "name": "baseline",
        "kind": "scripted",
        "scale": 1.0,
        "model_path": None,
    },
    {
        "name": "bc_200k",
        "kind": "model",
        "scale": 1.0,
        "model_path": os.path.join(REPO_ROOT, "rl", "overwatch_bc_200k.zip"),
    },
    {
        "name": "scale10_teacher_100k",
        "kind": "model",
        "scale": 10.0,
        "model_path": os.path.join(REPO_ROOT, "rl", "overwatch_bc_scale10_teacher_100k.zip"),
    },
]


def load_model(path: str):
    from stable_baselines3 import PPO  # type: ignore
    return PPO.load(path, device="cpu")


def run_episode_scripted(weights: list[float], seed: int, scale: float) -> dict:
    env = OverwatchEnv(max_steps=MAX_STEPS, weight_obs_scale=scale)
    env.set_reward_weights(weights)
    obs, info = env.reset(seed=seed)
    return _rollout(env, info, lambda o, e: predict_baseline_action(e))


def run_episode_model(weights: list[float], seed: int, scale: float, model) -> dict:
    env = OverwatchEnv(max_steps=MAX_STEPS, weight_obs_scale=scale)
    env.set_reward_weights(weights)
    obs, info = env.reset(seed=seed)
    return _rollout(env, info, lambda o, e: model.predict(o, deterministic=True)[0], initial_obs=obs)


def _rollout(env, info, action_fn, initial_obs=None) -> dict:
    obs = initial_obs
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
        action = action_fn(obs, env)
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


def run_all() -> dict[str, dict[str, list[dict]]]:
    """results[policy_name][regime_name] -> list of episode dicts."""
    results: dict[str, dict[str, list[dict]]] = {}
    for pol in POLICIES:
        name = pol["name"]
        results[name] = {r: [] for r in REGIMES}
        model = None
        if pol["kind"] == "model":
            try:
                model = load_model(pol["model_path"])
                print(f"[load] {name} <- {pol['model_path']}", file=sys.stderr)
            except Exception as exc:  # pragma: no cover
                print(f"[load-fail] {name}: {exc}", file=sys.stderr)
                results[name] = None  # type: ignore
                continue
        for reg, weights in REGIMES.items():
            for s in range(NUM_SEEDS):
                if pol["kind"] == "scripted":
                    ep = run_episode_scripted(weights, s, pol["scale"])
                else:
                    ep = run_episode_model(weights, s, pol["scale"], model)
                results[name][reg].append(ep)
            print(f"[done] {name} / {reg}", file=sys.stderr)
    return results


def print_per_policy_tables(results) -> None:
    for pol in POLICIES:
        name = pol["name"]
        print("#" * 78)
        print(f"POLICY: {name}  scale={pol['scale']}  model={pol['model_path']}")
        print("#" * 78)
        if results.get(name) is None:
            print("  (failed to load — skipped)")
            print()
            continue
        for reg in REGIMES:
            print("=" * 78)
            print(f"REGIME: {reg}  weights={REGIMES[reg]}  N={NUM_SEEDS}  max_steps={MAX_STEPS}")
            print("-" * 78)
            print(f"{'metric':<24}{'mean':>12}{'std':>12}{'min':>12}{'max':>12}")
            eps = results[name][reg]
            for m in NUMERIC_METRICS:
                vals = [r[m] for r in eps]
                mu, sd, mn, mx = stats(vals)
                print(f"{m:<24}{fmt(mu)}{fmt(sd)}{fmt(mn)}{fmt(mx)}")
            term = sum(1 for r in eps if r["terminated_or_truncated"] == "terminated")
            trunc = NUM_SEEDS - term
            print(f"{'terminated':<24}{term:>12}")
            print(f"{'truncated':<24}{trunc:>12}")
            print()


def print_gap_tables(results) -> None:
    print("=" * 78)
    print("GAP TABLES (student - baseline) per regime")
    print("=" * 78)
    pol_names = [p["name"] for p in POLICIES]
    students = [p["name"] for p in POLICIES if p["kind"] == "model"]
    for m in HEADLINE_METRICS:
        print(f"\nMetric: {m}")
        header = f"{'regime':<14}"
        for n in pol_names:
            header += f"{n:>22}"
        for s in students:
            header += f"{('gap('+s+')'):>26}"
        print(header)
        for reg in REGIMES:
            row = f"{reg:<14}"
            base_mu = None
            mus: dict[str, float | None] = {}
            for n in pol_names:
                if results.get(n) is None:
                    mus[n] = None
                    row += f"{'N/A':>22}"
                    continue
                vals = [r[m] for r in results[n][reg]]
                mu = mean(vals)
                mus[n] = mu
                row += f"{mu:>22.2f}"
                if n == "baseline":
                    base_mu = mu
            for s in students:
                if mus.get(s) is None or base_mu is None:
                    row += f"{'N/A':>26}"
                else:
                    row += f"{(mus[s] - base_mu):>26.2f}"
            print(row)


def print_termination(results) -> None:
    print()
    print("=" * 78)
    print("TERMINATION COUNTS (terminated = early death/wipe; truncated = reached step cap)")
    print("=" * 78)
    print(f"{'policy':<24}{'regime':<14}{'terminated':>14}{'truncated':>14}{'survival_rate':>18}")
    for pol in POLICIES:
        name = pol["name"]
        if results.get(name) is None:
            continue
        for reg in REGIMES:
            eps = results[name][reg]
            term = sum(1 for r in eps if r["terminated_or_truncated"] == "terminated")
            trunc = NUM_SEEDS - term
            survival = trunc / NUM_SEEDS
            print(f"{name:<24}{reg:<14}{term:>14}{trunc:>14}{survival:>17.1%}")


def main() -> None:
    results = run_all()
    print_per_policy_tables(results)
    print_gap_tables(results)
    print_termination(results)


if __name__ == "__main__":
    main()
