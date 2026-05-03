# Aggregated Model Comparison (N=30)

Run with `.venv/Scripts/python rl_alt/agg_eval_models.py`. Apples-to-apples
N=30 seeds × 3 regimes for each of: scripted `baseline`,
`rl/overwatch_bc_200k.zip` (scale=1), `rl/overwatch_bc_scale10_teacher_100k.zip`
(scale=10).

## Headline

**Scripted baseline dominates both students on outcomes across all three
regimes.** Among students, `bc_200k` (scale=1) uniformly beats
`scale10_teacher_100k` — the stronger weight signal at scale 10 buys
action-level conditioning responsiveness at the cost of force preservation,
and that trade is net-negative on the 30-seed average.

## Gap tables

### `own_value_lost` (lower is better)

| regime | baseline | bc_200k | scale10_100k | gap(bc_200k) | gap(scale10) |
|---|---:|---:|---:|---:|---:|
| offensive | 18.73 | 25.47 | 29.50 | +6.73 | +10.77 |
| defensive | 9.53 | 25.63 | 30.13 | +16.10 | +20.60 |
| recon-heavy | 17.23 | 25.47 | 30.80 | +8.23 | +13.57 |

### `final_blue_value` (higher is better)

| regime | baseline | bc_200k | scale10_100k | gap(bc_200k) | gap(scale10) |
|---|---:|---:|---:|---:|---:|
| offensive | 28.27 | 21.53 | 17.50 | -6.73 | -10.77 |
| defensive | 37.47 | 21.37 | 16.87 | -16.10 | -20.60 |
| recon-heavy | 29.77 | 21.53 | 16.20 | -8.23 | -13.57 |

## Survival (truncated/30, higher is better)

| | offensive | defensive | recon-heavy |
|---|---:|---:|---:|
| baseline | 100% | 100% | 80% |
| bc_200k | 10% | 10% | 10% |
| scale10_100k | 16.7% | 23.3% | 6.7% |

Both students die early in 80–95 % of seeds across regimes; baseline
almost always reaches the step cap.

## Interpretation

- **Scale-10 regression is real, not seed-fragile.** Over N=30, scale-10
  loses more own value than bc_200k in *every* regime (+4.0, +4.5, +5.3)
  and destroys far less enemy value (−7.0, −9.9, −13.8). Separations
  exceed per-seed std.
- **Worst student-teacher gap: defensive regime.** `own_value_lost` gap
  +16.1 / +20.6; `final_blue_value` collapses 37.5 → ~17. Both students
  ignore the "preserve own forces" weight in defensive.
- **Closest: offensive regime.** Students naturally trade pieces, which
  partly aligns with offensive intent — `own_value_lost` gap "only"
  +6.7 / +10.8.
- **Does scale-10 beat scale-1 anywhere?** No — bc_200k ≥ scale-10 on
  every headline outcome in every regime. Scale-10 only "wins" on
  commander-conditioned radar usage (offensive emits 22.3 turns vs
  bc_200k's 0.03), but it doesn't translate into outcomes.
- **Both students are degenerate on survival** (6.7–23.3 % truncation
  rate vs baseline 80–100 %); they learn an aggressive engage-and-die
  behavior regardless of commander weights.

## What this means with the agreement-audit data

Cross-referencing with `findings_student_teacher.md` (full-action
agreement 0–3.2 % for both models): the students aren't just outcome-bad,
they are not actually imitating the teacher at all. BC has mode-collapsed
the move heads. Conditioning sensitivity at scale 10 is brittle slot-flipping,
not real imitation. Outcome regression is consistent with that.
