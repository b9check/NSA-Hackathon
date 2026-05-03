# RL Lane Findings

Running record for the parallel-track baseline-divergence research lane.
Append-only. Sub-agents write to companion files (`findings_*.md`,
`ENGINE_BRIDGE.md`); this index synthesizes.

## Companion files

- `findings_demo_divergence.md` — per-slot action divergence audit across
  regimes on matched states (round 1, Agent 1).
- `ENGINE_BRIDGE.md` — scope of a Gymnasium adapter over `engine/` so RL
  work eventually transfers off the toy (round 1, Agent 2).
- `findings_agg_eval.md` — multi-seed baseline eval to validate regime
  distinctness across seeds (round 1, Agent 3).
- `findings_student_teacher.md` — student-teacher action-agreement audit
  on the trained PPO models (round 2).
- `findings_agg_eval_models.md` — multi-seed apples-to-apples comparison
  of baseline vs trained models (round 2).
- `findings_engine_env.md` — `EngineEnv` v0 wrapper findings + smoke test
  (round 3) and post-merge v0.1 refresh (round 4).
- `ENGINE_BRIDGE_v2.md` — post-merge re-scoping of the bridge after main
  rewrote the engine (round 4).

## Context

The standalone toy RL env (10×10 grid, blue recon/strike/SAM vs scripted
red, fog of war, HVTs, radar emit/silent) trains a reward-conditioned PPO
policy. Four reward weights `[w_enemy, w_own, w_info, w_time]` are
sampled per episode and concatenated to the obs, so a single policy is
supposed to adapt to commander intent.

Two parallel work lanes:
- **Codex lane** (owns `rl/`): conditioning architecture,
  `--weight-obs-scale` amplification, BC + PPO training cycles, the
  conditioning diagnostic tool.
- **This lane** (owns `rl_alt/`): teacher-baseline divergence,
  measurement, scoping. No model training.

## Diagnosis trail

### Initial finding — PPO-from-scratch (500k) collapsed all regimes

The original PPO model produced near-identical action distributions
across offensive/defensive/recon-heavy. Final-state metrics were
literally identical (e.g. blue/red 20/42 with enemy_destroyed = 5 across
all three regimes); only the reward totals differed because the weight
vectors differed.

Initial reading: "the policy is ignoring the weight inputs."

### Refined — actions DO differ; outcomes converge

Codex's conditioning diagnostic on `bc_200k` showed:

```
offensive vs defensive    full_action_flip_rate: 0.583
offensive vs recon-heavy  full_action_flip_rate: 0.200
defensive vs recon-heavy  full_action_flip_rate: 0.550
```

So the policy IS reading weights — actions differ ~58 % across the
strongest pair. But the resulting policies are all weak, so outcomes
converge. The "regime-blind" framing was too strong.

### Conditioning fix via weight-obs scaling

Codex added `--weight-obs-scale` (multiplier on the last 4 obs entries).
Scale 10 raises flip rates to 1.00 / 0.97 / 0.90 — strong action-level
conditioning.

But the scale-10 trained model regressed on outcomes:

| Regime | bc_200k own lost | scale-10 own lost |
|---|---:|---:|
| Offensive | ~27 | 34 |
| Defensive | ~27 | 39 |
| Recon-heavy | ~27 | 39 |

Defensive at scale 10 has radar emit = 0 across the entire episode,
regardless of state — the model has learned a fixed "defensive
template" that doesn't actually defend. Strong conditioning + insufficient
state-dependence = brittle regime templates.

### Baseline rewrite (this lane)

In `rl_alt/baseline.py`, rewrote the three regime functions:

- **Offensive**: prioritize red strike + red HVTs as targets, push
  toward enemy corner when nothing visible, SAM moves north every turn,
  radar always emit.
- **Defensive**: hug HVT anchor (radius 1), retreat recon at distance
  ≤ 4, prefer red strike specifically over other air threats, SAM silent
  unless actively in strike range.
- **Recon-heavy**: frontier sweep with retreat from red strike; strike
  hangs back at radius 2 of own anchor (was suiciding at midfield); SAM
  holds; radar emits unless red strike within 5 of own SAM.

Generalized `_dist` to accept tuples; added `_red_priority_targets` and
`_red_strike_visible` helpers.

### Single-seed eval (seed = 11) confirms divergence

| Metric | Offensive | Defensive | Recon-heavy |
|---|---:|---:|---:|
| Final blue / red value | 32 / 37 | 39 / 32 | 25 / 37 |
| Total reward | -9.12 | 18.70 | 30.22 |
| Enemy value destroyed | 10 | 15 | 10 |
| Own value lost | 15 | 8 | 22 |
| New cells revealed | 53 | 16 | 51 |
| Radar emit turns | 35 | 1 | 35 |
| Episode | survived 35 | survived 35 | survived 35 |

Each regime has a unique signature on every measured axis. But N = 1 per
regime — see `findings_agg_eval.md` for multi-seed validation.

## Open questions

1. Are these regimes distinct across **seeds**? → `findings_agg_eval.md`.
2. How much regime signal do the **demonstrations carry per-slot** in
   matched states? → `findings_demo_divergence.md`.
3. Does the BC student match the teacher on matched states, and where
   does it deviate? **Open — needs measurement.**
4. Is the scale-10 outcome regression seed-fragile or universal?
   **Open** — Codex reported single-seed.
5. What would it take to run RL on the real hex engine? →
   `ENGINE_BRIDGE.md`.

## Working hypothesis

The bottleneck is no longer "does the policy read weights" — it's the
**BC teacher-student gap**. Scale-10 BC student is worse than scale-10
baseline teacher on all three regimes. More BC of the same kind likely
entrenches the gap rather than closing it. The gap is the next thing to
characterize before more training compute.

## Round-1 sub-agent results (synthesis)

### Demo divergence (`findings_demo_divergence.md`)

**Full-action flip rate is 1.000 across every regime pair, both at reset
and mid-game (after 5 baseline steps).** Every state, every pair,
produces three different 7-slot actions. The signal in the BC demos is
not just present, it is *maximal* — there is zero ambiguity in the
labels.

Implication: **any regime-blindness in the BC student is a model /
training problem, not a data problem.** The demos are unambiguously
regime-tagged. If the student can't tell offensive from defensive on a
matched state, it's because the architecture isn't using the weight tail
strongly enough or the BC objective is collapsing labels — *not*
because the teacher's labels are similar.

Implementation note from the agent: `env.set_reward_weights(...)` only
populates `fixed_weights`; the live `env.w_*` attrs the policy reads are
copied during `reset()`. To swap regimes mid-episode (mid-game test) the
agent had to assign the four attrs directly. Worth flagging if anyone
re-uses this pattern.

### Aggregated baseline eval (`findings_agg_eval.md`)

**N=30 seeds × 3 regimes. Regimes are distinct on behavior shape, not
on reward ranking.**

Behavior-shape metrics that *cleanly* separate (non-overlapping mean ±
std):

| metric | offensive | defensive | recon-heavy |
|---|---:|---:|---:|
| radar_emit_turns | 35.0 ± 0.0 | 1.3 ± 1.2 | 29.2 ± 11.6 |
| new_cells_revealed | 49.1 ± 5.4 | 8.9 ± 7.7 | 8.0 ± 18.4 |
| enemy_value_destroyed | 19.8 ± 8.8 | 11.0 ± 4.7 | 10.2 ± 4.4 |
| final_blue_value | 28.3 ± 5.7 | 37.5 ± 7.6 | 29.8 ± 6.5 |
| final_red_value | 27.2 ± 8.8 | 36.0 ± 4.7 | 36.8 ± 4.4 |

`total_reward` does NOT separate the regimes (std ~14–17 across all
three; ranking flips with seed).

**Two important corrections to earlier reporting:**

1. **seed=11 is an outlier**, not representative. Its recon-heavy reward
   (+30.22) is ~+2.3 σ above the population mean (-9.77). The earlier
   single-seed conclusion that "recon-heavy gets the best reward" was
   wrong — the multi-seed truth is **defensive ≈ offensive > recon-heavy**
   on reward.

2. **Recon-heavy is seed-fragile**. 20 % early-termination rate (6/30 die
   before step 35), and `new_cells` std = 18.4 (range 0–56). Any
   single-seed result on recon-heavy is high-variance noise.

The earlier per-seed=11 table I built into `FINDINGS.md` should be read
with this in mind — the *behavior-shape* numbers there generalize, the
*reward* numbers do not.

### Engine bridge (`ENGINE_BRIDGE.md`)

Effort estimate: **low 14 h / expected 22 h / high 35 h** for v0 PPO
end-to-end on the real engine.

Top risk (HIGH): `resolve_turn` mutates `GameState` in place
(`engine/resolve.py:429`). With SB3 vectorized envs this means per-step
deepcopy of a fat pydantic model, or strict one-state-per-subprocess
discipline. Easy to get wrong; easy to make slow.

Recommendation: **build the minimum cut.** Wrap `engine.resolve.resolve_turn`,
per-unit MultiDiscrete with 9 choices (drop SCOUT and CAPTURE for v0),
Dict obs (~1620 floats), reward = score delta from `compute_scores`,
manual termination at `turn_limit=30` (engine and server don't enforce).

Two prerequisites that have to be written first: a scripted red opponent
(none exists in the engine) and a terminal condition. Both cheap.
Fallback: if v0 doesn't learn within ~1M steps, extend the toy further.

## What the round-1 results actually mean for the plan

1. **Stop worrying about demo signal.** The demos are perfectly
   regime-separated (1.000 flip rate everywhere). If the student can't
   match the teacher, the issue is on the model side.

2. **Stop using single-seed eval as the headline metric.** seed=11 was
   misleading. The right reporting is multi-seed mean ± std on
   behavior-shape metrics — `total_reward` is too noisy to be a regime
   discriminator.

3. **The teacher-student gap is now the only open question on the toy.**
   Specifically: at scale 10 (or any scale), how often does the BC
   student's action match the teacher's on matched states? If <70 %,
   capacity / objective is the issue. If >85 %, the gap is in the
   subsequent PPO finetune dynamics, not BC.

4. **The engine bridge is a real option, not a stretch goal.** ~22 h
   expected effort with a clean wrap surface. Worth weighing against
   continued toy iteration.

## Round-2 sub-agent results (synthesis)

### Student-teacher action agreement (`findings_student_teacher.md`)

**Full-action agreement (deterministic predict, on teacher-distributed
mid-game states, ~600 tuples per model):**

| Model | offensive | defensive | recon-heavy |
|---|---:|---:|---:|
| `bc_200k` (scale=1) | 0.0 % | 1.0 % | 0.0 % |
| `bc_scale10_teacher_100k` (scale=10) | 3.2 % | 0.0 % | 1.2 % |

**Diagnostic bucket: <60 % → BC is collapsing.** Both students fail to
reproduce the full teacher action on essentially any state. Per-slot
pattern:

- Policy slots (`recon_pol`, `sam_pol`) are pinned at 100 % — degenerate
  constant choices that match the teacher's mode by accident.
- Move slots collapse to single directions: scale-1's `strike_move` is
  91 % pinned to action 4 (east); scale-10's `recon_move` flips between
  1 and 4.
- Radar is per-regime bimodal for scale-1 (0 % / 100 %).

**Scale-10 vs scale-1: no fidelity improvement.** 1.5 % vs 0.3 % full
agreement — within noise. Scale-10's apparent regime sensitivity at the
flip-rate level is **brittle slot-flipping, not real imitation.**

### Aggregated model comparison (`findings_agg_eval_models.md`)

N=30 seeds × 3 regimes × 3 policies, same seed list across all three.

**Headline: scripted baseline dominates both students on outcomes across
all three regimes.** Among students, `bc_200k` uniformly beats `scale10`.

| `own_value_lost` (lower better) | baseline | bc_200k | scale10 |
|---|---:|---:|---:|
| offensive | 18.7 | 25.5 | 29.5 |
| defensive | 9.5 | 25.6 | 30.1 |
| recon-heavy | 17.2 | 25.5 | 30.8 |

| Survival rate (truncated / 30) | baseline | bc_200k | scale10 |
|---|---:|---:|---:|
| offensive | 100 % | 10 % | 17 % |
| defensive | 100 % | 10 % | 23 % |
| recon-heavy | 80 % | 10 % | 7 % |

- **Scale-10 regression is real, not seed-fragile.** Across N=30 it
  loses more own value than bc_200k in every regime (+4.0, +4.5, +5.3).
- **Worst gap: defensive regime.** `final_blue_value` collapses
  37.5 → ~17 — both students completely ignore "preserve own forces."
- **Both students engage-and-die** in 80–95 % of episodes regardless of
  regime.

## Updated diagnosis

The gap is **BC mode collapse on the move heads**, not capacity, not PPO
finetune dynamics, not demo signal.

The teacher's move-head distribution is multi-modal across regimes
(`strike_move` is east in offensive but hold in defensive). The flat
joint cross-entropy loss in the current BC trainer apparently lets the
policy converge to a single most-frequent class per move head and recover
high log-likelihood by being right on the policy/radar slots. Net result:
0–3 % full-action agreement and 80–95 % early-death rate.

Scaling weight inputs 10x doesn't fix this — it just makes which
particular collapsed action gets picked depend on regime, but the move
heads are still single-mode.

## Strong recommendation: do not run more BC of the current form

Three concrete fix candidates, in order of cheapness:

1. **Per-head loss balancing** — weight each of the 7 categorical losses
   so move heads can't be drowned by policy/radar slots. Or compute
   per-head accuracy during BC training and gate the model on minimum
   per-head accuracy before saving.
2. **Class-balanced cross-entropy on the move heads** — re-weight by
   inverse class frequency on each head to break mode collapse.
3. **Switch from BC-on-(state→action-tuple) to per-head supervised
   classification** with separate output heads sharing a backbone, each
   trained against its own demo distribution. This is what people usually
   do for MultiDiscrete imitation; it's what SB3's joint distribution
   doesn't really do under the hood.

If those don't crack it within ~1 h of code changes + a 100k retrain,
**the engine bridge becomes the cheapest path forward**. The toy has
shown it can express commander intent (the baseline is good, the demos
are perfect), but the current BC pipeline can't capture that. Spending
more compute on the same pipeline is unlikely to help.

## Round-3 result — engine bridge v0 built (`findings_engine_env.md`)

`EngineEnv` Gymnasium wrapper around `engine.resolve.resolve_turn`
exists at `rl_alt/engine_env.py` plus `rl_alt/scripted_red.py` and a
smoke test at `rl_alt/engine_smoke.py`. Smoke test **PASSES**: 10 random
masked actions execute without exception, episode truncates at
`turn_limit = 30` as expected.

- **Action space**: `MultiDiscrete([9] * 12)`. 9-action per-unit menu
  (HOLD, OVERWATCH, MOVE_NE/E/SE/SW/W/NW, STRIKE_NEAREST_VISIBLE_ENEMY).
  SCOUT/CAPTURE dropped per scoping doc §7.
- **Obs space**: `Dict` with `terrain (15,20)`, `objective_mask (15,20)`,
  `units (24,14)`, `bases (6,14)`, `fog_self (15,20)`, `turn (1,)`. Total
  ~1,560 floats; opposing-side unit features zeroed when not visible
  (no fog cheating).
- **Reward**: `Δ(my_score) − Δ(enemy_score)` from `compute_scores`
  including the +5/turn objective bonus.
- **Termination**: side annihilation or `turn >= max_turns`.

Independent build, parallel to Codex's BC iteration on the toy.

### Headline issue blocking PPO training

**Cold-start: random play yields 0 reward over 50 steps.** Blue and red
rarely make contact under random exploration on `strait_n7`. Without
reward shaping or warm-start, PPO sees a flat-zero gradient signal for a
long time.

Two cheap fixes:
1. Lightweight shaping (negative distance to nearest visible enemy) for
   the first ~100 k steps.
2. Imitation warm-start from a scripted blue analogous to
   `scripted_red.py`.

Either is ~1–2 h of work and unblocks training.

### Side effect (documented)

`pydantic==2.9.2` and `pyyaml==6.0.2` were installed into `.venv` from
the existing `requirements.txt` — engine imports failed without them.
Not new packages, just the declared versions.

## Round-4 result — engine bridge refreshed post-merge (`ENGINE_BRIDGE_v2.md` + `findings_engine_env.md` v0.1)

`origin/main` was merged into `RL-temp`, bringing PR-A (objectives gone,
`VictoryConfig` HP-threshold + turn_cap actually enforced, `state.winner`
authoritative) and PR-B (hex-targeted STRIKE, deterministic damage,
9-type catalog). The v0 wrapper crashed at `state.map.objective_hexes`.

Refreshed in place; **smoke test PASSES** against the new engine. ~60 %
of v0 code survived verbatim. Edits were surgical:

- **Reward**: switched from `Δcompute_scores` to
  `Δhp_pct_self − Δhp_pct_opp` per step + terminal `±1` from
  `state.winner`. The new `compute_scores` is display-only (cost-weighted
  HP %), so it's no longer the right reward target — the HP-threshold
  win condition needs a reward that's directly monotone in it.
- **Termination**: now a one-liner — `state.winner is not None`. The
  resolver calls `compute_winner` every turn and sets the field on
  annihilation / hp_collapse (≤25 %) / turn_cap.
- **Obs schema**: dropped `objective_mask` (no referent). Dropped
  `amphib` lane in unit features (catalog has none). Added `hp_pct (2,)`
  — the literal inputs to the win condition. `turn` rebased on
  `state.victory.turn_cap`.
- **STRIKE decode**: `target_id` → `target_hex = (target.col, target.row)`.
- **`engine_smoke.py` needed zero changes** — it iterates `obs.keys()`
  generically.

### Cold-start problem persists (and is now a tier worse)

Pre-merge: 0 contact in 50 random steps. Post-merge: contact actually
happens (smoke shows `events=14–18` per step, blue and red HP dropping),
but reward is still ~0 because both sides take symmetric damage under
random masked play — `Δhp_pct_self ≈ Δhp_pct_opp`. The objective bonus
that previously gave a dense spatial attractor is gone. Without
imitation warm-start or distance shaping, MaskablePPO will see near-zero
gradient even with the new reward.

**The bridge is functional. Training is still gated on the cold-start
fix.** Two options unchanged from v0:
1. Imitation warm-start from a scripted-blue policy analogous to
   `scripted_red.py` — the toy lane already has BC machinery proven to
   work (see round 2/3 results).
2. Distance-shaping auxiliary reward during early training.

## Lane discipline

- All work in `rl_alt/`. Never touch `rl/`.
- No model training, no `.zip` loading. Baseline + measurement only.
- Append findings; don't rewrite this index in place. Companion `.md`
  files are append-only.
