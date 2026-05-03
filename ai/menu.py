"""Per-unit legal-action menu builder.

For each unit on the AI's side, builds a SHORT list of legal actions tagged
with stable integer IDs. The LLM picks an `action_id` per unit; we map back
to a concrete Order via `validate.action_to_order`.

Pruning policy keeps the menu small (≈5–10 entries per unit) so the prompt
stays under budget. Tactical-interest filter for MOVE; domain + range filter
for STRIKE; ACTIVATE/DEACTIVATE only when the unit has a radar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.hex import Hex, distance
from engine.movement import reachable
from engine.state import GameState, UnitInstance


# Reserve ID 99 for the CUSTOM escape hatch — LLM can submit free-form
# coordinates if the menu is too restrictive. Validator snaps to legal.
CUSTOM_ACTION_ID = 99


@dataclass
class MenuItem:
    """One option in a unit's per-turn menu."""
    action_id: int
    kind: str           # "HOLD" | "OVERWATCH" | "SCOUT" | "MOVE" | "STRIKE"
                        # | "ACTIVATE_RADAR" | "DEACTIVATE_RADAR" | "CUSTOM"
    label: str          # human-readable description shown to the LLM
    target_hex: Optional[Tuple[int, int]] = None
    sensor_key: Optional[str] = None
    # Free-form bag for diagnostics (e.g. distance, terrain at target)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UnitMenu:
    unit_id: str
    unit_type: str
    items: List[MenuItem]

    def find(self, action_id: int) -> Optional[MenuItem]:
        for it in self.items:
            if it.action_id == action_id:
                return it
        return None


# ----------------------------------------------------------------------
# Builder

def build_menus(state: GameState, side: str) -> Dict[str, UnitMenu]:
    """Build a per-unit menu for every controllable unit on `side`."""
    out: Dict[str, UnitMenu] = {}
    for u in state.units:
        if u.side != side:
            continue
        out[u.id] = _build_one(state, u)
    return out


def _build_one(state: GameState, u: UnitInstance) -> UnitMenu:
    items: List[MenuItem] = []
    next_id = iter(range(1, 90)).__next__  # 1..89; reserve 99 for CUSTOM

    # 1. HOLD — always available.
    items.append(MenuItem(
        action_id=next_id(), kind="HOLD",
        label="HOLD — stand fast (takes full damage)",
    ))

    # 2. OVERWATCH — iff the unit has a usable weapon.
    if _has_usable_weapon(u):
        items.append(MenuItem(
            action_id=next_id(), kind="OVERWATCH",
            label=f"OVERWATCH — auto-fire on movers within range {u.weapon}",
        ))

    # 3. SCOUT — drone-only, free-action sensor pulse.
    if u.type == "scout_drone":
        items.append(MenuItem(
            action_id=next_id(), kind="SCOUT",
            label="SCOUT — stay put; reveal radius 4 around current hex",
        ))

    # 4. ACTIVATE / DEACTIVATE radar — for units carrying a radar sensor.
    radars = [s for s in u.sensors if s.modality == "radar"]
    for sn in radars:
        if not sn.is_active:
            items.append(MenuItem(
                action_id=next_id(), kind="ACTIVATE_RADAR",
                label=(f"ACTIVATE {sn.key} — sensor range jumps to {sn.range} "
                       "but unit emits (visible to enemy sigint at long range)"),
                sensor_key=sn.key,
            ))
        else:
            items.append(MenuItem(
                action_id=next_id(), kind="DEACTIVATE_RADAR",
                label=f"DEACTIVATE {sn.key} — go dark, fall back to passive sensors",
                sensor_key=sn.key,
            ))

    # 5. MOVE — pruned tactical-interest list.
    if u.speed > 0:
        for hx in _move_candidates(state, u):
            items.append(MenuItem(
                action_id=next_id(), kind="MOVE",
                label=_move_label(state, u, hx),
                target_hex=hx,
            ))

    # 6. STRIKE — only target hexes with visible enemies in range + domain.
    for hx in _strike_candidates(state, u):
        items.append(MenuItem(
            action_id=next_id(), kind="STRIKE",
            label=_strike_label(state, u, hx),
            target_hex=hx,
        ))

    # 7. CUSTOM escape hatch — free-form coords. The LLM should rarely
    # need this; if it does, the validator snaps to nearest legal hex.
    items.append(MenuItem(
        action_id=CUSTOM_ACTION_ID, kind="CUSTOM",
        label=("CUSTOM — emit raw {kind, target_hex} when no menu option "
               "fits. Coords will be snapped to nearest legal hex."),
    ))

    return UnitMenu(unit_id=u.id, unit_type=u.type, items=items)


# ----------------------------------------------------------------------
# Pruning helpers

def _has_usable_weapon(u: UnitInstance) -> bool:
    """Has at least one weapon with ammo > 0 (or unlimited)."""
    for w in u.weapons:
        if w.ammo == -1 or w.ammo > 0:
            return True
    return False


def _move_candidates(state: GameState, u: UnitInstance) -> List[Tuple[int, int]]:
    """Filter reachable() to hexes that matter tactically.

    Goal: keep at most ~6 destinations per unit, biased toward
    engagement/recon/retreat. The LLM picks; we don't decide.
    """
    reach = reachable(state, u)
    # Drop the unit's own hex (cost 0).
    candidates = [hx for hx, c in reach.items() if c > 0]
    if not candidates:
        return []

    visible_enemies = [
        e for e in state.units
        if e.side != u.side and _seen_by_side(state, u.side, e)
    ]
    enemy_pos = [(e.col, e.row) for e in visible_enemies]
    own_base = next(
        ((b.col, b.row) for b in (state.bases or []) if b.side == u.side),
        None,
    )

    scored: List[Tuple[float, Tuple[int, int]]] = []
    for hx in candidates:
        score = 0.0
        # +5 if adjacent to a visible enemy
        for ep in enemy_pos:
            d_e = distance(Hex(*hx), Hex(*ep))
            if d_e <= 1:
                score += 5
            elif d_e <= 2:
                score += 2
        # +closer-to-nearest-enemy reward (small)
        if enemy_pos:
            min_d = min(distance(Hex(*hx), Hex(*ep)) for ep in enemy_pos)
            score += max(0, 6 - min_d) * 0.3
        # +retreat-toward-base reward (small, only if HP is low)
        if own_base and u.hp <= u.max_hp / 2:
            score += max(0, 6 - distance(Hex(*hx), Hex(*own_base))) * 0.2
        # +exploration: hexes farther from own units (not just stacking)
        own_neighbours_dist = min(
            (distance(Hex(*hx), Hex(o.col, o.row))
             for o in state.units if o.side == u.side and o.id != u.id),
            default=99,
        )
        score += min(own_neighbours_dist, 4) * 0.05
        scored.append((score, hx))

    scored.sort(key=lambda p: -p[0])
    # Always include the highest-scored 4, plus a couple low-cost
    # "retreat / regroup" choices: cheapest 2 hexes by reach cost.
    top = [hx for _, hx in scored[:4]]
    cheap = sorted(candidates, key=lambda h: reach[h])[:2]
    seen = set()
    out: List[Tuple[int, int]] = []
    for hx in top + cheap:
        if hx not in seen:
            seen.add(hx)
            out.append(hx)
        if len(out) >= 6:
            break
    return out


def _strike_candidates(state: GameState, u: UnitInstance) -> List[Tuple[int, int]]:
    """Visible enemy hexes within weapon range + domain match."""
    if not _has_usable_weapon(u) or not u.weapons:
        return []
    w = u.weapons[0]
    if w.ammo == 0:
        return []

    out: List[Tuple[int, int]] = []
    seen = set()
    # Visible enemy units
    for e in state.units:
        if e.side == u.side:
            continue
        if not _seen_by_side(state, u.side, e):
            continue
        if w.target_domains and e.domain not in w.target_domains:
            continue
        d = distance(Hex(u.col, u.row), Hex(e.col, e.row))
        if d > w.range:
            continue
        hx = (e.col, e.row)
        if hx not in seen:
            seen.add(hx)
            out.append(hx)
    # Enemy bases (treat as land for domain filter)
    for b in (state.bases or []):
        if b.side == u.side:
            continue
        if w.target_domains and "land" not in w.target_domains:
            continue
        d = distance(Hex(u.col, u.row), Hex(b.col, b.row))
        if d > w.range:
            continue
        hx = (b.col, b.row)
        if hx not in seen:
            seen.add(hx)
            out.append(hx)
    return out


# ----------------------------------------------------------------------
# Labels for the LLM (terse, info-dense)

def _move_label(state: GameState, u: UnitInstance, hx: Tuple[int, int]) -> str:
    cell = next(
        (c for c in state.map.cells if c.col == hx[0] and c.row == hx[1]),
        None,
    )
    terrain = getattr(cell.terrain, "value", str(cell.terrain)) if cell else "?"
    d = distance(Hex(u.col, u.row), Hex(*hx))
    note = ""
    # Identify common tactical hooks for the model.
    visible_enemies = [
        e for e in state.units
        if e.side != u.side and _seen_by_side(state, u.side, e)
    ]
    nearest = min(
        (distance(Hex(*hx), Hex(e.col, e.row)) for e in visible_enemies),
        default=None,
    )
    if nearest is not None:
        if nearest == 0:
            note = " — onto enemy hex (kamikaze only)"
        elif nearest == 1:
            note = " — adjacent to enemy"
        elif nearest <= 2:
            note = f" — within {nearest} of nearest enemy"
    return f"MOVE H({hx[0]},{hx[1]}) — {terrain}, dist {d}{note}"


def _strike_label(state: GameState, u: UnitInstance, hx: Tuple[int, int]) -> str:
    on_hex = [
        e for e in state.units
        if e.side != u.side and (e.col, e.row) == hx
    ]
    on_hex_bases = [
        b for b in (state.bases or [])
        if b.side != u.side and (b.col, b.row) == hx
    ]
    parts = []
    for e in on_hex:
        parts.append(f"{e.id} ({e.type}, hp {e.hp}/{e.max_hp})")
    for b in on_hex_bases:
        parts.append(f"{b.id} (base, hp {b.hp}/{b.max_hp})")
    targets = ", ".join(parts) if parts else "empty"
    d = distance(Hex(u.col, u.row), Hex(*hx))
    w = u.weapons[0]
    return f"STRIKE H({hx[0]},{hx[1]}) — dist {d}, dmg {w.damage}; targets: {targets}"


# ----------------------------------------------------------------------
# Visibility helper (shared logic with engine._is_visible — kept simple)

def _seen_by_side(state: GameState, side: str, entity: UnitInstance) -> bool:
    """True iff `side` has any sensor that covers entity's hex."""
    for u in state.units:
        if u.side != side:
            continue
        if distance(Hex(u.col, u.row), Hex(entity.col, entity.row)) <= u.sensor:
            return True
    for b in (state.bases or []):
        if b.side != side:
            continue
        if distance(Hex(b.col, b.row), Hex(entity.col, entity.row)) <= b.sensor:
            return True
    return False
