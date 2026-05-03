# Units & Rules — Canonical Spec

This document is the source of truth for every unit in the game and how they
interact. Numbers are deterministic — no probability rolls, no `pkill`. If a
strike connects, damage is applied as stated. Reflects all design decisions
locked in PR-A and the active design doc.

---

## 1. Global rules

### 1.1 Turn flow
Pure turn-based, no wallclock. Each turn:

1. **Queue** — Blue queues all orders, then Red queues all orders. (UI presents
   one side at a time, or hot-seat.)
2. **Resolve** — Server runs phases A→D in fixed order, emits an event stream,
   client animates.
3. Repeat until a win condition fires (see §1.6).

### 1.2 Resolution phases (resolver order, not user-visible)
1. **SCOUT** — scout/observe actions reveal hexes for this turn.
2. **MOVE** — every MOVE order applies its planned path. Collisions resolve by
   seed-based tiebreak (loser stops one hex back). OVERWATCH may interrupt.
3. **STRIKE** — strikes target a hex; damage is computed against whoever is in
   that hex *after* moves. **A unit that moved out of the targeted hex dodges.**
4. **UPDATE** — dead units removed, objective streak ticks, win check.

### 1.3 Strike rules (deterministic)
- A strike order names a **hex**, not a unit ID.
- **Adjacent strike** (range ≥ 1): every enemy unit on the target hex takes the
  weapon's full `damage`. Friendlies in the hex are unaffected (no friendly
  fire).
- **Same-hex strike** (range 0, melee / kamikaze): both attacker and defender
  take damage. Defender takes `damage`. Attacker takes `⌈damage / 2⌉` counter-
  damage. If the weapon is `self_destruct`, the attacker dies regardless.
- **Multiple defenders in hex**: every enemy unit in the hex takes `damage`
  (a strike on a stacked hex hits everything stacked there).
- **Ammo**: weapons with finite `ammo` decrement on each strike. At 0, the
  STRIKE action is disabled. `ammo: ∞` never decrements.
- **One-shot**: `ammo: 1, self_destruct: true` — single use, kills attacker.

### 1.4 Sensing & visibility
Every unit has a **passive sensor** (always on, no emissions, short range).
Some units also have a **radar** that can be toggled.

| Sensor kind | Always on? | Emits?     | Detects stealth? | Notes                  |
|-------------|------------|------------|------------------|------------------------|
| passive eo  | yes        | no         | yes (LOS-y)      | every unit has one     |
| radar       | toggle     | when ACTIVE| no (vs stealth)  | range > eo when on     |
| sigint      | yes        | no         | n/a              | only sees radars-on    |

Default observation = each unit sees out to its `sensor_range` via its passive
EO. Radar doubles or extends the range *only when ACTIVE*. **A scout drone is
not special — it just has a long passive eo range built in** (passive 4 vs
infantry's passive 2). The optional `SCOUT` action lets *any* unit deliberately
boost its sensor range for one turn.

### 1.5 Stealth
A unit flagged `stealth: true` halves any enemy detector's effective range
against it (eo, radar, sigint). Stealth fighters can still be picked up at
close range.

### 1.6 Win conditions (PR-A, shipped)
Evaluated end-of-turn, in priority order:
1. **Objective hold** — a side has held *every* objective hex uncontested for
   `victory.objective_hold_turns` (default 3) consecutive turns → that side
   wins.
2. **Annihilation** — opposing side has 0 units AND 0 bases → win.
3. **HP collapse** — a side's total HP (units + bases) drops to ≤
   `victory.hp_loss_threshold` (default 25%) of starting HP → other side wins.
4. **Turn cap** — `state.turn ≥ victory.turn_cap` (default 30) → higher HP%
   wins, tie = draw.

---

## 2. Action catalog

These are the only actions in the game. Each unit's "Actions" row below lists
which ones it has.

| Action            | Params (in order)             | Effect                                                     |
|-------------------|-------------------------------|------------------------------------------------------------|
| MOVE              | `target_hex`                  | Pathfind up to `move_range`. Blocked by enemy units.       |
| STRIKE            | `target_hex`                  | Fires weapon at hex. See §1.3.                             |
| SCOUT             | `target_hex`                  | Doubles `sensor_range` toward that hex this turn.          |
| OVERWATCH         | —                             | Holds fire. Auto-strikes first enemy entering strike range during enemy MOVE phase. One shot per turn. |
| ACTIVATE_RADAR    | —                             | Turn radar ON. Free; combines with another action.         |
| DEACTIVATE_RADAR  | —                             | Turn radar OFF. Free.                                      |
| HOLD              | —                             | Skip turn. (Reserved for future repair/regen mechanics.)   |
| CAPTURE           | —                             | Ground units only, on an objective hex. Ticks streak.      |

`ACTIVATE_RADAR / DEACTIVATE_RADAR` are only available to units whose `radar`
field is non-null.

---

## 3. Unit roster (8 types)

Both sides draw from the same roster. Side is set per instance; numbers are
identical (red just gets a different glyph color).

### 3.1 Infantry (ground)

| Field | Value |
|-------|-------|
| domain | ground |
| HP | 4 |
| move | 2 |
| passive sensor | eo, range 2 |
| radar | — |
| stealth | no |

**Actions:** MOVE, STRIKE, OVERWATCH, HOLD, CAPTURE

| Weapon  | Range | Damage | Ammo | Notes                            |
|---------|-------|--------|------|----------------------------------|
| rifle/MANPADS | 1 | 1 | ∞ | hits ground or air in target hex |

Role: only ground unit that can capture objectives. Can engage low-flying air
in adjacent hex (manpads) but at low damage.

---

### 3.2 Armor (ground)

| Field | Value |
|-------|-------|
| domain | ground |
| HP | 6 |
| move | 2 |
| passive sensor | eo, range 1 |
| radar | — |
| stealth | no |

**Actions:** MOVE, STRIKE, OVERWATCH, HOLD

| Weapon | Range | Damage | Ammo | Notes                |
|--------|-------|--------|------|----------------------|
| gun    | 1     | 2      | ∞    | ground only — cannot hit air or sea |

Role: ground heavy hitter, slow sensors. Cannot capture (no infantry on top).

---

### 3.3 Missile Launcher / SAM (ground, stationary)

| Field | Value |
|-------|-------|
| domain | ground |
| HP | 3 |
| move | 0 (stationary) |
| passive sensor | eo, range 1 |
| radar | range 4, toggle ACTIVATE/DEACTIVATE |
| stealth | no |

**Actions:** STRIKE, OVERWATCH, ACTIVATE_RADAR, DEACTIVATE_RADAR, HOLD

| Weapon | Range | Damage | Ammo | Notes                             |
|--------|-------|--------|------|-----------------------------------|
| SAM    | 4     | 2      | 4    | hits air or sea; ground = no damage |

Role: long-range AAW. **Must ACTIVATE radar to see beyond range 1.** Becomes
visible to enemy sigint while radar is ON.

---

### 3.4 Scout Drone (air)

| Field | Value |
|-------|-------|
| domain | air |
| HP | 1 |
| move | 4 |
| passive sensor | eo, range 4 |
| radar | — |
| stealth | no |

**Actions:** MOVE, SCOUT, HOLD

No weapon. Pure ISR. Default observation already covers a wide radius —
scout drones don't need to spend a `SCOUT` action to do their job, they just
fly around. The SCOUT action exists if you want to push range to 8 for one
turn (e.g. peek over a ridge).

---

### 3.5 Strike Drone (air, kamikaze)

| Field | Value |
|-------|-------|
| domain | air |
| HP | 1 |
| move | 3 |
| passive sensor | eo, range 1 |
| radar | — |
| stealth | no |

**Actions:** MOVE, STRIKE

| Weapon | Range | Damage | Ammo | Notes                       |
|--------|-------|--------|------|-----------------------------|
| OWA warhead | 0 | 3 | 1 | self-destruct — attacker dies after firing |

Role: one-shot loitering munition. Fly into target hex, detonate.

---

### 3.6 Fighter (air, stealth)

| Field | Value |
|-------|-------|
| domain | air |
| HP | 3 |
| move | 4 |
| passive sensor | eo, range 1 |
| radar | range 3, toggle ACTIVATE/DEACTIVATE |
| stealth | yes |

**Actions:** MOVE, SCOUT, STRIKE, OVERWATCH, ACTIVATE_RADAR, DEACTIVATE_RADAR, HOLD

| Weapon | Range | Damage | Ammo | Notes                            |
|--------|-------|--------|------|----------------------------------|
| AAM/ASM | 3 | 2 | 4 | hits air, ground, or sea         |

Role: multirole flex. Stealth halves enemy detector ranges. Radar lets it see
out to 3 but blows its stealth advantage to enemy sigint while ACTIVE.

---

### 3.7 Destroyer (sea)

| Field | Value |
|-------|-------|
| domain | sea |
| HP | 8 |
| move | 2 |
| passive sensor | eo, range 1 |
| radar | range 3, toggle ACTIVATE/DEACTIVATE |
| stealth | no |

**Actions:** MOVE, STRIKE, OVERWATCH, ACTIVATE_RADAR, DEACTIVATE_RADAR, HOLD

| Weapon | Range | Damage | Ammo | Notes                        |
|--------|-------|--------|------|------------------------------|
| missile battery | 4 | 2 | 6 | hits air, sea, or ground     |

Role: sea-going AAW + ASuW + land-attack. Big HP pool, big ammo pool. Usually
wants radar on; relies on sigint detection of incoming threats while OFF.

---

### 3.8 Base (fixed)

| Field | Value |
|-------|-------|
| domain | fixed (cannot move, cannot be destroyed by routine fire — but does take HP damage) |
| HP | 12 |
| move | 0 |
| passive sensor | eo, range 2 |
| radar | range 4, toggle ACTIVATE/DEACTIVATE |
| stealth | no |

**Actions:** ACTIVATE_RADAR, DEACTIVATE_RADAR, HOLD (future: SPAWN)

| Weapon | Range | Damage | Ammo | Notes                  |
|--------|-------|--------|------|------------------------|
| point defense | 2 | 1 | ∞ | hits air or ground in adjacent hex |

Role: home for spawning new units (planned), also a strategic HP-bank. Same
type for everyone — what a base spawns is a per-instance scenario field, not
a base subtype. Side gets one or more bases at scenario load.

---

## 4. Quick reference table

| Type             | Dom | HP | Mv | Pass.eo | Radar    | Wpn rng | Wpn dmg | Ammo | Stealth | Capture |
|------------------|-----|----|----|---------|----------|---------|---------|------|---------|---------|
| infantry         | gnd | 4  | 2  | 2       | —        | 1       | 1       | ∞    | -       | Y       |
| armor            | gnd | 6  | 2  | 1       | —        | 1       | 2       | ∞    | -       | -       |
| missile_launcher | gnd | 3  | 0  | 1       | 4 toggle | 4       | 2       | 4    | -       | -       |
| scout_drone      | air | 1  | 4  | 4       | —        | —       | —       | —    | -       | -       |
| strike_drone     | air | 1  | 3  | 1       | —        | 0       | 3       | 1†   | -       | -       |
| fighter          | air | 3  | 4  | 1       | 3 toggle | 3       | 2       | 4    | Y       | -       |
| destroyer        | sea | 8  | 2  | 1       | 3 toggle | 4       | 2       | 6    | -       | -       |
| base             | fix | 12 | 0  | 2       | 4 toggle | 2       | 1       | ∞    | -       | -       |

† strike_drone is one-shot self-destruct — attacker dies after firing.

---

## 5. Open questions for sign-off

These need a yes/no before PR-B (catalog rewrite) starts:

1. **Cross-domain damage** — current spec says any weapon with a non-zero
   range hits any enemy in the target hex regardless of domain. Should some
   weapons be domain-restricted? (e.g. armor's gun can't hit air = current
   `unit_types.json`.) **My take: keep it cross-domain for simplicity; flavor
   it later.** Confirm or override.
2. **Counter-damage on melee** — is `⌈damage / 2⌉` the right number for the
   attacker, or should it be a flat 1?
3. **Number of bases per side at scenario start** — 1, or up to 3 (one per
   spawn category)?
4. **Spawn mechanic** — is it in scope for the hackathon, or do bases just sit
   there as HP banks for v1?
5. **Stealth applies to which sensors** — all three (eo, radar, sigint), or
   just radar+eo? (Sigint sees emitters by definition; stealth shouldn't help
   if you're radiating.) **My take: stealth halves eo and radar only; sigint
   sees you the moment you ACTIVATE.**
