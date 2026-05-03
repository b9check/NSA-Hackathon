"""Cheap scripted opponent for EngineEnv.

Per living unit on `side`:
  - find nearest visible enemy (using engine._is_visible semantics + hex distance)
  - if a visible enemy is within weapon range AND unit has a weapon: STRIKE
  - elif a visible enemy exists: MOVE one neighbor hex toward it
  - else: HOLD
"""
from __future__ import annotations

from engine.hex import Hex, distance, in_bounds, neighbors
from engine.orders import HoldOrder, MoveOrder, Order, StrikeOrder
from engine.resolve import _is_visible
from engine.state import GameState
from engine.terrain import can_traverse


def _terrain_at(state: GameState, col: int, row: int):
    for c in state.map.cells:
        if c.col == col and c.row == row:
            return c.terrain
    return None


def choose_orders(state: GameState, side: str = "red") -> list[Order]:
    cols, rows = state.map.cols, state.map.rows
    enemies = [u for u in state.units if u.side != side and u.hp > 0]
    orders: list[Order] = []
    for u in state.units:
        if u.side != side or u.hp <= 0:
            continue
        # nearest visible enemy
        visible = [
            e for e in enemies
            if _is_visible(side, e, state)
        ]
        if not visible:
            orders.append(HoldOrder(unit_id=u.id))
            continue
        target = min(
            visible,
            key=lambda e: distance(Hex(u.col, u.row), Hex(e.col, e.row)),
        )
        d = distance(Hex(u.col, u.row), Hex(target.col, target.row))
        # STRIKE if in range and we have a weapon
        if u.weapon > 0 and d <= u.weapon:
            orders.append(StrikeOrder(
                unit_id=u.id, target_hex=(target.col, target.row),
            ))
            continue
        # MOVE one hex toward target
        best_nb = None
        best_d = d
        for nb in neighbors(Hex(u.col, u.row)):
            if not in_bounds(nb, cols, rows):
                continue
            terr = _terrain_at(state, nb.col, nb.row)
            if terr is None or not can_traverse(u.domain, terr):
                continue
            nd = distance(nb, Hex(target.col, target.row))
            if nd < best_d:
                best_d = nd
                best_nb = nb
        if best_nb is not None:
            orders.append(MoveOrder(unit_id=u.id, target_hex=(best_nb.col, best_nb.row)))
        else:
            orders.append(HoldOrder(unit_id=u.id))
    return orders
