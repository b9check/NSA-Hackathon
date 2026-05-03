# Game Rules Design — v2

Working document. Revise freely.

Owner: Alex
Status: **proposal**, synthesized from 7 parallel agents on
win condition, combat resolution, order semantics, weapon ammo,
sensor state, unit catalog, action vocabulary, and edge cases.

---

## Overview — what's changing

| Today (v1) | Proposed (v2) |
|---|---|
| 5-min wallclock timer | HP threshold (≤25% remaining) + alt-path "hold all objectives 3 turns" + 30-turn cap |
| Pkill probability rolls per strike | Deterministic damage; no rolls in strike phase |
| Strike targets a unit (`target_id`) | Strike targets a hex (`target_hex`); whoever is there at resolution time takes damage |
| 12 named platforms (F-35A, J-20, …) + 6 named bases | 8 abstract types: `infantry · armor · missile_launcher · scout_drone · strike_drone · fighter · destroyer · base` (1 generic base type) |
| Static `sensor_range: int` per type | Sensors are typed (radar / EO/IR / SIGINT) with state (active/inactive) |
| Radar always passive | RADAR can be `ACTIVE` or `INACTIVE`; player-controlled via `ACTIVATE`/`DEACTIVATE` action; emission detectable by enemy SIGINT |
| 6 actions: MOVE/STRIKE/SCOUT/OVERWATCH/HOLD/CAPTURE | 7 actions: MOVE / STRIKE / SCOUT / OVERWATCH / HOLD / ACTIVATE / DEACTIVATE (CAPTURE removed) |

---

## 1. Win condition

```
A side LOSES when any of these are true at the end of a turn:

  (a) own_hp_total ≤ 0.25 × own_starting_hp_total       (HP collapse)
  (b) opponent has held ALL objective hexes uncontested  (objective hold)
      for ≥ 3 consecutive turns
  (c) turn ≥ 30 AND own_hp% < opponent_hp%               (turn cap tiebreak)

Tie at turn 30 OR both sides cross 25% same turn:
  → compare own_hp%; higher wins
  → if still tied, draw
```

`compute_scores` becomes a display-only helper (HUD still shows a number for
flavor); `compute_winner(state) -> "blue"|"red"|"draw"|None` becomes the
authoritative end-game gate.

State changes:
- Drop `objective_points` field, drop `OBJECTIVE_BONUS_PER_TURN`/`OBJECTIVE_BONUS_CAP`
- Add `starting_hp: dict[str, int]` (sum of unit + base max_hp at game start)
- Add `objective_streak: dict[str, int]` (consecutive turns side has held all objectives)
- `VictoryConfig` gains `hp_loss_threshold: float = 0.25`, `objective_hold_turns: int = 3`

Frontend: delete `Timer.tsx` entirely. Replace with a per-side **HP bar** showing
`current_hp / starting_hp` with a red marker at 25%, plus an "Objective streak"
indicator. `EndGameOverlay` adapts copy based on which condition fired.

---

## 2. Turn flow (the 4 phases stay)

```
┌─ TURN N STARTS ─────────────────────────────────────────────────┐
│                                                                  │
│ A. SCOUT     scout actions reveal contacts (pre-move sensors)    │
│ B. MOVE      planned moves commit; OVERWATCH fires on path       │
│ C. STRIKE    deterministic damage, hex-targeted                  │
│ D. UPDATE    apply damage, remove dead, capture/objective        │
│              counter, emission state cleanup, win check          │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Big tension to resolve before implementing**: how does Phase C interpret
strike orders when the target moved during Phase B? Two consistent options
both satisfy the user's quote *"red decided to move // so didn't get striked"*:

> **Option B — MOVE-first then STRIKE-at-current-hex** (the agents called this
> "(b)" or "phased-simultaneous"): After Phase B, all positions update. Phase C
> resolves strikes against `target_hex` from the order. If defender vacated
> that hex, the strike whiffs.

> **Option C — Strict snapshot** (literal reading of user's quote): Both sides
> see the *start-of-turn* board. Strike orders use either `target_id`
> (locks defender's snapshot hex — defender vacates → whiff) OR `target_hex`
> (locks coordinate). Even if defender ends in the targeted hex, snapshot
> semantics decide.

**Recommend Option B** — simpler, matches what `engine/resolve.py` already
does, and produces "red moved, didn't get hit" cleanly when the strike order
is hex-targeted. Strict snapshot is more elegant but adds a target-mode
distinction the user didn't ask for. **Open for discussion.**

---

## 3. Combat resolution — deterministic damage

### Strike orders take a hex, not a unit

```
StrikeOrder = { unit_id, target_hex }   # was target_id
```

At resolution, look up post-move occupant of `target_hex`:
- If empty → ammo expended, `StrikeMissEvent(hex)`
- If occupant exists and weapon's `pkill[occupant.domain] > 0` → damage applied
  - `pkill` becomes a **domain-eligibility filter** (the float value isn't a
    probability anymore, just `>0` means "this weapon can hurt that domain")
  - Or replace `pkill` with explicit `target_domains: list[Domain]` for clarity
- If occupant exists but weapon can't hurt that domain → ammo expended, miss

### Damage model

```
strike: {
  range:                int,
  damage_target:        int,       # HP loss to whoever is on target_hex
  damage_self_if_same_hex: int,    # HP loss to attacker if firing at own hex
  ammo:                 int,       # -1 unlimited, N finite
  consumes_carrier:     bool,      # renamed from self_destruct; true = unit
                                   #   dies after firing (kamikaze)
  target_domains:       list[Domain]   # which domains this weapon can hurt
}
```

### Same-hex melee

- Firing at own hex with enemy co-located: target takes `damage_target`,
  attacker takes `damage_self_if_same_hex` (typically less, e.g. infantry 1 vs 1)
- Firing at adjacent hex: clean target damage, attacker untouched
- Applies to all ground units (not just infantry — tanks too)

### Stacking

- **One unit per hex per domain layer** (ground / air / sea). Cross-domain
  co-location allowed (a fighter can fly over a tank).
- AOE / splash deferred to v2.
- Multiple strikes on same hex → damages **sum** (additive), applied
  simultaneously after all rolls.

### Concrete damage table per unit type

| Unit | range | dmg_target (G/A/S/F) | dmg_self_same_hex | ammo | consumes_carrier |
|---|---|---|---|---|---|
| infantry | 1 | 1 / 1 / — / 1 | 1 | -1 | false |
| armor | 1 | 2 / — / — / 2 | 1 | -1 | false |
| missile_launcher | 4 | — / 2 / 1 / — | n/a | 4 | false |
| scout_drone | n/a | (no strike) | — | — | — |
| strike_drone | 0 | 3 / — / 2 / 3 | (consumed) | 1 | **true** |
| fighter | 3 | 2 / — / 2 / 2 | n/a | 4 | false |
| destroyer | 4 | 2 / 2 / 2 / 2 | n/a | 6 | false |
| base | n/a | (no strike) | — | — | — |

`G/A/S/F` = ground / air / sea / fixed; `—` = ineligible.

---

## 4. Movement rules

- **One MOVE per unit per turn** (already enforced). Validate at intake.
- **Collisions**: two units want the same hex → deterministic tie-break by
  blake2b roll; loser stops one hex back along its path.
- **Hex swap** (A→B and B→A simultaneously): allowed; both succeed (units flow
  past each other on a tactical scale).
- **MOVE into enemy hex** (same-domain): blocked; mover stops one short.
  Cross-domain: allowed (fighter flies over infantry).
- **No auto-melee from move-into-enemy.** Combat requires explicit STRIKE.
- **MOVE while RADAR is ACTIVE**: the radar stays ON, but its `range` is
  **halved** for that turn. (Mobile radars degrade; soft rule, not hard
  deactivation.)

---

## 5. Sensors — typed, stateful

### Modalities (4, with SONAR as v2 stub)

| Modality | Active/Passive | Sees | Detectable |
|---|---|---|---|
| RADAR | ACTIVE (emits) or INACTIVE (silent) | ground / air / sea | yes (by enemy SIGINT) |
| EO/IR | passive | ground / sea (poor air), short range | no |
| SIGINT | passive (always listening) | only active emitters (radars) | no |
| SONAR (v2) | active or passive variants | sea-only | active is detectable |

### State machine (per sensor)

- RADAR: `ACTIVE` | `INACTIVE`. Sticky across turns.
- EO/IR, SIGINT: always passive (no state).
- Default at scenario start: **non-stealth platforms = `ACTIVE`**;
  **stealth platforms = `INACTIVE`** (you bought stealth, you'd want it on).

### `ACTIVATE` / `DEACTIVATE` actions

- **Free toggle** — does NOT consume the unit's tactical action slot.
- A unit can issue `ACTIVATE + MOVE` or `DEACTIVATE + STRIKE` in the same turn.
- Order shape: `pendingOrders[unitId] = { tactical?: Order, sensor?: 'ACTIVATE' | 'DEACTIVATE' }`
- Toggles **all** of a unit's emitter sensors at once (not per-sensor).

### Emission consequences

- Active RADAR is detectable by enemy SIGINT at up to **4× the radar's own range**
  (e.g., radar range 3 → SIGINT detects emission at range 12).
- Stealth + own emission: stealth is **disabled** for the turn the unit emits.
  (You can't be stealthy and ping at the same time.)
- OVERWATCH implies emission too — a SAM in OW mode is using fire-control radar.
  → While on OVERWATCH, the unit counts as `ACTIVE` for SIGINT purposes that turn.

### Per-unit sensor loadouts

| Unit | RADAR (range) | EO/IR (range) | SIGINT (range) |
|---|---|---|---|
| infantry | — | 1 | — |
| armor | — | 1 | — |
| missile_launcher | 2 active / 0 inactive | 1 | — |
| scout_drone | — | 4 | 3 |
| strike_drone | — | 1 | — |
| fighter | 2 active / 0 inactive | 1 | 4 (passive RWR) |
| destroyer | 3 active / 0 inactive | 2 | 5 (passive ESM) |
| base | 3 active / 0 inactive | — | 4 |

Note: `scout_drone` deliberately has **no radar** — its survival doctrine is
"silent, fast, sees far." Differentiates it from `fighter` which has active radar.

---

## 6. Action vocabulary (final 7)

| Key | Action | Notes |
|---|---|---|
| `M` | MOVE | tactical slot |
| `S` | STRIKE | tactical slot, hex-targeted |
| `V` | SCOUT | tactical slot |
| `O` | OVERWATCH | tactical slot, deals 50% damage on intercepts |
| `H` | HOLD | tactical slot |
| `A` | ACTIVATE | free, sets all emitters to ON |
| `D` | DEACTIVATE | free, sets all emitters to OFF |
| `X` | clear tactical order | |
| `Shift+X` | clear sensor toggle | |
| `ESC` | cancel targeting | |
| `Space` | lock current side / resolve | |

**Removed**: `CAPTURE`. With the new win condition, capture is automatic by
uncontested occupation at end-of-turn — no explicit order needed.
Action menu loses one button; `C` shortcut is freed.

### UI layout (right rail)

```
┌─ MOVEMENT ──────────────┐
│  MOVE         HOLD      │
├─ COMBAT ────────────────┤
│  STRIKE       OVERWATCH │
├─ SENSORS ───────────────┤
│  SCOUT        ACTIVATE  │  ← ACTIVATE/DEACTIVATE rendered
│               DEACTIVATE│    with dashed green border
└─────────────────────────┘    (signal "free / stackable")
```

### Compound order display

```
unit panel: "ACTIVATE + MOVE → (5,7)"
            "DEACTIVATE + STRIKE → (4,3)"
            "ACTIVATE"          (alone — no tactical action)
            "STRIKE → (4,3)"    (no sensor toggle)
```

---

## 7. Unit catalog — final 8 types

Single source of truth: `engine/unit_types.json`. Side is per-instance.

```
infantry        ground   HP 4  MV 2   capture-eligible
armor           ground   HP 6  MV 2
missile_launcher ground  HP 3  MV 0   stationary; finite missiles
scout_drone     air      HP 1  MV 4   pure ISR, no weapons, no radar
strike_drone    air      HP 1  MV 3   one-shot kamikaze; consumes_carrier
fighter         air      HP 3  MV 4   stealth flag, multirole strike
destroyer       sea      HP 8  MV 2   tankiest mobile unit
base            fixed    HP 12 MV 0   spawning deferred to v2
```

### Migration

1. Delete `engine/catalog/{platforms,sensors,weapons,bases}.py`
2. New `engine/catalog/loader.py` reads `engine/unit_types.json` once at startup
3. `engine/scenario.py`: replace platform/base lookups with `loader.get(type_key)`
4. Scenario YAML: bases become units of type `base` (drop separate `bases:` block)
5. `scripts/sample_terrain.py`: rewrite placement using 8 generic keys
6. Icons: 8 SVGs (one per type), tinted blue/red at runtime; delete the 18 old files
7. Frontend `types.ts`: `unit.type` is one of 8 strings; remove `platform_key`

---

## 8. Bugs in current code this design fixes

The "edge cases" agent found 4 real bugs in the shipped resolver:

1. **Posthumous strikes**: a unit killed in Phase B (overwatch) still fires
   its STRIKE in Phase C because dead-removal happens in Phase D. Fix: filter
   `hp > 0` at the top of every phase loop.
2. **`self_destruct` not enforced**: the flag exists in `unit_types.json` but
   the resolver never kills the attacker after firing. Strike drones are
   currently reusable. Fix: when `consumes_carrier=true`, mark attacker
   `hp = 0` after the strike resolves.
3. **CAPTURE order is dead code**: the resolver doesn't read `CaptureOrder`,
   it just checks "is a ground unit on the objective?" Removing CAPTURE
   action cleans this up. Capture happens automatically by uncontested
   occupation at end-of-turn.
4. **Friendly fire allowed**: `_is_visible(side, entity, …)` returns True for
   friendlies, so a Blue STRIKE on a Blue unit passes legality check. Fix:
   `if tgt.side == atk.side: continue`.
5. **Order rejection silent**: rejected orders just `continue` past the loop.
   Fix: emit `OrderRejectedEvent { unit, reason }` so BattleLog explains why.

---

## 9. Open tensions / decisions to make

| # | Question | Default lean |
|---|---|---|
| T1 | MOVE-first-then-STRIKE (option B) vs strict snapshot (option C) | **B** — simpler, matches existing code, satisfies user's quote |
| T2 | HP-loss threshold: 25%, 30%, or other? | **25%** (8-15 turn games) |
| T3 | Default RADAR state at scenario start | **ACTIVE for non-stealth, INACTIVE for stealth** |
| T4 | MOVE while RADAR active: full range, halved range, or auto-deactivate? | **Halved range** (soft rule) |
| T5 | Replace `pkill` floats with `target_domains: list` or keep float for "domain eligibility filter"? | **Replace with target_domains list** for clarity |
| T6 | Stealth + own emission semantics | **Stealth disabled this turn while emitting** |
| T7 | OVERWATCH damage: 50% of `damage_target`? | **50% deterministic** |
| T8 | Capture mechanism (now that CAPTURE action is gone) | **Automatic by uncontested occupation at end-of-turn** |
| T9 | Replay determinism with deterministic damage | **Trivially deterministic — no rolls in Phase C anymore. blake2b only needed for collisions and OVERWATCH timing.** |

---

## 10. Migration sequence

This is bigger than the sensor-pipeline integration. Roughly 4 PRs, can run
in parallel with `feat/fusion-integration` since they touch different files.

```
PR-A  Win condition rewrite             ~3h
        - drop Timer + 5-min wallclock
        - add HP threshold + objective-streak + 30-turn cap
        - rewrite compute_scores → compute_winner
        - update EndGameOverlay copy

PR-B  Catalog rewrite (12 named → 8 abstract)  ~5h
        - new engine/catalog/loader.py from unit_types.json
        - delete platforms.py / bases.py / sensors.py / weapons.py
        - update scenario.py + sample_terrain.py
        - rename + reduce icons to 8 SVGs

PR-C  Combat resolution (Pkill → deterministic + hex-targeted)  ~4h
        - StrikeOrder.target_hex (was target_id)
        - damage_target / damage_self_if_same_hex / target_domains
        - filter hp>0 at every phase loop entry
        - enforce consumes_carrier
        - friendly-fire guard
        - emit OrderRejectedEvent

PR-D  Sensor state machine                 ~4h
        - typed sensors (radar/eo_ir/sigint) per unit_types.json
        - is_emitting field on UnitInstance
        - ACTIVATE/DEACTIVATE actions (free toggle)
        - move-halves-radar-range rule
        - emission detection loop (SIGINT vs ACTIVE radars)
        - drop CAPTURE action; auto-capture on occupation
```

Total ~16h. Each PR can ship green independently.

---

## 11. Out of scope (defer)

- Helicopter / Submarine / Bomber / Transport unit types
- Per-base spawn capacity, missile reserve pools, reload at base
- Weapon reload over time
- AOE / splash damage
- Per-sensor (not per-unit) ACTIVATE
- Sub-impulse / initiative-based turn ordering
- LLM agent on the new rules
- Brian's fusion integration (separate plan in `INTEGRATION_PLAN.md`)

---

## 12. Sign-off needed

Before kicking off implementation:

1. **T1**: MOVE-first-then-STRIKE (B) or strict snapshot (C)?
2. **T2**: HP threshold percentage?
3. **T3**: Default radar state?
4. **T4**: Radar-while-moving rule (halved range vs auto-off)?
5. **T8**: Capture model (auto-by-occupation vs explicit action with new button)?
6. Sequence of PRs A/B/C/D — any blockers, any order changes?
7. Run this in parallel with `feat/fusion-integration`, or sequential?

---

## Revision history

- 2026-05-02 — initial design synthesized from 7 parallel agent reports
  (win condition, combat resolution, order semantics, weapon ammo,
  sensor state machine, unit catalog, action vocabulary, edge cases).
