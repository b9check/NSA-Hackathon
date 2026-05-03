"""Deterministic combat resolver.

Pipeline per turn:
    A. SCOUT — scout actions reveal contacts (event-only, no state change
       to units; visibility is recomputed lazily by callers).
    B. MOVE — every MOVE order applies its planned path; collisions are
       resolved by deterministic seed-tie-break (loser stops one hex back).
       OVERWATCH may interrupt a move (single shot per overwatcher per turn).
    C. STRIKE — every STRIKE order rolls a Pkill against the *pre-strike*
       HP of its target. Damages are applied after all rolls so two
       fighters dueling can both die in the same turn.
    D. UPDATE — capture counters tick, dead entities removed, victory
       check, turn counter advances.

Determinism: blake2b((game_seed, turn, attacker_id, target_id, idx)) feeds
a numpy PCG64. Same inputs => identical events => replay-safe.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from engine.catalog import PLATFORMS, WEAPONS
from engine.events import (
    CaptureEvent,
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
    CaptureOrder,
    HoldOrder,
    MoveOrder,
    Order,
    OverwatchOrder,
    ScoutOrder,
    StrikeOrder,
)
from engine.state import BaseInstance, GameState, UnitInstance


# ----- objective bonus -----
# A side that holds an objective hex (any of its land/amphib units sitting
# on it, no enemy on the same hex) earns this many score points per turn.
# Hard cap so a turtle strategy can't run away with the score.
OBJECTIVE_BONUS_PER_TURN = 5.0
OBJECTIVE_BONUS_CAP = 30.0


# ----- damage table (per-weapon-kind hp damage on a successful hit) -----
DAMAGE_BY_KIND = {
    "aam": 2,
    "asm_air": 3,
    "asm_ship": 3,
    "sam": 2,
    "gun_naval": 1,
    "gun_armor": 1,
    "manpads": 1,
    "loitering": 3,
}


def _seed64(game_seed: int, turn: int, atk: str, tgt: str, idx: int) -> int:
    h = hashlib.blake2b(
        f"{game_seed}|{turn}|{atk}|{tgt}|{idx}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return struct.unpack("<Q", h)[0]


def _roll(game_seed: int, turn: int, atk: str, tgt: str, idx: int) -> float:
    s = _seed64(game_seed, turn, atk, tgt, idx)
    # convert 64-bit unsigned to [0, 1) with full precision
    return (s >> 11) / 2 ** 53


# ---- Helpers ----------------------------------------------------------

def _entity_at(state: GameState, hex_: tuple[int, int]):
    for u in state.units:
        if (u.col, u.row) == hex_:
            return u
    for b in state.bases:
        if (b.col, b.row) == hex_:
            return b
    return None


def _all_targets(state: GameState):
    """Both units and bases, indexed by id, treated as damageable entities."""
    by_id: dict[str, UnitInstance | BaseInstance] = {}
    for u in state.units:
        by_id[u.id] = u
    for b in state.bases:
        by_id[b.id] = b
    return by_id


def _is_visible(side: str, entity, state: GameState) -> bool:
    """Crude visibility: any friendly unit/base sensor covers the entity hex.

    Mirrors web/src/components/MapStage.tsx::computeVisibleHexes. Stealth
    halves effective sensor range against the target.
    """
    is_stealth = bool(getattr(entity, "stealth", False))
    sources: list[tuple[int, int, int]] = []  # (col, row, sensor_range)
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


def _best_weapon(attacker: UnitInstance, target_domain: str):
    """Pick the attached weapon with the highest pkill vs target.domain
    that still has ammo."""
    best = None
    best_pk = -1.0
    for w in attacker.weapons:
        if w.ammo == 0:
            continue
        pk = w.pkill.get(target_domain, 0.0)
        if pk > best_pk:
            best = w
            best_pk = pk
    return best


def _validate_hex(state: GameState, hex_: tuple[int, int]) -> bool:
    return in_bounds(Hex(*hex_), state.map.cols, state.map.rows)


# ---- Phase A: Scout --------------------------------------------------

def _phase_scout(
    state: GameState, orders: list[Order], game_seed: int, turn: int
) -> list[Event]:
    events: list[Event] = []
    units_by_id = {u.id: u for u in state.units}
    for o in orders:
        if not isinstance(o, ScoutOrder):
            continue
        u = units_by_id.get(o.unit_id)
        if u is None or not _validate_hex(state, o.target_hex):
            continue
        # Scouting boosts effective sensor +50% (rounded up) for one turn.
        eff = max(1, int(round(u.sensor * 1.5)) if u.sensor > 0 else 1)
        revealed_hexes: list[tuple[int, int]] = []
        revealed_units: list[str] = []
        for h in hex_range(Hex(*o.target_hex), eff,
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
    path: list[tuple[int, int]]
    final: tuple[int, int]
    stopped_short: bool = False


def _plan_moves(state: GameState, orders: list[Order]) -> list[_PlannedMove]:
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


def _resolve_collisions(planned: list[_PlannedMove],
                        game_seed: int, turn: int) -> None:
    """If two units want the same hex, deterministic tie-break decides
    who gets it; the loser stops one hex back along its path."""
    while True:
        # group by final hex
        by_hex: dict[tuple[int, int], list[_PlannedMove]] = {}
        for p in planned:
            by_hex.setdefault(p.final, []).append(p)
        any_changed = False
        for hex_, contenders in by_hex.items():
            if len(contenders) <= 1:
                continue
            # rank by deterministic roll
            contenders.sort(
                key=lambda p: _roll(game_seed, turn, p.unit.id, "move", 0)
            )
            winner = contenders[0]
            for loser in contenders[1:]:
                # back the loser up one hex along their path; if path
                # already only had one step, they stay put.
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
    state: GameState, orders: list[Order], game_seed: int, turn: int,
) -> tuple[list[Event], list[_PlannedMove]]:
    planned = _plan_moves(state, orders)
    _resolve_collisions(planned, game_seed, turn)
    events: list[Event] = []
    # commit positions
    for p in planned:
        p.unit.col, p.unit.row = p.final
        events.append(MoveEvent(
            unit=p.unit.id, path=p.path, stopped_short=p.stopped_short,
        ))
    # OVERWATCH triggers — for v1, a simple post-move scan: any OVERWATCH
    # unit on the OPPOSITE side fires once at any mover that ends within
    # weapon range and is visible.
    overwatchers = [
        u for u in state.units
        if any(isinstance(o, OverwatchOrder) and o.unit_id == u.id
               for o in orders)
    ]
    fired: set[str] = set()
    for ow in overwatchers:
        for p in planned:
            mover = p.unit
            if mover.side == ow.side:
                continue
            if ow.weapon == 0 or ow.id in fired:
                continue
            if distance(Hex(ow.col, ow.row), Hex(mover.col, mover.row)) > ow.weapon:
                continue
            if not _is_visible(ow.side, mover, state):
                continue
            w = _best_weapon(ow, mover.domain)
            if w is None:
                continue
            pk = w.pkill.get(mover.domain, 0.0) * 0.7
            if mover.stealth:
                pk *= 0.85
            r = _roll(game_seed, turn, ow.id, mover.id, 0)
            hit = r < pk
            if w.ammo > 0:
                w.ammo -= 1
            dmg = DAMAGE_BY_KIND.get(w.kind, 1) if hit else 0
            if hit:
                mover.hp = max(0, mover.hp - dmg)
            events.append(OverwatchFireEvent(
                attacker=ow.id, target=mover.id, weapon=w.key,
                pkill=round(pk, 3), roll=round(r, 3), hit=hit,
                damage=dmg, trigger_hex=(mover.col, mover.row),
            ))
            fired.add(ow.id)
    return events, planned


# ---- Phase C: Strikes ------------------------------------------------

def _phase_strike(
    state: GameState, orders: list[Order], game_seed: int, turn: int,
) -> list[Event]:
    targets = _all_targets(state)
    units_by_id = {u.id: u for u in state.units}
    events: list[StrikeEvent] = []
    pending_damage: dict[str, int] = {}
    idx = 0
    for o in orders:
        if not isinstance(o, StrikeOrder):
            continue
        atk = units_by_id.get(o.unit_id)
        tgt = targets.get(o.target_id)
        if atk is None or tgt is None:
            continue
        if atk.weapon == 0:
            continue
        if distance(Hex(atk.col, atk.row), Hex(tgt.col, tgt.row)) > atk.weapon:
            continue
        if not _is_visible(atk.side, tgt, state):
            continue
        w = _best_weapon(atk, tgt.domain)
        if w is None:
            continue
        pk = w.pkill.get(tgt.domain, 0.0)
        if getattr(tgt, "stealth", False):
            pk *= 0.85
        r = _roll(game_seed, turn, atk.id, tgt.id, idx)
        idx += 1
        hit = r < pk
        if w.ammo > 0:
            w.ammo -= 1
        dmg = DAMAGE_BY_KIND.get(w.kind, 1) if hit else 0
        events.append(StrikeEvent(
            attacker=atk.id, target=tgt.id, weapon=w.key,
            weapon_kind=w.kind, pkill=round(pk, 3), roll=round(r, 3),
            hit=hit, damage=dmg, remaining_hp=tgt.hp,  # pre-strike
        ))
        if hit:
            pending_damage[tgt.id] = pending_damage.get(tgt.id, 0) + dmg
    # Apply damage *after* every roll so simultaneous duels work correctly.
    for ev in events:
        if not ev.hit:
            continue
        tgt = targets.get(ev.target)
        if tgt is None:
            continue
        tgt.hp = max(0, tgt.hp - ev.damage)
        ev.remaining_hp = tgt.hp
    return events  # type: ignore[return-value]


# ---- Phase D: capture + cleanup + score ------------------------------

def _phase_update(
    state: GameState, orders: list[Order], game_seed: int, turn: int,
) -> list[Event]:
    events: list[Event] = []
    # ---- Objective bonus: +OBJECTIVE_BONUS_PER_TURN per held objective,
    #       capped per side at OBJECTIVE_BONUS_CAP. "Held" = the side has
    #       at least one ground unit on the hex AND the enemy doesn't.
    for o in state.map.objective_hexes:
        h = (o.col, o.row)
        sides = {u.side for u in state.units
                 if (u.col, u.row) == h
                 and u.domain in ("land", "amphib")}
        if len(sides) == 1:
            side = next(iter(sides))
            cur = state.objective_points.get(side, 0.0)
            after = min(OBJECTIVE_BONUS_CAP, cur + OBJECTIVE_BONUS_PER_TURN)
            if after > cur:
                state.objective_points[side] = after
                events.append(CaptureEvent(
                    hex=h, side=side,
                    counter=int(after),
                    threshold=int(OBJECTIVE_BONUS_CAP),
                    controller=side,
                ))

    # Remove dead entities + emit destroyed events.
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

    # Turn end + score
    blue_s, red_s = compute_scores(state)
    events.append(TurnEndEvent(turn=turn, blue_score=blue_s, red_score=red_s))
    state.turn = turn + 1
    return events


def compute_scores(state: GameState) -> tuple[float, float]:
    """Score = cost-weighted health (100 = full strength) + objective bonus.

    Combat-power denominator is the side's STARTING total so attrition
    visibly shaves points (a side annihilated reads ~0 + objective_bonus).
    Objective bonus caps at OBJECTIVE_BONUS_CAP so a turtle strategy can't
    run away with the score.
    """
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
        base = 100.0 * cur / total if total else 0.0
        bonus = min(
            OBJECTIVE_BONUS_CAP,
            state.objective_points.get(side, 0.0),
        )
        return round(base + bonus, 1)
    return score("blue"), score("red")


# ---- Driver ----------------------------------------------------------

def resolve_turn(
    state: GameState,
    blue_orders: list[Order],
    red_orders: list[Order],
) -> list[Event]:
    """Apply both sides' orders; mutate `state` in place; return event log."""
    turn = state.turn
    seed = state.seed
    all_orders: list[Order] = list(blue_orders) + list(red_orders)
    events: list[Event] = []
    events += _phase_scout(state, all_orders, seed, turn)
    move_events, _planned = _phase_move(state, all_orders, seed, turn)
    events += move_events
    events += _phase_strike(state, all_orders, seed, turn)
    events += _phase_update(state, all_orders, seed, turn)
    return events
