# Demo Divergence Audit

## Demo Divergence Audit

How much does the scripted baseline's action vector change across the three
commander-intent regimes (offensive / defensive / recon-heavy) on matched
starting and mid-game states? This bounds the regime signal a behavioral
cloning student could pick up from these demonstrations.

Action layout (7 slots): recon_move, recon_pol, strike_move, strike_pol,
sam_move, sam_pol, radar. Cells are per-slot agreement rate across seeds
(higher = regimes pick the same value more often). full_match is the rate
where all 7 slots match; full_flip = 1 - full_match.

### Reset-state divergence (N = 200 seeds)

| pair | recon_move | recon_pol | strike_move | strike_pol | sam_move | sam_pol | radar | full_match | full_flip |
|---|---|---|---|---|---|---|---|---|---|
| offensive vs defensive    | 0.000 | 1.000 | 0.325 | 0.000 | 0.430 | 1.000 | 0.000 | 0.000 | 1.000 |
| offensive vs recon-heavy  | 1.000 | 1.000 | 0.145 | 0.000 | 0.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| defensive vs recon-heavy  | 0.000 | 1.000 | 0.820 | 1.000 | 0.000 | 1.000 | 0.000 | 0.000 | 1.000 |

### Mid-game divergence (N = 50 seeds, 5 offensive-driven steps before query)

The env's `set_reward_weights` only stores `fixed_weights`; the live
`w_enemy/w_own/w_info/w_time` attrs the policy reads are written at reset, so
the script mirrors the swap onto those attrs to make the regime change
effective mid-episode.

| pair | recon_move | recon_pol | strike_move | strike_pol | sam_move | sam_pol | radar | full_match | full_flip |
|---|---|---|---|---|---|---|---|---|---|
| offensive vs defensive    | 0.060 | 0.320 | 0.680 | 0.700 | 0.000 | 1.000 | 0.000 | 0.000 | 1.000 |
| offensive vs recon-heavy  | 0.200 | 0.500 | 0.680 | 0.700 | 0.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| defensive vs recon-heavy  | 0.560 | 0.820 | 1.000 | 1.000 | 0.240 | 1.000 | 0.000 | 0.000 | 1.000 |

### Headline numbers (full-action flip rate per pair)

| pair | reset-state | mid-game |
|---|---|---|
| offensive vs defensive    | 1.000 | 1.000 |
| offensive vs recon-heavy  | 1.000 | 1.000 |
| defensive vs recon-heavy  | 1.000 | 1.000 |

### Interpretation

The strongest regime signal sits in sam_move, radar, recon_move, and
strike_pol; recon_pol and sam_pol are largely shared across regimes and
carry the least signal. The hardest pair to separate is defensive vs
recon-heavy (highest per-slot agreement, e.g. strike slots both 1.0
mid-game) - they diverge mainly on recon and sam posture - while offensive
vs defensive is the most separable. Every pair shows a 100% full-action
flip rate on both reset and mid-game states: no two regimes ever produce
identical 7-slot action vectors on the same state, so the BC dataset
carries unambiguous per-state regime labels and a student conditioned on
the four reward weights has enough signal to learn the regime distinction.
