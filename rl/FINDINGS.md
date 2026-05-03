# RL Findings Log

This file records the current state of the standalone reward-conditioned RL demo.
It intentionally covers findings from earlier runs so the next iteration does not
repeat already answered questions.

## Current Architecture

- `rl/rl_env.py` implements a standalone `OverwatchEnv`; it does not depend on
  Alex's hex engine or the frontend.
- The world is a 10x10 orthogonal grid with 3 blue units and 3 red units:
  recon drone, strike drone, SAM battery.
- Action space is `MultiDiscrete([5, 3, 5, 3, 5, 3, 2])`:
  movement/contact policy for recon, strike, and SAM, plus SAM radar
  emit/silent.
- Observation is a flat vector for `MlpPolicy`: 6 grid channels * 10 * 10
  plus 4 reward weights.
- Fog of war is real: blue sees only fused sensor output from visible cells,
  not ground truth.
- Reward weights are part of the observation. `weight_obs_scale` can amplify
  the four weight features in the observation tail.
- Combat resolves after both sides move and visibility updates, so strike
  decisions are based on contacts detected this step.
- Red opponent is scripted: recon moves noisily, strike attacks visible blue
  targets, SAM holds defensively.

## Reward Model

Base reward:

```text
w_enemy * enemy_value_destroyed
+ w_own   * own_value_lost
+ w_info  * new_cells_revealed
+ w_time  * 1.0
```

Training samples random weights on reset:

```text
w_enemy in [0.2, 1.0]
w_own   in [-1.0, -0.1]
w_info  in [0.0, 0.5]
w_time  in [-1.0, 0.0]
```

Evaluation regimes:

```text
offensive   = [1.0, -0.2, 0.3, -0.8]
defensive   = [0.5, -1.0, 0.2, 0.0]
recon-heavy = [0.3, -0.3, 1.0, -0.2]
```

Additional shaping currently exists for HVT kills/losses, red strike drone
destruction, keeping strike/SAM near blue HVTs, and radar exposure.

## Models Run So Far

- `rl/overwatch_agent.zip`: original PPO-only model.
- `rl/overwatch_bc_100k.zip`: behavior cloning plus small PPO fine-tune.
- `rl/overwatch_bc_200k.zip`: second small PPO fine-tune at weight scale 1.
- `rl/overwatch_bc_scale10_100k.zip`: scale-10 experiment before the improved
  baseline was fully integrated.
- `rl/overwatch_bc_scale10_teacher_100k.zip`: scale-10 BC plus 100k PPO after
  improving the scripted baseline.

Generated model zip files and logs are intentionally ignored by git.

## Findings So Far

1. The environment works as a standalone Gymnasium loop.
2. The terminal eval path works and shows actions, fog, events, rewards, and
   end-of-episode summaries.
3. The scripted baseline can express distinct commander intents:
   offensive, defensive, and recon-heavy behavior visibly differ.
4. PPO from scratch was not enough under the current compute budget.
5. Behavior cloning helped, but the scale-1 learned policy still produced very
   similar outcomes across regimes.
6. Conditioning diagnostics showed the scale-1 BC policy was not completely
   regime-blind, but weight influence was weak.
7. Increasing `weight_obs_scale` to 10 made action probabilities highly
   regime-sensitive.
8. The scale-10 student became more conditioned but tactically worse in the
   single-seed eval. It appeared to learn brittle regime templates instead of
   robust state-dependent tactics.
9. Reward shaping alone is not the right next lever until we know whether the
   learned policy is failing to imitate the teacher or failing after PPO
   fine-tuning.

## Key Numbers From Prior Runs

Scripted baseline, 35-step single seed:

```text
offensive:   final blue/red 32/20, enemy destroyed 27, own lost 15, radar emits 35
defensive:   final blue/red 47/32, enemy destroyed 15, own lost 0,  radar emits 2
recon-heavy: final blue/red 37/32, enemy destroyed 15, own lost 10, radar emits 13
```

Scale-1 `overwatch_bc_200k`, 35-step single seed:

```text
offensive:   final blue/red 20/42, enemy destroyed 5, own lost 27, radar emits 0
defensive:   final blue/red 20/42, enemy destroyed 5, own lost 27, radar emits 0
recon-heavy: final blue/red 20/42, enemy destroyed 5, own lost 27, radar emits 0
```

Scale-10 `overwatch_bc_scale10_teacher_100k`, 35-step single seed:

```text
offensive:   final blue/red 13/42, enemy destroyed 5, own lost 34, radar emits 35
defensive:   final blue/red 8/47,  enemy destroyed 0, own lost 39, radar emits 0
recon-heavy: final blue/red 8/42,  enemy destroyed 5, own lost 39, radar emits 7
```

Scale-10 improved conditioning, but did not improve tactical performance.

## Current Diagnosis

The most likely bottleneck is not the environment's ability to express intent.
The baseline proves it can. The bottleneck is now one of:

- BC fidelity: the student may not be accurately imitating the scripted teacher.
- Architecture/capacity: a flat MLP may not preserve both state dependence and
  reward conditioning well enough.
- PPO fine-tuning dynamics: fine-tuning may be degrading the cloned policy.
- Eval variance: the single-seed results may overstate or understate
  performance.

## Diagnostics Added

- `rl/aggregate_eval.py` runs compact multi-seed evals for baseline and PPO
  models and can write per-episode CSV rows to `rl/logs/aggregate_eval.csv`.
- `rl/action_agreement.py` rolls out the scripted teacher and compares each
  teacher action with a deterministic PPO student's action on the same
  observation.

Both scripts are standalone and do not touch `rl_alt/`.

## 2026-05-02 Diagnostic Results

Aggregate eval, 20 seeds, 35 steps:

```text
baseline defensive:    reward  8.84, enemy destroyed 12.25, own lost 10.00, final 37.00/34.75
baseline offensive:    reward 14.96, enemy destroyed 26.60, own lost 15.20, final 31.80/20.40
baseline recon-heavy:  reward 42.92, enemy destroyed 14.70, own lost 10.75, final 36.25/32.30

bc_200k defensive:     reward -1.88, enemy destroyed 12.55, own lost 26.40, final 20.60/34.45
bc_200k offensive:     reward 20.82, enemy destroyed 13.40, own lost 26.40, final 20.60/33.60
bc_200k recon-heavy:   reward 49.59, enemy destroyed 13.40, own lost 26.40, final 20.60/33.60

scale10 defensive:     reward -28.63, enemy destroyed  2.85, own lost 32.25, final 14.75/44.15
scale10 offensive:     reward  -3.32, enemy destroyed  6.20, own lost 30.35, final 16.65/40.80
scale10 recon-heavy:   reward  30.81, enemy destroyed  0.50, own lost 32.80, final 14.20/46.50
```

Interpretation:

- The baseline remains the best tactical performer and shows distinct regime
  behavior.
- `bc_200k` still collapses to very similar tactical outcomes across regimes.
- `scale10` reads the weights more strongly but performs worse than `bc_200k`
  and much worse than the baseline.
- This confirms the issue is not merely single-seed noise.

Teacher-student action agreement, 20 seeds, 35 steps:

```text
bc_200k full action match: 3.62%
  recon_move 56.95%, recon_policy 83.62%
  strike_move 36.24%, strike_policy 37.76%
  sam_move 60.24%, sam_policy 90.52%
  radar_emit 54.33%
  offensive full match 0.00%, defensive 2.29%, recon-heavy 8.57%

scale10 full action match: 43.38%
  recon_move 73.76%, recon_policy 96.95%
  strike_move 82.95%, strike_policy 80.52%
  sam_move 76.24%, sam_policy 90.52%
  radar_emit 82.62%
  offensive full match 46.43%, defensive 49.43%, recon-heavy 34.29%
```

Interpretation:

- `bc_200k` is not imitating the teacher. The worst gaps are strike movement,
  strike policy, and radar.
- `scale10` imitates the teacher much better than scale 1, but 43% full-action
  agreement is still not enough for a brittle tactical policy.
- Recon-heavy remains the weakest regime for full-action agreement.

Conditioning probe on fixed baseline-collected states:

```text
bc_200k scale 1:
  offensive vs defensive   full-action flip 57%, mean prob delta 0.043
  offensive vs recon-heavy full-action flip 39%, mean prob delta 0.020
  defensive vs recon-heavy full-action flip 43%, mean prob delta 0.027

scale10:
  offensive vs defensive   full-action flip 100%, mean prob delta 0.259
  offensive vs recon-heavy full-action flip 100%, mean prob delta 0.148
  defensive vs recon-heavy full-action flip 49%,  mean prob delta 0.132
```

Interpretation:

- Scale 1 is not fully blind, but the probability movement is small.
- Scale 10 strongly changes actions when only the reward-weight tail changes.
- The remaining problem is preserving state-responsive tactical competence
  while making the weights visible to the policy.

## Immediate Diagnostics / Decisions

Run aggregate eval before another training run:

- Compare baseline, scale-1 student, and scale-10 student across multiple seeds.
- Summarize reward, enemy destroyed, own lost, new cells, radar emits, episode
  length, and final blue/red value.

Run teacher-student action agreement:

- On matched states, compare scripted baseline actions to student actions.
- Report full action match rate and per-head match rates.
- If agreement is low, fix BC/capacity/feature conditioning before more PPO.
- If agreement is high but outcomes are bad, inspect PPO fine-tuning and env
  dynamics.

Current decision from the 20-seed diagnostics:

- Do not spend the next compute cycle on reward shaping.
- Do not continue with `weight_obs_scale=10` as the main fix.
- Next best change is a SB3-compatible custom feature extractor that gives the
  grid/state and reward weights separate encoders, then fuses them before the
  policy/value heads.
- In parallel, try moderate scales like 2 or 3 and validate with
  `action_agreement.py` before PPO fine-tuning.

## 2026-05-02 Follow-Up Training

Implemented `rl/policy.py` with `RewardConditionedExtractor`:

- State/grid features and reward weights are encoded by separate MLP towers.
- The fused feature vector is still used by SB3 `MlpPolicy`, so the env API
  stays a flat `Box` observation.
- `rl/train.py` and `rl/imitation.py` now accept
  `--policy-arch mlp|conditioned`.

Smoke test:

```text
conditioned extractor BC smoke saved/loaded successfully.
```

Conditioned BC-only, scale 3, 200 episodes/regime, 20 epochs:

```text
full action agreement: 66.10%
offensive full match: 81.00%
defensive full match: 67.57%
recon-heavy full match: 49.71%

offensive:   reward 11.34, enemy destroyed 24.05, own lost 14.85, final 32.15/22.95
defensive:   reward  3.07, enemy destroyed 11.35, own lost 12.85, final 34.15/35.65
recon-heavy: reward 29.00, enemy destroyed 10.00, own lost 17.15, final 29.85/37.00
```

Conditioned BC-only, scale 3, 500 episodes/regime, 30 epochs:

```text
BC loss ended at 0.7446.
full action agreement: 76.67%
offensive full match: 89.29%
defensive full match: 80.14%
recon-heavy full match: 60.57%

offensive:   reward 14.22, enemy destroyed 26.20, own lost 14.45, final 32.55/20.80
defensive:   reward  5.20, enemy destroyed 11.60, own lost 11.05, final 35.95/35.40
recon-heavy: reward 36.53, enemy destroyed 11.25, own lost 17.55, final 29.45/35.75
```

This is now the best learned policy. Offensive nearly matches the scripted
baseline. Defensive is usable but still behind baseline. Recon-heavy improved
but remains the weakest regime.

50k PPO fine-tune from the best conditioned BC checkpoint:

```text
full action agreement dropped from 76.67% to 13.62%.

offensive:   reward  10.84, enemy destroyed 20.10, own lost 29.20, final 17.80/26.90
defensive:   reward -25.88, enemy destroyed  4.50, own lost 21.80, final 25.20/42.50
recon-heavy: reward  27.52, enemy destroyed  7.50, own lost 24.55, final 22.45/39.50
```

Conclusion:

- The custom extractor plus moderate scale solved most of the imitation issue.
- PPO fine-tuning at the current defaults is destructive. It overwrites the
  cloned behavior instead of improving it.
- The next PPO attempt, if any, should use a much smaller learning rate and/or
  KL control, and should be validated every small checkpoint. Until then, the
  BC-only conditioned model is the best demo policy.

## 2026-05-02 Recon-Heavy Audit

Enhanced `rl/action_agreement.py` to report per-regime/per-head agreement and
top teacher-to-student mismatches.

Best prior model, `overwatch_conditioned_bc_scale3_30e`, 30 seeds:

```text
full action agreement: 77.10%
offensive full match:   88.86%
defensive full match:   81.02%
recon-heavy full match: 61.62%

recon-heavy per-head:
  recon_move    91.33%
  strike_move   91.14%
  strike_policy 95.81%
  sam_move      96.95%
  radar_emit    76.29%
```

Main diagnosis:

- Recon-heavy is weakest because it is the most planner-like branch of the
  scripted teacher: frontier search, threat avoidance, opportunistic strikes,
  HVT exceptions, home defense, and radar timing.
- The biggest explicit mismatch is radar timing. The teacher used
  `env.t % 3 == 0` for recon-heavy radar, but the original observation did not
  expose timestep. That made part of the teacher policy only partially
  observable to the student.

Added optional observation features:

- `include_time_feature`: appends normalized `t / max_steps` before the four
  reward weights.
- `include_time_phase`: appends one-hot `t % 3` before the four reward weights.
- The reward weights remain the final four observation values, so diagnostics
  that swap `obs[-4:]` still work.

Added imitation controls:

- per-regime demo counts:
  `--offensive-episodes`, `--defensive-episodes`,
  `--recon-heavy-episodes`
- per-head BC loss weights:
  `--head-loss-weights`

BC-only variants, 30-seed aggregate eval:

```text
original conditioned_s3_30e:
  offensive:   reward 15.96, enemy destroyed 27.27, own lost 14.77, final 32.23/19.73
  defensive:   reward  5.15, enemy destroyed 11.40, own lost 11.07, final 35.93/35.60
  recon-heavy: reward 37.95, enemy destroyed 11.40, own lost 15.93, final 31.07/35.60

time_balanced, 500/500/500 demos, time feature, no radar weighting:
  offensive:   reward 17.55, enemy destroyed 28.03, own lost 15.10, final 31.90/18.97
  defensive:   reward  4.60, enemy destroyed 14.40, own lost 13.30, final 33.70/32.60
  recon-heavy: reward 39.42, enemy destroyed 11.67, own lost 15.07, final 31.93/35.33

time_recon, 500/500/1200 demos, time feature, radar weight 2:
  offensive:   reward 11.46, enemy destroyed 24.57, own lost 14.93, final 32.07/22.43
  defensive:   reward  5.92, enemy destroyed 11.93, own lost 11.63, final 35.37/35.07
  recon-heavy: reward 40.92, enemy destroyed 13.17, own lost 14.20, final 32.80/33.83

phase_recon, time + phase features, 500/500/1200 demos, radar weight 2:
  full action agreement: 87.42%
  recon-heavy full match: 87.33%
  recon-heavy radar match: 99.14%
  recon-heavy reward: 38.05, enemy destroyed 12.83, own lost 16.73

time_balanced_radar2, 500/500/500 demos, time feature, radar weight 2:
  offensive:   reward 14.59
  defensive:   reward  2.96
  recon-heavy: reward 37.87
```

Interpretation:

- Adding normalized time helps outcomes modestly.
- Oversampling recon-heavy improves recon-heavy outcomes but costs offensive
  performance.
- Explicit phase makes imitation much better but did not improve rollout
  outcomes. Agreement on teacher states is not sufficient when small rollout
  deviations change the state distribution.
- Radar loss weighting alone did not help.

Current best learned policies:

- Best all-regime tradeoff: `rl/overwatch_conditioned_bc_scale3_time_balanced`
- Best recon-heavy-specific learned policy:
  `rl/overwatch_conditioned_bc_scale3_time_recon`
- Best pure teacher-state imitation:
  `rl/overwatch_conditioned_bc_scale3_phase_recon`

Recommended demo default for one learned model:

```powershell
.\.venv\Scripts\python rl\eval.py --policy ppo --model rl\overwatch_conditioned_bc_scale3_time_balanced --weight-obs-scale 3 --include-time-feature --steps 35
```

Recommended recon-heavy-focused demo:

```powershell
.\.venv\Scripts\python rl\eval.py --policy ppo --model rl\overwatch_conditioned_bc_scale3_time_recon --weight-obs-scale 3 --include-time-feature --steps 35 --regime recon-heavy
```

## 2026-05-02 Claude Reconciliation

Claude's critique that total reward should not be the headline metric is
correct. `rl/aggregate_eval.py` now prints behavior metrics first:

```text
final_blue, final_red, enemy_destroyed, own_lost, new_cells, radar_emits,
episode_length, total_reward
```

Claude's concern that the `time_balanced` comparison was based on seed 11 was
not correct for the latest Codex results. The numbers above came from N=30
aggregate evals. To remove ambiguity, we reran the exact consolidated comparison
Claude requested:

```powershell
.\.venv\Scripts\python rl\aggregate_eval.py --seeds 30 --steps 35 `
  --config baseline=baseline@1 `
  --config bc_200k=rl/overwatch_bc_200k@1 `
  --config scale10=rl/overwatch_bc_scale10_teacher_100k@10 `
  --config time_balanced=rl/overwatch_conditioned_bc_scale3_time_balanced@3@time `
  --config phase_recon=rl/overwatch_conditioned_bc_scale3_phase_recon@3@time@phase `
  --csv-path rl/logs/aggregate_eval_claude_requested.csv --csv
```

N=30 behavior summary:

```text
baseline:
  offensive:   final 31.73/20.53, enemy 26.47, own 15.27, cells 54.60, radar 35.00
  defensive:   final 35.43/35.33, enemy 11.67, own 11.57, cells 33.40, radar  2.87
  recon-heavy: final 36.57/31.87, enemy 15.13, own 10.43, cells 51.77, radar 13.87

bc_200k:
  offensive:   final 21.53/32.40, enemy 14.60, own 25.47, cells 53.20, radar  0.03
  defensive:   final 21.37/33.00, enemy 14.00, own 25.63, cells 54.13, radar  0.00
  recon-heavy: final 21.53/32.40, enemy 14.60, own 25.47, cells 53.27, radar  0.00

scale10:
  offensive:   final 17.50/39.37, enemy  7.63, own 29.50, cells 54.77, radar 22.33
  defensive:   final 16.87/42.90, enemy  4.10, own 30.13, cells 38.47, radar  0.00
  recon-heavy: final 16.20/46.17, enemy  0.83, own 30.80, cells 48.07, radar  8.30

time_balanced:
  offensive:   final 31.90/18.97, enemy 28.03, own 15.10, cells 54.87, radar 35.00
  defensive:   final 33.70/32.60, enemy 14.40, own 13.30, cells 33.80, radar  3.23
  recon-heavy: final 31.93/35.33, enemy 11.67, own 15.07, cells 50.23, radar 13.33

phase_recon:
  offensive:   final 31.90/22.60, enemy 24.40, own 15.10, cells 54.67, radar 35.00
  defensive:   final 35.30/35.33, enemy 11.67, own 11.70, cells 33.97, radar  3.33
  recon-heavy: final 30.27/34.17, enemy 12.83, own 16.73, cells 49.40, radar 14.17
```

Interpretation:

- `time_balanced` is the best learned model by all-regime behavior shape.
- It beats prior students decisively.
- It matches or slightly exceeds the scripted baseline on offensive lethality
  and exploration.
- It does not beat the scripted baseline on recon-heavy force preservation.
- `phase_recon` proves better teacher-state imitation can fail to improve
  rollout outcomes, which is a standard BC distribution-shift effect.

Current toy conclusion:

- The toy model is demo-ready with `time_balanced`.
- The toy has probably reached the point where more BC-only tuning has
  diminishing returns.
- The next high-value work is either a demo surface around `time_balanced` or
  refreshing the engine bridge after the latest `main` merge.

Merge note:

- `origin/main` was merged into `RL-temp` as `e9712a0`.
- The `rl/` toy lane was unaffected.
- Claude reports the `rl_alt/` engine bridge needs refresh because main removed
  objectives and changed STRIKE to hex-targeted orders.

## Guardrails

- Do not touch or delete `rl_alt/`; that folder is reserved for Claude's work.
- Do not modify Alex's engine for this standalone demo.
- Prefer diagnostics and small retrains over long PPO runs on this machine.
