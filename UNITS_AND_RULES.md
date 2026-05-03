# Units & Rules

The 9 unit types, what they do, and the rules of engagement. Deterministic
damage, no probability rolls. This is the canonical reference; everything
else is implementation.

---

## 1. Visibility

Map terrain and bases are **always** visible. There are no blackened tiles.

An enemy unit appears on your map iff one of:
- It is **currently** within range of one of your active sensors, **or**
- It was sensed previously **and** has not moved since.

The moment an enemy moves, its icon disappears from your map (last-known-
position is invalidated) until you re-spot it. So drones flying back over the
same airspace turn after turn matters; a scan once and never again means stale
intel.

Friendly units are always visible to you.

---

## 2. Sensors

Two kinds. That's it.

| Kind   | Always on? | Emits? | Range          |
|--------|------------|--------|----------------|
| passive view | yes  | no     | per-unit, short |
| radar  | toggle ON/OFF | yes (when ON) | per-unit, longer |

Any unit with radar **ON** is visible to all enemies at long range (the
emission gives it away). Turning radar OFF makes you stealthier but you only
see your passive radius.

A unit with no radar listed in the table simply can't toggle one; it always
runs on passive view.

---

## 3. Stealth

Some units are stealth (just fighters, today). Effect: any sensor's effective
range is **halved** when looking at a stealth unit. They can still be spotted
at close range.

---

## 4. Actions

These are all the actions in the game. Each unit's roster row says which it
has access to.

| Action            | What it does |
|-------------------|--------------|
| MOVE              | Pathfind up to `move_range` hexes. Blocked by enemy units. |
| STRIKE            | Fire weapon at a hex within range. See §5. |
| SCOUT             | The unit stays where it is. Every enemy in radius `scout_radius` around the unit's own hex is revealed this turn. Drone-only; passive (no emissions). Mutually exclusive with MOVE for the turn. |
| OVERWATCH         | Skip the strike phase, then auto-fire on the first enemy that moves into your weapon range during the enemy MOVE phase. One shot per turn. |
| DEFEND            | Unit does nothing offensive. Any incoming damage to this unit is **halved** (`⌈dmg/2⌉`, min 1) for the turn. Replaces HOLD as the "skip turn" action. |
| ACTIVATE_RADAR    | Turn radar ON. Free; combines with another action. |
| DEACTIVATE_RADAR  | Turn radar OFF. Free. |

There is no CAPTURE action. There are no objective hexes.

---

## 5. Strike rules (deterministic)

A strike order names a **hex**, not a target unit.

- **Adjacent strike** (range ≥ 1): every enemy unit in the target hex takes
  the weapon's full `damage`. Friendlies unaffected (no friendly fire).
- **Same-hex strike** (range 0, melee/kamikaze): defender takes `damage`;
  attacker takes `⌈damage/2⌉` counter-damage. If the weapon is one-shot
  self-destruct, the attacker dies regardless.
- **Stacked hexes**: every enemy in the hex takes the damage (a strike on a
  hex with three units hits all three).
- **Move-out dodge**: strikes resolve **after** the MOVE phase. If the target
  hex is empty by then, the strike whiffs — but **the ammo is still spent**,
  and a one-shot self-destruct attacker still dies. You don't get the
  munition back.
- **Defender's bonus**: if a unit was issued DEFEND this turn, any damage
  it would take (from STRIKE or OVERWATCH) is halved (`⌈dmg/2⌉`, min 1).
- **Ammo**: `∞` never decrements; finite ammo decrements once per STRIKE
  order, hit or miss. `0` ammo disables STRIKE.

---

## 6. Win conditions

End-of-turn check, priority order:

1. **Annihilation** — opposing side has 0 units AND 0 bases → win.
2. **HP collapse** — total HP (units + bases) ≤ 25% of starting → other side
   wins. Both sides crossing simultaneously → higher HP% wins; tie → draw.
3. **Turn cap** — turn ≥ 30 → higher HP% wins; tie = draw.

(Objective-hold is gone along with the objective-hex concept.)

---

## 7. Roster — 9 types

Both sides draw from the same roster. Numbers are identical; only the glyph
color differs.

### infantry — ground

| HP | Move | View | Radar | Stealth | Actions                                |
|----|------|------|-------|---------|----------------------------------------|
| 4  | 2    | 2    | —     | -       | MOVE, STRIKE, OVERWATCH, DEFEND        |

| Weapon          | Range | Damage | Ammo | Hits         |
|-----------------|-------|--------|------|--------------|
| rifle / MANPADS | 1     | 1      | ∞    | ground, air  |

Cheap, plentiful, can engage low-flying air with manpads.

---

### armor — ground

| HP | Move | View | Radar | Stealth | Actions                          |
|----|------|------|-------|---------|----------------------------------|
| 6  | 2    | 1    | —     | -       | MOVE, STRIKE, OVERWATCH, DEFEND  |

| Weapon | Range | Damage | Ammo | Hits   |
|--------|-------|--------|------|--------|
| gun    | 1     | 2      | ∞    | ground |

Heavy hitter, blind on its own — needs a scout's help to find targets.

---

### missile_launcher — ground, stationary

| HP | Move | View | Radar     | Stealth | Actions                                                              |
|----|------|------|-----------|---------|----------------------------------------------------------------------|
| 3  | 0    | 1    | range 4   | -       | STRIKE, OVERWATCH, DEFEND, ACTIVATE_RADAR, DEACTIVATE_RADAR          |

| Weapon | Range | Damage | Ammo | Hits      |
|--------|-------|--------|------|-----------|
| SAM    | 4     | 2      | 4    | air, sea  |

Long-range AAW. **Must turn radar ON to see beyond range 1.** Trade-off:
when radar's on, every enemy can see you.

---

### scout_drone — air

| HP | Move | View | Radar | Stealth | Actions                |
|----|------|------|-------|---------|------------------------|
| 1  | 4    | 2    | —     | -       | MOVE, SCOUT, DEFEND    |

| Action stat   | Value |
|---------------|-------|
| scout_radius  | 4     |

No weapon. Eyes of the team. Two modes per turn:
- **MOVE**: fly up to 4 hexes, sees enemies in radius 2 around its new position.
- **SCOUT**: stay put, reveal every enemy in radius 4 around current hex.

---

### strike_drone — air, kamikaze

| HP | Move | View | Radar | Stealth | Actions          |
|----|------|------|-------|---------|------------------|
| 1  | 3    | 1    | —     | -       | MOVE, STRIKE     |

| Weapon | Range | Damage | Ammo | Hits             | Notes                          |
|--------|-------|--------|------|------------------|--------------------------------|
| OWA warhead | 0 | 3 | 1 | ground, sea, air | self-destruct: attacker dies after firing (whiff or not) |

One-shot loitering munition. Fly into target hex, detonate.

---

### fighter — air, stealth

| HP | Move | View | Radar    | Stealth | Actions                                                              |
|----|------|------|----------|---------|----------------------------------------------------------------------|
| 3  | 4    | 1    | range 3  | Y       | MOVE, STRIKE, OVERWATCH, DEFEND, ACTIVATE_RADAR, DEACTIVATE_RADAR    |

| Weapon  | Range | Damage | Ammo | Hits             |
|---------|-------|--------|------|------------------|
| AAM/ASM | 3     | 2      | 4    | ground, air, sea |

Fast, multirole. Stealth halves enemy detector ranges; radar ON breaks that
advantage at long range.

---

### bomber — air

| HP | Move | View | Radar | Stealth | Actions                  |
|----|------|------|-------|---------|--------------------------|
| 5  | 2    | 1    | —     | -       | MOVE, STRIKE, DEFEND     |

| Weapon       | Range | Damage | Ammo | Hits         |
|--------------|-------|--------|------|--------------|
| heavy bombs  | 2     | 4      | 2    | ground, sea  |

Slow, fragile-ish, tiny magazine, but the hardest hitter in the game per
shot. No air-to-air capability — needs fighter cover. Cannot dogfight
(no overwatch).

---

### destroyer — sea

| HP | Move | View | Radar    | Stealth | Actions                                                              |
|----|------|------|----------|---------|----------------------------------------------------------------------|
| 8  | 2    | 1    | range 3  | -       | MOVE, STRIKE, OVERWATCH, DEFEND, ACTIVATE_RADAR, DEACTIVATE_RADAR    |

| Weapon          | Range | Damage | Ammo | Hits             |
|-----------------|-------|--------|------|------------------|
| missile battery | 4     | 2      | 6    | ground, air, sea |

Big HP, big magazine, big radar. Wants radar ON most of the time but pays
the visibility tax for it.

---

### base — fixed

| HP | Move | View | Radar    | Stealth | Actions                                                |
|----|------|------|----------|---------|--------------------------------------------------------|
| 12 | 0    | 2    | range 4  | -       | DEFEND, ACTIVATE_RADAR, DEACTIVATE_RADAR               |

| Weapon         | Range | Damage | Ammo | Hits         |
|----------------|-------|--------|------|--------------|
| point defense  | 2     | 1      | ∞    | ground, air  |

Stationary HP bank. One base type for everyone — what it spawns (planned
mechanic) is set per-instance in the scenario, not by base subtype.

---

## 8. Quick-reference table

| Type             | Dom | HP | Mv | View | Radar | Wpn rng | Wpn dmg | Ammo | Stealth |
|------------------|-----|----|----|------|-------|---------|---------|------|---------|
| infantry         | gnd | 4  | 2  | 2    | —     | 1       | 1       | ∞    | -       |
| armor            | gnd | 6  | 2  | 1    | —     | 1       | 2       | ∞    | -       |
| missile_launcher | gnd | 3  | 0  | 1    | 4     | 4       | 2       | 4    | -       |
| scout_drone      | air | 1  | 4  | 2*   | —     | —       | —       | —    | -       |
| strike_drone     | air | 1  | 3  | 1    | —     | 0†      | 3       | 1    | -       |
| fighter          | air | 3  | 4  | 1    | 3     | 3       | 2       | 4    | Y       |
| bomber           | air | 5  | 2  | 1    | —     | 2       | 4       | 2    | -       |
| destroyer        | sea | 8  | 2  | 1    | 3     | 4       | 2       | 6    | -       |
| base             | fix | 12 | 0  | 2    | 4     | 2       | 1       | ∞    | -       |

† strike_drone is one-shot self-destruct.
\* scout_drone passive view is 2 while moving; SCOUT action reveals radius 4 if drone stays put.

---

## 9. Open questions

Need a yes/no before catalog rewrite (PR-B):

1. **Counter-damage on melee** — `⌈dmg/2⌉` or flat 1?
2. **Bases per side at scenario start** — 1, or 2-3?
3. **Spawn mechanic** in scope for the hackathon, or are bases just HP banks for v1?
4. **Stealth halves view range only, or radar too?** (My take: both — emission is the only thing stealth doesn't help with, and radar ON exposes you anyway.)
5. **Last-known-position memory** — persist forever until the unit moves, or decay after N turns even if stationary? (My take: forever — one move clears it.)
6. **Bomber overwatch?** — currently no. If you want bombers reactively dropping on movers, easy to flip.
