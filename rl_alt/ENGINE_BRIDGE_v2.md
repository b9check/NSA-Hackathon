# EngineEnv scoping v2 — post-merge refresh

Read-only. Replaces v1 (`ENGINE_BRIDGE.md`) for the post-PR-A/PR-B engine.
Wrapper code (`engine_env.py`, `scripted_red.py`, `engine_smoke.py`) is
broken on first `reset()` and must be rebuilt against the new API.

---

## 1. What changed in the engine

### 1a. Objectives are gone (PR-A.1)

- `MapInfo` no longer carries `objective_hexes` (`engine/state.py:106-109`
  is just `cols/rows/cells`). The wrapper crashes here first.
- Scenario YAML `objectives:` block dropped (`scenarios/strait_n7.yaml`
  has no objectives section; was a top-level field in v1).
- `compute_scores` no longer awards an objective bonus
  (`engine/resolve.py:387-404`); it's now display-only cost-weighted HP.

> previously: `state.map.objective_hexes: list[Hex]`, +5/turn cap +30.
> now: gone. Capture/objective concept removed entirely.

### 1b. Win condition is HP-threshold and is now actually consulted (PR-A)

- `VictoryConfig` is now `{hp_loss_threshold: float = 0.25, turn_cap:
  int = 30}` (`engine/state.py:97-103`). v1's `capture_hold_turns` and
  `combat_power_threshold` are gone.
- `GameState` gained `starting_hp: dict[str,int]`, `winner`, `win_reason`
  (`engine/state.py:127-131`).
- `compute_winner(state)` (`engine/resolve.py:420-469`) is the
  authoritative end-game gate, called from `_phase_update` every turn
  (`engine/resolve.py:380-383`). Priorities: annihilation → hp_collapse
  (≤25%) → turn_cap. v1 had no consultation of VictoryConfig at all.

> previously: env terminated on annihilation only; turn_limit not enforced.
> now: engine sets `state.winner` itself; wrapper reads it.

### 1c. STRIKE is hex-targeted, not unit-targeted (PR-B)

- `StrikeOrder` field is `target_hex: HexCoord` (`engine/orders.py:29-34`).
  No more `target_id`. Strike resolves against post-MOVE occupants of
  `target_hex` (`engine/resolve.py:277-340`).
- Whiff (empty hex post-move) still burns ammo; `self_destruct=True`
  attacker dies regardless (`engine/resolve.py:329-330,347-351`).
- Same-hex melee: defender takes `damage`, attacker takes
  `ceil(damage/2)` counter-damage (`engine/resolve.py:322-326`).
- Damage is **deterministic** (`weapon.damage: int`); no Pkill rolls.
  `WeaponRef` now has `damage`, `self_destruct`, `target_domains`,
  `ammo` (`engine/state.py:38-48`).

> previously: `StrikeOrder(unit_id, target_id)`, probabilistic Pkill.
> now: `StrikeOrder(unit_id, target_hex)`, deterministic damage, domain filter.

### 1d. 9-type catalog (PR-B); CAPTURE order removed

- `Order` union dropped `CaptureOrder` (`engine/orders.py:51-57`). Only
  MOVE / STRIKE / SCOUT / OVERWATCH / HOLD remain. Note: ACTIVATE_RADAR /
  DEACTIVATE_RADAR are documented in `UNITS_AND_RULES.md` §4 but are NOT
  yet in the order union — design pending. SCOUT is now drone-only and
  drops the `target_hex` field; reveals radius around the unit's own hex
  (`engine/orders.py:37-41`, `engine/resolve.py:136-168`).
- 9 types: `infantry, armor, missile_launcher, scout_drone, strike_drone,
  fighter, bomber, destroyer, base` (`engine/catalog/platforms.py:50-115`,
  `UNITS_AND_RULES.md` §7). v1 had 12 named platforms.
- `UnitInstance.domain` values are now `"land"|"air"|"sea"` (no `"amphib"`
  in the new catalog; the field still permits it but no unit uses it).

### 1e. Scenario YAML schema

- `scenarios/strait_n7.yaml`: `objectives:` removed; `victory:` now
  `{hp_loss_threshold, turn_cap}`; `bases:` block kept (still separate
  from units in code, despite GAME_RULES_DESIGN.md §1 hinting at unification).

---

## 2. What in `engine_env.py` survives, breaks, or can be dropped

### Survives (use as-is)

- Class scaffolding, `__init__` probe-the-scenario pattern (lines 97-130)
  — `cols/rows/turn_limit/n_bases` still derivable.
- `reset()` flow calling `load_scenario` then `compute_scores` (135-146).
- `step()` outer skeleton: decode → opponent orders → `resolve_turn` →
  reward → obs → info (148-182). Only the internals change.
- `_terrain_grid` helper, `_DELTAS_*` neighbor tables, `_move_delta`
  (53-65) — terrain & hex math unchanged.
- `_encode_unit` core (67-89) — fields it touches (col/row/hp/max_hp/
  side/domain/speed/sensor/weapon/cost/stealth) all still exist on
  `UnitInstance`. Only the `DOMAINS` tuple should drop `"amphib"`.
- Fog-of-war computation in `_build_obs` (303-318) — `_is_visible` is
  still exported (`engine/resolve.py:99-114`) and unchanged in shape.
- `valid_action_mask` skeleton (246-284) — direction masking by
  terrain/in_bounds is reusable; only the STRIKE branch needs updating.

### Needs rewrite

- **`_build_obs` `objective_mask` block (297-300, 347).** `state.map.
  objective_hexes` no longer exists — this is the line that crashes
  `reset()`. Drop the channel from `observation_space` and from
  `_build_obs`'s return dict.
- **`_decode_actions` STRIKE branch (217-222).** `StrikeOrder(target_id=...)`
  is invalid — must be `target_hex=(c,r)`. Either keep the
  "nearest-visible-enemy" heuristic and pass *that unit's hex* (post-move
  it might whiff — that's fine, learnable), or expose a hex-pick action
  (see §3).
- **`valid_action_mask` STRIKE check (277-282).** Range check still
  works, but the action-decoding semantics it gates have changed.
- **Termination logic (175-178).** Should consult `state.winner`
  directly; HP-collapse wins are now possible without annihilation.
- **Reward formula (164-172).** `compute_scores` lost the objective
  bonus, so its dynamic range is narrower (pure cost-weighted HP %
  ∈ [0,100]). Numerically still works as `Δmy − Δopp`, but see §3 for
  a cleaner alternative.

### Drop

- `objective_mask` observation channel — feature has no referent.
- `A_CAPTURE` (would have been added had we shipped v0.5; never made it
  into the action enum, so just don't add it).
- The `"amphib"` slot in `DOMAINS` — no 9-type unit uses it.

---

## 3. Updated v0 spec

### 3.1 Where to wrap

Same chokepoint: `engine.resolve.resolve_turn` (`engine/resolve.py:474-489`).
`load_scenario` (`engine/scenario.py:105-171`) still produces a fully-
populated `GameState`. No FastAPI / no server — confirmed unchanged.

### 3.2 Action space

Keep `MultiDiscrete([N]*MAX_UNITS_PER_SIDE)` shape with `MAX_UNITS = 12`
(scenario has 9-per-side; headroom safe).

**Recommended menu (N = 9, same as v1 for diff legibility):**

```
0  HOLD
1  OVERWATCH
2-7 MOVE_NE..MOVE_NW (six neighbor directions, one-hex)
8  STRIKE_NEAREST_ENEMY_HEX
```

`STRIKE_NEAREST_ENEMY_HEX` decodes to `StrikeOrder(unit_id, target_hex=
(enemy.col, enemy.row))` where enemy = nearest visible enemy currently
in weapon range. Whiff-by-move-out is acceptable training signal. SCOUT
is still skipped for v0 (drone-only, niche, hard to reward without
visibility-aware shaping). CAPTURE is gone. ACTIVATE/DEACTIVATE_RADAR
are deferred until those order types are actually wired into `Order`.

Per-unit features (`_encode_unit`): same 14 floats; just drop the
`amphib` one-hot lane. If you want to expose ammo to the policy
(deterministic damage + finite ammo on missile_launcher/strike_drone/
bomber/fighter/destroyer makes ammo strategically real now), append
`max(weapon.ammo,0)/10.0` for the unit's primary weapon — bumps F to 15.

### 3.3 Observation space

Keep the Dict, with edits:

- `terrain` — unchanged. 5 terrain values.
- `objective_mask` — **delete**.
- `units` — same shape `(24, 14|15)`. Visibility gate unchanged.
- `bases` — same.
- `fog_self` — unchanged. **Note:** `_is_visible` still uses the
  summary `entity.sensor` field (single int). Per `UNITS_AND_RULES.md`
  §2 the proper sensor model is passive-view + toggleable radar, but
  the resolver hasn't implemented that yet; the wrapper should follow
  the resolver, not the design doc.
- `turn` — keep, normalized by `state.victory.turn_cap` (30) rather
  than `turn_limit` for symmetry with the win check.
- **Optionally add** `hp_pct_self`, `hp_pct_opp` — two scalars derived
  from `_hp_total` / `starting_hp`. These are the literal inputs to
  the win condition; cheap to expose, materially helps cold-start.

### 3.4 Reward

The HP-threshold win condition makes the cleanest signal:

```
r_t = (Δhp_pct_self) − (Δhp_pct_opp)         # per step, dense
     + R_terminal                             # only on episode end
R_terminal = +1 if state.winner == self
             −1 if state.winner == opp
              0 if draw / truncation
```

where `hp_pct_side = sum(hp) / starting_hp[side]`. This is monotone in
the actual win condition (unlike `compute_scores`, which is now just
display flavor) and keeps the scale tight (∆ per step in [−0.25, 0.25]
typically). `compute_scores`-delta still works as a fallback if you
want a 1-line v0; both should learn, but HP% is more honest.

### 3.5 Termination

```python
terminated = self.state.winner is not None
truncated  = self.state.turn >= self.state.victory.turn_cap and not terminated
```

(In practice the engine sets `winner="draw"|"blue"|"red"` itself on
turn-cap, so `truncated` rarely fires — leave it for safety.) Drop
the v1 manual annihilation check; it's redundant with `state.winner`.

---

## 4. Scripted red opponent

`scripted_red.choose_orders` (`rl_alt/scripted_red.py`) is ~80%
salvageable:

- `_is_visible`-based target picking, neighbor-step MOVE, distance-in-
  weapon-range check — all still valid.
- **One line breaks**: `StrikeOrder(unit_id=u.id, target_id=target.id)`
  at line 47 — change to `target_hex=(target.col, target.row)`.
- Drone with no weapon (`u.weapon == 0`) will fall through to MOVE,
  which is fine (scout_drone, base both speed-0 stationary will just
  HOLD). Optional polish: route scout_drones to SCOUT.

That's the full diff. Maybe 15 minutes.

---

## 5. Effort estimate

Reusing v1 wrapper as the starting point (which is the right move —
shape and structure are sound, just the API drift hurts):

| Task | Hours |
|---|---|
| Drop objective_mask, add winner/HP%-based termination | 1 |
| Rewrite STRIKE decode + mask to hex-targeted | 1.5 |
| New reward (HP% delta + terminal) | 1 |
| Drop `amphib`, optional ammo feature | 0.5 |
| Fix scripted_red STRIKE call | 0.25 |
| Re-run smoke + chase any pydantic-v2 / domain-string surprise | 2-4 |
| Re-run a short PPO sanity train | 2-3 |

**Low: 6h. Expected: 9h. High: 14h.**

About 60-70% of v1's code lines survive verbatim. This is a refresh,
not a rewrite.

---

## 6. Risks / unknowns

1. **Reward sparsity got worse, not better (HIGH).** With objectives
   gone, there's no reason for units to congregate or close — the dense
   shaping signal v1 leaned on (the +5/turn objective bonus) is dead.
   `Δhp_pct` only fires on damage, which only fires when units come into
   weapon range, which only happens if they MOVE toward each other for
   no immediate reward. Cold-start exploration is meaningfully harder
   than v1. Mitigation: imitation pretraining from `scripted_red` (you
   already have machinery for this in recent commits), or auxiliary
   shaping reward on enemy-distance reduction.
2. **STRIKE-to-hex semantics interact badly with random exploration
   (MED).** A randomly-acting agent firing at "nearest enemy's hex"
   wastes ammo on whiffs (defender moved). Finite-ammo weapons
   (missile_launcher: 4, strike_drone: 1, bomber: 2) get burned out
   in 2-4 turns of random play. The policy may need to learn ammo
   conservation before it learns combat — long horizon. Mitigation:
   mask STRIKE when ammo == 0 (already implicit via `_primary_weapon`,
   but check); consider only allowing STRIKE when defender has no
   MOVE option (HOLD/OVERWATCH).
3. **`_is_visible` is shallower than the design (LOW-MED).** The
   resolver still uses the summary `unit.sensor` int. Stealth halving
   works, but the typed-radar / passive-view distinction in
   `UNITS_AND_RULES.md` §2 is unimplemented. The wrapper inherits this;
   risk is that we ship an RL agent learning a sensor model that gets
   replaced under it in a later PR.

---

## 7. Recommendation

**Refresh, don't reconsider.** The v1 design is mostly intact; the
merge mostly *cleans up* surface area (no objectives, no probabilistic
combat, single source of truth for end-game). Total expected effort
≈9h to a passing smoke test.

**Things that got easier:**
- Termination is now a one-liner (`state.winner is not None`); v1 had
  to reimplement turn-limit enforcement because the engine ignored
  `VictoryConfig`.
- Deterministic damage means episode replays for diagnostics are
  exactly reproducible from `(seed, orders)`. Nice for debugging the
  reward signal.
- HP-threshold win is a cleaner, monotone reward target than the
  old "score includes objective bonus" mess. The reward formula
  literally matches the win condition now.

**Things that got harder:**
- Reward is sparser (no objective-occupation tick). Cold start gets worse.

**Minimum viable cut for v0.5:**
- Same scenario (`scenarios/strait_n7.yaml`).
- 9-action menu, MultiDiscrete[9]*12, mask illegal directions/STRIKE.
- Obs Dict: terrain / units / bases / fog_self / turn / +hp_pct (drop
  objective_mask).
- Reward = `Δhp_pct_self − Δhp_pct_opp + terminal{±1, 0}`.
- Termination = `state.winner is not None`.
- Scripted red: 1-line patch.

---

## 8. Diff vs v1

| Decision | v1 said | v2 says |
|---|---|---|
| Action menu size | 11 (incl. SCOUT, CAPTURE) → trimmed to 9 | 9 (SCOUT deferred, CAPTURE gone) |
| STRIKE decode | `StrikeOrder(target_id=nearest_enemy.id)` | `StrikeOrder(target_hex=nearest_enemy_hex)` |
| Obs keys | terrain, objective_mask, units, bases, fog_self, turn | terrain, units, bases, fog_self, turn, [+hp_pct] |
| Per-unit features | F=14 incl. amphib lane | F=14, drop amphib (optionally +ammo → F=15) |
| Reward | `Δcompute_scores` (incl. +5/turn objective) | `Δhp_pct_self − Δhp_pct_opp + terminal` |
| Termination | manual: turn≥limit OR annihilation | `state.winner is not None` (engine-driven) |
| Scenario fields read | `map.objective_hexes`, `turn_limit` | `victory.turn_cap`, `starting_hp`, `winner` |
| Domain set | land/air/sea/amphib | land/air/sea |
| Effort estimate | 14/22/35h (greenfield) | 6/9/14h (refresh of v1) |
