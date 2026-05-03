# Toy Reward-Conditioned RL Demo

This directory contains the standalone Gymnasium wargame demo. It is separate
from Alex's engine and from `rl_alt/`.

## Current Best Model

Use this learned policy for the toy demo:

```text
rl/overwatch_conditioned_bc_scale3_time_balanced
```

It uses:

- `weight_obs_scale=3`
- `include_time_feature=True`
- no time phase feature
- conditioned SB3 feature extractor from `rl/policy.py`

`rl/eval.py` now defaults to this checkpoint and applies the matching
observation settings automatically.

## Live Terminal Demo

Run all three commander intents:

```powershell
.\.venv\Scripts\python rl\eval.py --steps 35
```

Run one intent:

```powershell
.\.venv\Scripts\python rl\eval.py --steps 35 --regime offensive
.\.venv\Scripts\python rl\eval.py --steps 35 --regime defensive
.\.venv\Scripts\python rl\eval.py --steps 35 --regime recon-heavy
```

Compare against the scripted baseline:

```powershell
.\.venv\Scripts\python rl\eval.py --policy baseline --steps 35
```

## Behavior-First Aggregate Eval

Compact baseline vs best learned model:

```powershell
.\.venv\Scripts\python rl\demo_compare.py --seeds 30 --steps 35
```

Full default comparison table:

```powershell
.\.venv\Scripts\python rl\aggregate_eval.py --seeds 30 --steps 35
```

The aggregate table intentionally leads with behavior metrics:

```text
final_blue, final_red, enemy_destroyed, own_lost, new_cells, radar_emits,
episode_length, total_reward
```

Total reward is useful but noisy; do not use it as the only headline metric.

## Key Finding

The toy model is demo-ready but not better than the scripted teacher in every
regime.

N=30 summary:

```text
baseline offensive:       enemy 26.47, own 15.27, final 31.73/20.53
time_balanced offensive:  enemy 28.03, own 15.10, final 31.90/18.97

baseline recon-heavy:      enemy 15.13, own 10.43, final 36.57/31.87
time_balanced recon-heavy: enemy 11.67, own 15.07, final 31.93/35.33
```

Interpretation:

- The learned model is strongest in offensive behavior.
- Defensive is usable.
- Recon-heavy remains below the scripted baseline on force preservation.
- PPO fine-tuning at default settings degraded the clone and should stay off
  unless KL/checkpoint safeguards are added.

## Training Notes

Best learned model command:

```powershell
.\.venv\Scripts\python rl\imitation.py `
  --offensive-episodes 500 `
  --defensive-episodes 500 `
  --recon-heavy-episodes 500 `
  --steps 35 `
  --epochs 30 `
  --batch-size 512 `
  --learning-rate 0.0003 `
  --ppo-timesteps 0 `
  --save-path rl\overwatch_conditioned_bc_scale3_time_balanced `
  --log-dir rl\logs\conditioned_bc_scale3_time_balanced `
  --weight-obs-scale 3 `
  --policy-arch conditioned `
  --include-time-feature
```

Do not use PPO fine-tuning as a default next step. The last 50k PPO attempt
reduced teacher agreement from 76.67% to 13.62%.

## Bridge Status

`rl_alt/` is Claude's engine-bridge workspace. Do not delete or overwrite it.
After the latest `main` merge, the bridge needs a v2 refresh because objectives
were removed and STRIKE became hex-targeted. The toy demo in `rl/` is unaffected.
