"""Mission planner — generate per-turn Orders from standing Missions.

This is the operational layer over the existing tactical resolver. The player
plans missions ("scout drone go reconnoiter the eastern peninsula, surveil only";
"destroyer establish picket at hex (10,5), engage hostile contacts"), and each
turn the planner emits concrete Orders based on the mission spec + current
state + Rules of Engagement.

After each `resolve_turn`, halt conditions are evaluated. Any unit hitting a
halt condition pauses the auto-resolve loop so the player can intervene.

ROE semantics:
- engage: if a hostile contact is in this unit's weapon range, STRIKE it
  (highest existence × leading-class-prob target). Otherwise advance toward
  mission target.
- surveil: never engage. Just advance. Useful for scouts/drones that want
  to see without revealing.
- avoid: if any hostile contact is within sensor range, MOVE directly away
  from the nearest one. Else advance.
"""
from __future__ import annotations

from typing import Optional

from engine.hex import Hex, distance, in_bounds, neighbors
from engine.orders import (
    HoldOrder,
    MoveOrder,
    Order,
    StrikeOrder,
)
from engine.state import Contact, GameState, Mission, UnitInstance


# ---------------------------------------------------------------------------
# Halt evaluation
# ---------------------------------------------------------------------------

def _hp_pct(unit: UnitInstance) -> float:
    if unit.max_hp <= 0:
        return 1.0
    return unit.hp / unit.max_hp


def _has_ammo(unit: UnitInstance) -> bool:
    if not unit.weapons:
        return False  # no weapons at all (drones, etc.) don't trigger no-ammo halt
    return any(w.ammo != 0 for w in unit.weapons)


def evaluate_halts(
    state: GameState,
    prev_contact_ids_by_side: dict[str, set[str]],
    started_at_target: set[str],
    turns_into_run: int,
) -> list[tuple[str, str]]:
    """Return [(unit_id, halt_reason), ...] for any halt that fired this cycle.

    `prev_contact_ids_by_side`: contact ids at start of auto-resolve loop.
    `started_at_target`: unit ids that were already at their mission target
        before the loop started (so target_reached doesn't fire spuriously).
    """
    halts: list[tuple[str, str]] = []
    new_contacts_by_side: dict[str, set[str]] = {
        side: {c.contact_id for c in state.contacts.get(side, [])} - prev_contact_ids_by_side.get(side, set())
        for side in ("blue", "red")
    }

    for unit in state.units:
        m = state.missions.get(unit.id)
        if m is None:
            continue
        # Reached target — only fires if the unit MOVED to target during the
        # auto-resolve loop. Units that started at target never fire this halt
        # (they're holding intentionally).
        if unit.id not in started_at_target and (unit.col, unit.row) == tuple(m.target_hex):
            halts.append((unit.id, "target_reached"))
            continue
        # New contact for this side?
        if m.halt_on_contact and new_contacts_by_side.get(unit.side):
            halts.append((unit.id, "new_contact"))
            continue
        # Low HP?
        if m.halt_on_low_hp and _hp_pct(unit) < 0.5:
            halts.append((unit.id, "low_hp"))
            continue
        # No ammo?
        if m.halt_on_no_ammo and not _has_ammo(unit):
            # Skip non-strikers (no weapons at all). Ignore if unit naturally has no weapons.
            if unit.weapons:
                halts.append((unit.id, "no_ammo"))
                continue
        # Per-mission turn cap
        if turns_into_run >= m.max_turns:
            halts.append((unit.id, "mission_max_turns"))
            continue

    return halts


# ---------------------------------------------------------------------------
# Per-step order generation
# ---------------------------------------------------------------------------

def _next_step_toward(
    src_col: int, src_row: int, dst_col: int, dst_row: int,
    cols: int, rows: int, blocked: set[tuple[int, int]],
) -> Optional[tuple[int, int]]:
    """Greedy: pick the in-bounds neighbor that minimizes hex distance to dst,
    avoiding blocked hexes. None if already at dst or no progress possible."""
    if (src_col, src_row) == (dst_col, dst_row):
        return None
    cur = Hex(src_col, src_row)
    target = Hex(dst_col, dst_row)
    best: Optional[tuple[int, int]] = None
    best_d = distance(cur, target)
    for n in neighbors(cur):
        if not in_bounds(n, cols, rows):
            continue
        if (n.col, n.row) in blocked:
            continue
        d = distance(n, target)
        if d < best_d:
            best_d = d
            best = (n.col, n.row)
    return best


def _step_away_from(
    src_col: int, src_row: int, threat_col: int, threat_row: int,
    cols: int, rows: int, blocked: set[tuple[int, int]],
) -> Optional[tuple[int, int]]:
    """Greedy: pick the in-bounds neighbor that maximizes distance from threat."""
    cur = Hex(src_col, src_row)
    threat = Hex(threat_col, threat_row)
    best: Optional[tuple[int, int]] = None
    best_d = distance(cur, threat)
    for n in neighbors(cur):
        if not in_bounds(n, cols, rows):
            continue
        if (n.col, n.row) in blocked:
            continue
        d = distance(n, threat)
        if d > best_d:
            best_d = d
            best = (n.col, n.row)
    return best


def _strike_priority(c: Contact) -> float:
    """Higher = more urgent to strike. Joint confidence × class threat weight."""
    THREAT_WEIGHT = {
        "missile_launcher": 3.0,   # SAM is the biggest deal — kill before it kills
        "fighter":          2.5,
        "destroyer":        2.5,
        "bomber":           2.0,
        "armor":            1.5,
        "strike_drone":     1.5,
        "scout_drone":      0.8,
        "infantry":         1.0,
    }
    leading_cls, leading_p = max(c.class_probs.items(), key=lambda kv: kv[1]) if c.class_probs else ("?", 0)
    threat = THREAT_WEIGHT.get(leading_cls, 1.0)
    return c.existence * leading_p * threat


def _select_strike_target(
    unit: UnitInstance,
    enemy_contacts: list[Contact],
) -> Optional[Contact]:
    """Pick the highest-priority contact within unit's weapon range, with at
    least medium confidence. Returns None if no good target."""
    if not unit.weapons or unit.weapon == 0:
        return None
    candidates = []
    for c in enemy_contacts:
        if c.existence < 0.30:
            continue   # too uncertain to commit ammo
        d = distance(Hex(unit.col, unit.row), Hex(c.believed_col, c.believed_row))
        if d > unit.weapon:
            continue
        candidates.append(c)
    if not candidates:
        return None
    return max(candidates, key=_strike_priority)


def _nearest_contact(
    unit: UnitInstance,
    enemy_contacts: list[Contact],
) -> Optional[Contact]:
    if not enemy_contacts:
        return None
    return min(
        enemy_contacts,
        key=lambda c: distance(Hex(unit.col, unit.row), Hex(c.believed_col, c.believed_row)),
    )


def generate_orders_from_missions(state: GameState, side: str) -> list[Order]:
    """Emit one Order per unit on `side` that has a Mission. Units without
    a mission emit no order (the resolver treats that as HOLD by default)."""
    orders: list[Order] = []
    cols, rows = state.map.cols, state.map.rows
    enemy_side = "red" if side == "blue" else "blue"
    enemy_contacts = state.contacts.get(side, [])  # MY view of THEM
    blocked: set[tuple[int, int]] = {(u.col, u.row) for u in state.units}

    for unit in state.units:
        if unit.side != side:
            continue
        m = state.missions.get(unit.id)
        if m is None:
            continue
        # Don't issue orders for stationary platforms with no remaining target choice.
        # (They'll just HOLD by default.)

        # ROE: engage — first, if a high-priority contact is in weapon range, strike it.
        if m.roe == "engage":
            tgt = _select_strike_target(unit, enemy_contacts)
            if tgt is not None:
                orders.append(StrikeOrder(
                    unit_id=unit.id,
                    target_hex=(tgt.believed_col, tgt.believed_row),
                    intent=f"engage contact ({tgt.contact_id}) per mission",
                ))
                continue

        # ROE: avoid — if any contact in this unit's sensor range, step away.
        if m.roe == "avoid" and enemy_contacts:
            nearest = _nearest_contact(unit, enemy_contacts)
            if nearest is not None:
                d = distance(Hex(unit.col, unit.row), Hex(nearest.believed_col, nearest.believed_row))
                if d <= max(unit.sensor, 1):
                    step = _step_away_from(
                        unit.col, unit.row, nearest.believed_col, nearest.believed_row,
                        cols, rows, blocked - {(unit.col, unit.row)},
                    )
                    if step is not None:
                        orders.append(MoveOrder(
                            unit_id=unit.id, target_hex=step,
                            intent="evade contact per ROE: avoid",
                        ))
                        continue

        # Default: advance one step toward target (skip if speed=0)
        if unit.speed <= 0:
            orders.append(HoldOrder(unit_id=unit.id, intent="stationary (no movement)"))
            continue
        # Compute target this turn — speed-many hex hops, but for simplicity
        # take the nearest neighbor toward target (engine.movement applies one
        # step per Move via path_to). speed > 1 still moves faster because
        # engine.movement.path_to expands a path of up to `speed` hexes.
        step = _next_step_toward(
            unit.col, unit.row, m.target_hex[0], m.target_hex[1],
            cols, rows, blocked - {(unit.col, unit.row)},
        )
        if step is None:
            orders.append(HoldOrder(unit_id=unit.id, intent="at target hex"))
        else:
            # Tell the resolver where we ULTIMATELY want to go — its movement
            # planner will path up to `speed` hexes per turn. The waypoint is
            # the mission's final hex, not the next-step neighbor.
            orders.append(MoveOrder(
                unit_id=unit.id,
                target_hex=tuple(m.target_hex),
                intent=f"advance to {tuple(m.target_hex)} per mission",
            ))

    return orders


# ---------------------------------------------------------------------------
# Auto-resolve loop
# ---------------------------------------------------------------------------

def snapshot_contact_ids(state: GameState) -> dict[str, set[str]]:
    return {side: {c.contact_id for c in state.contacts.get(side, [])} for side in ("blue", "red")}


def default_unmissioned_order(unit: UnitInstance, state: GameState) -> Order:
    """What a unit does when it has no mission/queued order this turn.

    Defense doctrine: any unit with weapons + ammo + a credible contact in
    weapon range goes to OVERWATCH (auto-fire on enemies that move into
    range). Units that can't shoot, or have nothing nearby, HOLD.
    """
    if not unit.weapons or unit.weapon == 0:
        return HoldOrder(unit_id=unit.id)
    has_ammo = any(w.ammo != 0 for w in unit.weapons)
    if not has_ammo:
        return HoldOrder(unit_id=unit.id)
    enemy_contacts = state.contacts.get(unit.side, [])
    for c in enemy_contacts:
        if c.existence < 0.30:
            continue
        d = distance(Hex(unit.col, unit.row), Hex(c.believed_col, c.believed_row))
        if d <= unit.weapon:
            from engine.orders import OverwatchOrder
            return OverwatchOrder(
                unit_id=unit.id,
                intent="defensive overwatch — credible contact in weapon range",
            )
    return HoldOrder(unit_id=unit.id)
