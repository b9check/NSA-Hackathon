"""Smoke test for rl_alt.engine_env.EngineEnv."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# allow running as a script from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl_alt.engine_env import EngineEnv, MAX_UNITS_PER_SIDE, N_ACTIONS


def sample_action(env: EngineEnv, rng: np.random.Generator) -> np.ndarray:
    masks = env.valid_action_mask()
    out = np.zeros(MAX_UNITS_PER_SIDE, dtype=np.int64)
    for i, m in enumerate(masks):
        legal = np.flatnonzero(m)
        if len(legal) == 0:
            out[i] = 0
        else:
            out[i] = int(rng.choice(legal))
    return out


def main() -> int:
    env = EngineEnv(scenario_path=str(ROOT / "scenarios" / "strait_n7.yaml"))
    print("action_space:", env.action_space)
    print("obs_space keys:", list(env.observation_space.spaces.keys()))
    for k, sp in env.observation_space.spaces.items():
        print(f"  {k}: shape={sp.shape} dtype={sp.dtype}")

    obs, info = env.reset(seed=42)
    print("\nreset() ok. obs keys:", list(obs.keys()))
    for k, v in obs.items():
        print(f"  {k}: shape={v.shape} dtype={v.dtype}")
    print("sample unit feature row[0]:", obs["units"][0])
    print("initial info:", {k: v for k, v in info.items() if k != "events"})

    rng = np.random.default_rng(0)
    print("\n--- 10 random valid steps ---")
    for t in range(10):
        a = sample_action(env, rng)
        obs, reward, terminated, truncated, info = env.step(a)
        print(
            f"step {t}: reward={reward:+.2f} term={terminated} trunc={truncated} "
            f"turn={info['turn']} blue={info['blue_score']:.1f} red={info['red_score']:.1f} "
            f"events={len(info['events'])}"
        )
        if terminated or truncated:
            print("  -> episode end")
            break

    # episode budget assertion: 50 steps must terminate or truncate
    obs, _ = env.reset(seed=7)
    ended = False
    budget = env.max_turns + 5
    for t in range(50):
        a = sample_action(env, rng)
        obs, reward, terminated, truncated, info = env.step(a)
        if terminated or truncated:
            ended = True
            print(f"\nepisode ended at step {t} (turn={info['turn']}, "
                  f"term={terminated}, trunc={truncated})")
            break
    assert ended, f"episode did not end within 50 steps (budget={budget})"

    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
