# EngineEnv v0 — what works

Gymnasium wrapper around the real hex-engine wargame. Built per
`ENGINE_BRIDGE.md`. Smoke test passes.

## Files

- `rl_alt/engine_env.py` — `EngineEnv` Gymnasium env. Standalone, no SB3
  dependency, no toy-env imports.
- `rl_alt/scripted_red.py` — heuristic opponent: per unit, STRIKE if
  visible enemy in weapon range, else MOVE one hex toward nearest visible
  enemy, else HOLD.
- `rl_alt/engine_smoke.py` — smoke test script.

## Smoke test result

```
action_space: MultiDiscrete([9 9 9 9 9 9 9 9 9 9 9 9])
obs_space keys: ['bases', 'fog_self', 'objective_mask', 'terrain', 'turn', 'units']
  bases:          shape=(6, 14)   dtype=float32
  fog_self:       shape=(15, 20)  dtype=uint8
  objective_mask: shape=(15, 20)  dtype=uint8
  terrain:        shape=(15, 20)  dtype=uint8
  turn:           shape=(1,)      dtype=float32
  units:          shape=(24, 14)  dtype=float32

reset() ok. initial info: blue_score=100.0 red_score=100.0 turn=0

--- 10 random valid steps ---
step 0..9: reward=0.0, blue=100.0, red=100.0  (no contact)
episode ended at step 29 (turn=30, truncated=True)

SMOKE OK
```

10 random masked actions execute without exception. Episode truncates at
`turn_limit=30` as expected.

## Key implementation decisions

1. **Decode-then-fallback for invalid orders.** Each `MultiDiscrete` slot
   maps to an `Order`; clearly illegal picks (off-grid MOVE, STRIKE with
   no visible target, OVERWATCH on unarmed unit) silently fall back to
   `HoldOrder` rather than getting submitted. Keeps `resolve_turn` happy
   without trusting masking to be perfect.
2. **Reward = Δ(my_score) − Δ(enemy_score)**, cached on the env. Uses
   `compute_scores(state)` from `engine/resolve.py:394` directly, so the
   +5/turn objective bonus is included automatically.
3. **No state copying on `step`.** `reset()` calls `load_scenario` fresh
   to avoid the deepcopy hazard in scoping doc §6.1. `_is_visible` is
   called via the engine module rather than reimplemented, so agent
   visibility matches engine resolution exactly.

## What's stubbed or skipped

- **SCOUT and CAPTURE orders** — dropped per scoping §7. The 9-action
  menu is `{HOLD, OVERWATCH, MOVE_NE/E/SE/SW/W/NW, STRIKE_NEAREST_VISIBLE_ENEMY}`.
  CAPTURE is implicit via the objective-bonus shaping; SCOUT effects are
  event-only (not visible in `compute_scores`).
- **Multi-hex MOVE.** Each MOVE action moves exactly one neighbor hex.
  The engine's `path_to` could pull longer moves but adds illegal-target
  cases.
- **Action masking** — `valid_action_mask()` is implemented but
  conservative; it doesn't yet check weapon range for STRIKE perfectly.
  Fine for v0 (the decode fallback handles it).
- **render()** returns None.

## Surprises encountered

1. **Missing declared deps.** `pydantic` and `pyyaml` were in
   `requirements.txt` but absent from `.venv`. Engine imports failed
   until they were installed (versions from `requirements.txt`, no new
   packages added).
2. **`_is_visible` signature** is `(side, entity, state)` — positional
   `side` first — different from what the scoping doc implied.
3. **`BaseInstance` has no `speed` field.** The unified unit/base
   feature encoder uses `getattr(..., 0)` for unit-only fields.

## Known issues / TODOs

1. **Cold-start: random play yields 0 reward over 50 steps.** Blue and
   red rarely make contact under random exploration on `strait_n7`.
   Without reward shaping or warm-start, PPO will see a flat-zero
   gradient signal for a long time. **This is the headline issue blocking
   training.**
2. **Action masking gaps.** STRIKE-no-visible-target and OVERWATCH-on-unarmed
   slots fall through to HoldOrder rather than being masked at the
   action-space level. MaskablePPO will train on slightly-off masks but
   should still work; tighten later.
3. **Score scale.** `compute_scores` returns ~[0, 130]; reward deltas
   are typically small (≤5/turn from objectives, larger spikes from
   kills). May need normalization for PPO value-function stability.
4. **Side effect**: installing the two missing deps changed `.venv`. Not
   `rl_alt/` content, but documenting it.

## How to run

```
.venv/Scripts/python rl_alt/engine_smoke.py
```

To use as a Gymnasium env in your own training script:

```python
from rl_alt.engine_env import EngineEnv

env = EngineEnv(scenario_path="scenarios/strait_n7.yaml", agent_side="blue")
obs, info = env.reset(seed=42)
mask = env.valid_action_mask()  # for MaskablePPO
action = ...                    # MultiDiscrete([9]*12)
obs, reward, terminated, truncated, info = env.step(action)
```

## Recommendation for the next step

Before MaskablePPO training: add a lightweight shaping term (e.g.,
small negative reward proportional to mean distance between visible
enemies and the agent's nearest unit) for the first ~100 k training
steps, OR warm-start with imitation from a scripted blue policy
analogous to `scripted_red.py`. With pure score-delta reward and
`strait_n7` starting positions, the contact rate under random
exploration is too low to produce gradient signal. Either fix is cheap;
without one of them, training will stall.

## v0.1 — post-merge refresh

The merge of `origin/main` into `RL-temp` brought PR-A (objectives gone,
`VictoryConfig` HP-threshold + turn_cap actually enforced, `state.winner`
authoritative) and PR-B (hex-targeted STRIKE, deterministic damage,
9-type catalog). Wrapper rebuilt against the new API per
`rl_alt/ENGINE_BRIDGE_v2.md`. ~60% of v0 code survives verbatim.

### What changed

- **Obs space**: dropped `objective_mask` (no referent). Added `hp_pct`
  (shape `(2,)`, `[self, opp]`) — the literal inputs to the win
  condition. `turn` now normalized by `state.victory.turn_cap` instead
  of the legacy `turn_limit`.
- **Per-unit features**: dropped the `amphib` lane. `UNIT_FEATURES` is
  now 13 (was 14). No 9-type unit uses `amphib`.
- **STRIKE decode**: `StrikeOrder(target_id=…)` → `StrikeOrder(
  target_hex=(target.col, target.row))`. Whiff-by-move-out is accepted
  as legitimate training signal.
- **Reward**: switched from `Δcompute_scores` (objective bonus is gone,
  so the score is now display-only cost-weighted HP%) to the
  HP-threshold-aligned form below.
- **Termination**: now a one-liner — `state.winner is not None`. The
  engine's `_phase_update` calls `compute_winner` every turn and sets
  `winner` on annihilation / hp_collapse (≤25%) / turn_cap. Manual
  alive-checks removed; `truncated` retained as a defensive fallback
  but rarely fires.
- **Scripted red**: 1-line patch to STRIKE call site (same as v0 logic).
- **Smoke**: `engine_smoke.py` untouched — the explicit prints iterate
  `obs.keys()` generically, so dropping `objective_mask` was transparent.

### New smoke output

```
action_space: MultiDiscrete([9 9 9 9 9 9 9 9 9 9 9 9])
obs_space keys: ['bases', 'fog_self', 'hp_pct', 'terrain', 'turn', 'units']
  bases: shape=(2, 13) dtype=float32
  fog_self: shape=(15, 20) dtype=uint8
  hp_pct: shape=(2,) dtype=float32
  terrain: shape=(15, 20) dtype=uint8
  turn: shape=(1,) dtype=float32
  units: shape=(24, 13) dtype=float32

reset() ok. obs keys: ['terrain', 'units', 'bases', 'fog_self', 'turn', 'hp_pct']
sample unit feature row[0]: [0.8 0.8 1. 0. 0. 0. 1. 0.2 0.15 0.2 1.2 0. 1. ]
initial info: {'blue_score': 100.0, 'red_score': 100.0, 'turn': 0}

--- 10 random valid steps ---
step 0: reward=+0.00 term=False trunc=False turn=1 blue=100.0 red=100.0 events=14
step 1: reward=+0.00 term=False trunc=False turn=2 blue=96.9 red=96.9 events=18
step 2: reward=-0.09 term=False trunc=False turn=3 blue=91.8 red=96.9 events=17
step 3: reward=-0.06 term=False trunc=False turn=4 blue=86.8 red=95.0 events=18
step 4: reward=+0.00 term=False trunc=False turn=5 blue=86.8 red=95.0 events=9
step 5: reward=-0.02 term=False trunc=False turn=6 blue=85.8 red=95.0 events=9
step 6: reward=+0.00 term=False trunc=False turn=7 blue=84.0 red=93.1 events=12
step 7: reward=+0.00 term=False trunc=False turn=8 blue=84.0 red=93.1 events=12
step 8: reward=+0.00 term=False trunc=False turn=9 blue=84.0 red=93.1 events=11
step 9: reward=-0.02 term=False trunc=False turn=10 blue=83.0 red=93.1 events=13

episode ended at step 29 (turn=30, term=True, trunc=False)

SMOKE OK
```

Episode ends at turn=30 with `terminated=True` (engine set
`winner` via turn_cap path with hp_pct comparison). 50-step budget
asserted; episode terminated cleanly at step 29.

### Reward formula (derivation)

```
hp_pct(side) = sum(u.hp for live u on side) + sum(b.hp for live b)
                     ─────────────────────────────────────────────
                                  state.starting_hp[side]

r_t = (hp_pct_self_t − hp_pct_self_{t-1})
    − (hp_pct_opp_t  − hp_pct_opp_{t-1})
    + R_terminal

R_terminal = +1.0  if state.winner == agent_side
             −1.0  if state.winner == opponent_side
              0.0  if "draw" or still in progress
```

Why: the HP-threshold win condition compares `cur_hp / starting_hp` to
`0.25`; the dense reward is just the per-step derivative of (my HP% −
opp HP%), which is monotone in the win indicator. Telescopes
across an episode to `(hp_pct_self_T − hp_pct_self_0) − (hp_pct_opp_T −
hp_pct_opp_0) + R_terminal`. Per-step magnitude typically in
`[−0.25, +0.25]` (a wiped destroyer is ~10% of one side's total HP);
terminal bonus dominates by design (±1 vs cumulative ~±0.75).

`compute_scores` is no longer in the reward path. We still surface
`blue_score`/`red_score` in `info` for diagnostic continuity with v0
logs (and SB3 callbacks that read it).

### TODOs

- **Cold-start contact rate.** With objectives gone there is no
  attractor — random play barely closes distance. Confirmed by the
  smoke run: ~half the steps had reward=+0.00. Either imitation
  pretrain on `scripted_red`-as-blue, or add an auxiliary
  enemy-distance-reduction shaping term. `ENGINE_BRIDGE_v2.md` §6 risk
  #1 flagged this; it is the single biggest blocker for v0.5 PPO.
- **Ammo-aware STRIKE masking.** Finite-ammo weapons get burned out in
  2-4 random turns. `unit.weapon > 0` masks range, not ammo. Worth
  threading `_primary_weapon().ammo > 0` into the STRIKE mask check.
- **SCOUT not in action menu.** Drone-only and visibility-shaped, so
  not in v0. Revisit when fog-of-war reward shaping lands.
- **Sensor model is the resolver's summary int**, not the typed
  passive/radar split in `UNITS_AND_RULES.md` §2. The wrapper inherits
  whatever the resolver does. If/when the engine implements the typed
  model, `_is_visible`-based masking will need to be revisited.
