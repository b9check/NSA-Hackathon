"""Deterministic combat resolver.

Pipeline per turn:
    A. SCOUT — scout actions reveal contacts (event-only, no state change
       to units; visibility is recomputed lazily by callers).
    B. MOVE — every MOVE order applies its planned path; collisions are
       resolved by deterministic seed-tie-break (loser stops one hex back).
       OVERWATCH may interrupt a move (single shot per overwatcher per turn).
    C. STRIKE — every STRIKE order applies deterministic damage to every
       enemy unit on the target hex *after* moves resolve. A move-out
       dodges. The strike still consumes ammo and self-destruct attackers
       still die regardless of whiff.
    D. UPDATE — dead entities removed, win check, turn counter advances.

Determinism: blake2b((game_seed, turn, attacker_id, idx)) feeds
collision tie-breaks. Combat damage itself is deterministic (no rolls).
"""
from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Optional, Tuple

from engine.events import (
    DestroyedEvent,
    Event,
    MoveEvent,
    OverwatchFireEvent,
    ScoutRevealEvent,
    StrikeEvent,
    TurnEndEvent,
)
from engine.hex import Hex, hex_range, in_bounds, distance
from engine.movement import path_to
from engine.orders import (
    HoldOrder,
    MoveOrder,
    Order,
    OverwatchOrder,
    ScoutOrder,
    StrikeOrder,
)
from engine.state import BaseInstance, GameState, UnitInstance


def _seed64(game_seed: int, turn: int, atk: str, idx: int) -> int:
    h = hashlib.blake2b(
        f"{game_seed}|{turn}|{atk}|{idx}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return struct.unpack("<Q", h)[0]


def _roll(game_seed: int, turn: int, atk: str, idx: int) -> float:
    s = _seed64(game_seed, turn, atk, idx)
    return (s >> 11) / 2 ** 53


# ---- Helpers ----------------------------------------------------------

def _entity_at(state: GameState, hex_: Tuple[int, int]):
    for u in state.units:
        if (u.col, u.row) == hex_:
            return u
    for b in state.bases:
        if (b.col, b.row) == hex_:
            return b
    return None


def _enemies_at(state: GameState, side: str, hex_: Tuple[int, int]):
    """Every enemy unit/base on the given hex."""
    out = []
    for u in state.units:
        if u.side != side and (u.col, u.row) == hex_:
            out.append(u)
    for b in state.bases:
        if b.side != side and (b.col, b.row) == hex_:
            out.append(b)
    return out


def _is_visible(side: str, entity, state: GameState) -> bool:
    """Crude visibility: any friendly unit/base sensor covers the entity hex.
    Stealth halves effective sensor range against the target."""
    is_stealth = bool(getattr(entity, "stealth", False))
    sources = []
    for u in state.units:
        if u.side == side:
            sources.append((u.col, u.row, u.sensor))
    for b in state.bases:
        if b.side == side:
            sources.append((b.col, b.row, b.sensor))
    for c, r, sn in sources:
        eff = sn // 2 if is_stealth else sn
        if distance(Hex(c, r), Hex(entity.col, entity.row)) <= eff:
            return True
    return False


def _primary_weapon(attacker: UnitInstance):
    """Return (weapon_ref, idx) for the first non-empty-ammo weapon, or None.

    The 9-type catalog gives every striker exactly one weapon, so this
    reduces to 'pick the only weapon if it has ammo'.
    """
    for i, w in enumerate(attacker.weapons):
        if w.ammo == 0:
            continue
        return w, i
    return None


def _validate_hex(state: GameState, hex_: Tuple[int, int]) -> bool:
    return in_bounds(Hex(*hex_), state.map.cols, state.map.rows)


# ---- Phase A: Scout --------------------------------------------------

def _phase_scout(
    state: GameState, orders: list, game_seed: int, turn: int
) -> list[Event]:
    """Drone-only SCOUT action: unit stays put, reveals every enemy in
    radius `scout_radius` around its OWN hex this turn.
    """
    from engine.catalog import PLATFORMS
    events: list[Event] = []
    units_by_id = {u.id: u for u in state.units}
    for o in orders:
        if not isinstance(o, ScoutOrder):
            continue
        u = units_by_id.get(o.unit_id)
        if u is None:
            continue
        plat = PLATFORMS.get(u.type)
        radius = getattr(plat, "scout_radius", 0) if plat else 0
        if radius <= 0:
            continue
        revealed_hexes: list[Tuple[int, int]] = []
        revealed_units: list[str] = []
        for h in hex_range(Hex(u.col, u.row), radius,
                           state.map.cols, state.map.rows):
            revealed_hexes.append((h.col, h.row))
            ent = _entity_at(state, (h.col, h.row))
            if ent is not None and getattr(ent, "side", None) != u.side:
                revealed_units.append(ent.id)
        events.append(ScoutRevealEvent(
            side=u.side, observer=u.id,
            revealed_units=revealed_units,
            revealed_hexes=revealed_hexes,
        ))
    return events


# ---- Phase B: Move + Overwatch interleave ---------------------------

@dataclass
class _PlannedMove:
    unit: UnitInstance
    path: list
    final: Tuple[int, int]
    stopped_short: bool = False


def _plan_moves(state: GameState, orders: list) -> list:
    out: list[_PlannedMove] = []
    units_by_id = {u.id: u for u in state.units}
    for o in orders:
        if not isinstance(o, MoveOrder):
            continue
        u = units_by_id.get(o.unit_id)
        if u is None or not _validate_hex(state, o.target_hex):
            continue
        path = path_to(state, u, o.target_hex[0], o.target_hex[1])
        if path is None or len(path) < 2:
            continue
        out.append(_PlannedMove(unit=u, path=path, final=path[-1]))
    return out


def _resolve_collisions(planned: list, game_seed: int, turn: int) -> None:
    """Same-final-hex collisions: deterministic tie-break, losers back up
    one hex. Repeats until stable."""
    while True:
        by_hex: dict[Tuple[int, int], list] = {}
        for p in planned:
            by_hex.setdefault(p.final, []).append(p)
        any_changed = False
        for hex_, contenders in by_hex.items():
            if len(contenders) <= 1:
                continue
            contenders.sort(
                key=lambda p: _roll(game_seed, turn, p.unit.id, 0)
            )
            for loser in contenders[1:]:
                if len(loser.path) >= 2:
                    loser.path = loser.path[:-1]
                    loser.final = loser.path[-1]
                    loser.stopped_short = True
                else:
                    loser.final = (loser.unit.col, loser.unit.row)
                    loser.stopped_short = True
                any_changed = True
        if not any_changed:
            break


def _phase_move(
    state: GameState, orders: list, game_seed: int, turn: int,
) -> Tuple[list[Event], list]:
    planned = _plan_moves(state, orders)
    _resolve_collisions(planned, game_seed, turn)
    events: list[Event] = []
    for p in planned:
        p.unit.col, p.unit.row = p.final
        events.append(MoveEvent(
            unit=p.unit.id, path=p.path, stopped_short=p.stopped_short,
        ))
    # OVERWATCH triggers — simple post-move scan: any OVERWATCH unit on
    # the OPPOSITE side fires once at any mover that ends within weapon
    # range and is visible.
    overwatchers = [
        u for u in state.units
        if any(isinstance(o, OverwatchOrder) and o.unit_id == u.id
               for o in orders)
    ]
    fired: set[str] = set()
    for ow in overwatchers:
        for p in planned:
            mover = p.unit
            if mover.side == ow.side or ow.id in fired:
                continue
            if ow.weapon == 0:
                continue
            if distance(Hex(ow.col, ow.row), Hex(mover.col, mover.row)) > ow.weapon:
                continue
            if not _is_visible(ow.side, mover, state):
                continue
            picked = _primary_weapon(ow)
            if picked is None:
                continue
            w, _idx = picked
            if w.ammo > 0:
                w.ammo -= 1
            dmg = w.damage
            mover.hp = max(0, mover.hp - dmg)
            events.append(OverwatchFireEvent(
                attacker=ow.id, target=mover.id, weapon=w.key,
                damage=dmg, trigger_hex=(mover.col, mover.row),
            ))
            fired.add(ow.id)
    return events, planned


# ---- Phase C: Strikes (deterministic, hex-targeted) ------------------

def _phase_strike(
    state: GameState, orders: list, game_seed: int, turn: int,
) -> list[Event]:
    """Hex-targeted, deterministic.

    For each STRIKE order:
      - Validate range (attacker -> target_hex).
      - Decrement ammo on attempt (whiff still costs ammo).
      - Apply `weapon.damage` to every enemy unit/base on target_hex.
      - Same-hex / range-0 melee: attacker takes ⌈dmg/2⌉ counter-damage.
      - self_destruct=True: attacker dies after firing (whiff or not).
    """
    units_by_id = {u.id: u for u in state.units}
    pending_attacker_damage: dict[str, int] = {}
    self_destruct_ids: set[str] = set()
    events: list[Event] = []
    for o in orders:
        if not isinstance(o, StrikeOrder):
            continue
        atk = units_by_id.get(o.unit_id)
        if atk is None or atk.hp <= 0:
            continue
        if not _validate_hex(state, o.target_hex):
            continue
        picked = _primary_weapon(atk)
        if picked is None:
            continue
        w, _idx = picked
        d = distance(Hex(atk.col, atk.row), Hex(*o.target_hex))
        if d > w.range:
            continue
        # Burn the ammo on attempt.
        if w.ammo > 0:
            w.ammo -= 1
        targets = _enemies_at(state, atk.side, tuple(o.target_hex))
        whiffed = len(targets) == 0
        targets_hit = [t.id for t in targets]
        damage_applied = 0 if whiffed else w.damage
        for t in targets:
            t.hp = max(0, t.hp - w.damage)
        # Counter-damage: same-hex melee
        counter = 0
        is_melee = (d == 0)
        if is_melee and not whiffed:
            counter = math.ceil(w.damage / 2)
            pending_attacker_damage[atk.id] = (
                pending_attacker_damage.get(atk.id, 0) + counter
            )
        # Self-destruct: attacker dies whether or not whiff.
        if w.self_destruct:
            self_destruct_ids.add(atk.id)
        events.append(StrikeEvent(
            attacker=atk.id,
            weapon=w.key, weapon_kind=w.kind,
            target_hex=tuple(o.target_hex),
            damage=damage_applied,
            targets_hit=targets_hit,
            whiffed=whiffed,
            self_destruct=w.self_destruct,
            counter_damage=counter,
        ))
    # Apply queued counter-damage and self-destructs after resolution.
    for atk_id, cdmg in pending_attacker_damage.items():
        atk = units_by_id.get(atk_id)
        if atk is None:
            continue
        atk.hp = max(0, atk.hp - cdmg)
    for atk_id in self_destruct_ids:
        atk = units_by_id.get(atk_id)
        if atk is None:
            continue
        atk.hp = 0
    return events


# ---- Phase D: cleanup + score + win check ----------------------------

def _phase_update(
    state: GameState, orders: list, game_seed: int, turn: int,
) -> list[Event]:
    events: list[Event] = []
    survivors: list[UnitInstance] = []
    for u in state.units:
        if u.hp <= 0:
            events.append(DestroyedEvent(entity_id=u.id, side=u.side, is_base=False))
        else:
            survivors.append(u)
    state.units = survivors
    surviving_bases: list[BaseInstance] = []
    for b in state.bases:
        if b.hp <= 0:
            events.append(DestroyedEvent(entity_id=b.id, side=b.side, is_base=True))
        else:
            surviving_bases.append(b)
    state.bases = surviving_bases

    blue_s, red_s = compute_scores(state)
    events.append(TurnEndEvent(turn=turn, blue_score=blue_s, red_score=red_s))
    state.turn = turn + 1

    winner, reason = compute_winner(state)
    if winner is not None:
        state.winner = winner
        state.win_reason = reason
    return events


def compute_scores(state: GameState) -> Tuple[float, float]:
    """Cost-weighted health, normalized to 100 at full strength.
    Display-only — end-game decision uses HP % via compute_winner."""
    def score(side: str) -> float:
        cur = 0.0
        for u in state.units:
            if u.side != side:
                continue
            cur += u.cost * (u.hp / max(u.max_hp, 1))
        for b in state.bases:
            if b.side != side:
                continue
            cur += 50.0 * (b.hp / max(b.max_hp, 1))
        total = state.starting_total.get(side, 0.0)
        if total == 0:
            return 0.0
        return round(100.0 * cur / total, 1)
    return score("blue"), score("red")


def _hp_total(state: GameState, side: str) -> int:
    return (
        sum(u.hp for u in state.units if u.side == side)
        + sum(b.hp for b in state.bases if b.side == side)
    )


def _hp_pct(state: GameState, side: str) -> float:
    cur = _hp_total(state, side)
    start = state.starting_hp.get(side, 0)
    return cur / start if start > 0 else 0.0


def compute_winner(state: GameState) -> Tuple[Optional[str], Optional[str]]:
    """End-of-game decision. Returns (winner, reason) or (None, None) if
    the match continues. Priority order:
      1. annihilation — opposing side has 0 units AND 0 bases
      2. hp_collapse  — total HP <= threshold% of starting HP
      3. turn_cap     — turn >= victory.turn_cap; higher HP% wins
    """
    v = state.victory
    blue_hp = _hp_total(state, "blue")
    red_hp = _hp_total(state, "red")
    blue_alive = any(u.side == "blue" for u in state.units) or any(
        b.side == "blue" for b in state.bases
    )
    red_alive = any(u.side == "red" for u in state.units) or any(
        b.side == "red" for b in state.bases
    )

    # 1) Annihilation
    if not blue_alive and not red_alive:
        return "draw", "annihilation"
    if not blue_alive:
        return "red", "annihilation"
    if not red_alive:
        return "blue", "annihilation"

    # 2) HP collapse
    blue_pct = _hp_pct(state, "blue")
    red_pct = _hp_pct(state, "red")
    blue_collapsed = blue_pct <= v.hp_loss_threshold
    red_collapsed = red_pct <= v.hp_loss_threshold
    if blue_collapsed and red_collapsed:
        if blue_pct > red_pct:
            return "blue", "hp_collapse"
        if red_pct > blue_pct:
            return "red", "hp_collapse"
        return "draw", "hp_collapse"
    if blue_collapsed:
        return "red", "hp_collapse"
    if red_collapsed:
        return "blue", "hp_collapse"

    # 3) Turn cap
    if state.turn >= v.turn_cap:
        if blue_pct > red_pct:
            return "blue", "turn_cap"
        if red_pct > blue_pct:
            return "red", "turn_cap"
        return "draw", "turn_cap"

    return None, None


# ---- Driver ----------------------------------------------------------

def resolve_turn(
    state: GameState,
    blue_orders: list,
    red_orders: list,
) -> list[Event]:
    """Apply both sides' orders; mutate `state` in place; return event log."""
    turn = state.turn
    seed = state.seed
    all_orders = list(blue_orders) + list(red_orders)
    events: list[Event] = []
    events += _phase_scout(state, all_orders, seed, turn)
    move_events, _planned = _phase_move(state, all_orders, seed, turn)
    events += move_events
    events += _phase_strike(state, all_orders, seed, turn)
    events += _phase_update(state, all_orders, seed, turn)
    return events
