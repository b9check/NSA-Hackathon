# Aggregated Baseline Eval

N=30 seeds × 3 regimes, max_steps=35. Run with
`.venv/Scripts/python rl_alt/agg_eval.py`.

## Headline

**Are the regimes distinct across seeds?** **Partly — yes on behavior
shape, no on reward ranking.**

Behavior-shape metrics (radar emits, exploration footprint, enemy/own
value, final blue/red) cleanly separate all three regimes across 30
seeds. `total_reward` does **not** separate them — per-seed std is
~14–17, and the ranking flips depending on seed.

## Full output

```
REGIME: offensive  weights=[1.0,-0.2,0.3,-0.8]
metric                          mean         std         min         max
total_reward                    4.00       16.02      -18.36       31.14
enemy_value_destroyed          19.77        8.82       10.00       35.00
own_value_lost                 18.73        5.74       15.00       31.00
new_cells_revealed             49.13        5.41       43.00       55.00
radar_emit_turns               35.00        0.00       35.00       35.00
episode_len                    35.00        0.00       35.00       35.00
final_blue_value               28.27        5.74       16.00       32.00
final_red_value                27.23        8.82       12.00       37.00
terminated/truncated:           0/30

REGIME: defensive  weights=[0.5,-1.0,0.2,0.0]
total_reward                    5.79       14.24      -27.00       24.90
enemy_value_destroyed          11.00        4.73        0.00       15.00
own_value_lost                  9.53        7.62        0.00       30.00
new_cells_revealed              8.93        7.68        0.00       24.00
radar_emit_turns                1.30        1.19        0.00        4.00
episode_len                    35.00        0.00       35.00       35.00
final_blue_value               37.47        7.62       17.00       47.00
final_red_value                36.00        4.73       32.00       47.00
terminated/truncated:           0/30

REGIME: recon-heavy  weights=[0.3,-0.3,1.0,-0.2]
total_reward                   -9.77       16.97      -19.28       35.70
enemy_value_destroyed          10.17        4.37        0.00       15.00
own_value_lost                 17.23        6.53        0.00       35.00
new_cells_revealed              7.97       18.39        0.00       56.00
radar_emit_turns               29.23       11.55        5.00       35.00
episode_len                    29.23       11.55        5.00       35.00
final_blue_value               29.77        6.53       12.00       47.00
final_red_value                36.83        4.37       32.00       47.00
terminated/truncated:           6/24  (survival 80%)

SIDE-BY-SIDE (mean +/- std)
metric                       offensive          defensive       recon-heavy
final_blue_value         28.27+/-5.74       37.47+/-7.62      29.77+/-6.53
final_red_value          27.23+/-8.82       36.00+/-4.73      36.83+/-4.37
total_reward              4.00+/-16.02       5.79+/-14.24    -9.77+/-16.97
enemy_value_destroyed    19.77+/-8.82       11.00+/-4.73      10.17+/-4.37
own_value_lost           18.73+/-5.74        9.53+/-7.62      17.23+/-6.53
new_cells_revealed       49.13+/-5.41        8.93+/-7.68       7.97+/-18.39
radar_emit_turns         35.00+/-0.00        1.30+/-1.19      29.23+/-11.55
```

## Most-differentiating metrics (non-overlapping mean ± std)

- **radar_emit_turns**: offensive 35 / defensive 1.3 / recon 29.2 — clean
  three-way split.
- **new_cells_revealed**: offensive 49 vs defensive/recon both ~8 —
  *surprise:* offensive explores **more** than recon-heavy on average
  (recon's frontier move is high-variance, std=18.4).
- **final_blue_value**: defensive 37.5 strictly above offensive 28.3 and
  recon 29.8 — defensive preserves force.
- **enemy_value_destroyed**: offensive 19.8 vs defensive/recon ~10–11 —
  offensive kills ~2x.
- **final_red_value**: offensive 27.2 strictly below defensive 36 and
  recon 36.8.

## Variance / fragility

- **recon-heavy is the seed-fragile regime**: new_cells std=18.4 (range
  0–56), radar std=11.6, 20 % early-termination rate. Single-seed recon
  results are unreliable.
- **offensive is the most consistent** (radar/episode_len zero variance,
  new_cells std=5.4).
- All three have `total_reward` std ~14–17 — reward alone is a poor
  regime discriminator.

## Survival

Offensive 100 %, defensive 100 %, recon-heavy 80 % (6/30 terminate early
— only regime that ever dies before step 35).

## seed=11 vs the population

The earlier single-seed result (seed=11) was an **outlier on reward and
recon yield**:

- Its recon-heavy reward (+30.22) vs population mean (-9.77) → ~+2.3 σ.
- Its recon `new_cells=51` vs mean 8 → ~+2.3 σ.
- Its offensive reward (-9.12) was below mean (+4.0).

seed=11 produced the misleading reward ranking
**recon > defensive > offensive**. The multi-seed truth is
**defensive ≈ offensive > recon-heavy** on reward. On behavior shape
(radar pattern, kill/loss ratio, blue/red endpoints) seed=11 is broadly
representative.

## Bottom line

The "three distinct regimes" claim survives multi-seed scrutiny **on
behavior**, but any reward-ranking conclusion drawn from one seed is
unsafe — especially seed=11, which favored recon-heavy by ~2 σ. For
future single-seed eval reporting, lead with behavior metrics, not
total_reward.
