"""Validate the LLM's plan and translate action_ids back to Orders.

The LLM responds with a `LLMTurnPlan` JSON: {summary, decisions[]}. Each
decision names an action_id from that unit's menu. We:
  1. Look up the menu item by id.
  2. Translate to a concrete Order (HoldOrder, MoveOrder, etc).
  3. On unknown id or invalid CUSTOM coords: snap-to-legal or fall back to HOLD.
  4. Fire any radar toggles via the existing sensor-toggle path.
  5. Ensure every controllable unit has exactly one Order (fill missing with HOLD).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ai.menu import MenuItem, UnitMenu
from engine.hex import Hex, distance
from engine.movement import reachable
from engine.orders import (
    HoldOrder, MoveOrder, Order, OverwatchOrder, ScoutOrder, StrikeOrder,
)
from engine.state import GameState, UnitInstance


log = logging.getLogger(__name__)


def validate_plan(
    plan: Dict[str, Any],
    menus: Dict[str, UnitMenu],
    state: GameState,
    side: str,
) -> Tuple[List[Order], List[Tuple[str, str, Optional[Tuple[int, int]]]], List[str]]:
    """Map a parsed LLM `LLMTurnPlan` to engine Orders.

    Returns:
      orders: list[Order] — one per controllable unit (HOLD for any drops).
      sensor_toggles: list[(unit_id, sensor_key, active)]
        — radar flips to apply via the existing /api/sensor/toggle path.
      drops: list[str] — human-readable drop reasons for the reasoning panel.
    """
    own_units = {u.id: u for u in state.units if u.side == side}
    decisions = plan.get("decisions") or []

    orders: List[Order] = []
    sensor_toggles: List[Tuple[str, str, Optional[Tuple[int, int]]]] = []
    drops: List[str] = []
    seen_uids: set[str] = set()

    for d in decisions:
        uid = d.get("unit_id")
        action_id = d.get("action_id")
        intent = (d.get("intent") or "")[:120]
        if not isinstance(uid, str) or uid not in own_units:
            drops.append(f"unknown unit_id {uid!r}")
            continue
        if uid in seen_uids:
            drops.append(f"duplicate decision for {uid}")
            continue
        seen_uids.add(uid)

        unit = own_units[uid]
        menu = menus.get(uid)
        if menu is None:
            drops.append(f"no menu for {uid}")
            orders.append(HoldOrder(unit_id=uid, intent=intent))
            continue

        item = menu.find(int(action_id) if action_id is not None else -1)
        if item is None:
            drops.append(
                f"{uid}: action_id {action_id!r} not in menu; defaulting to HOLD",
            )
            orders.append(HoldOrder(unit_id=uid, intent=intent))
            continue

        order, toggle = _item_to_order(item, unit, state, intent, d)
        if order is not None:
            orders.append(order)
        if toggle is not None:
            sensor_toggles.append(toggle)

    # Fill missing controllable units with HOLD.
    for uid in own_units:
        if uid not in seen_uids:
            drops.append(f"{uid}: missing from plan; defaulted to HOLD")
            orders.append(HoldOrder(unit_id=uid))

    return orders, sensor_toggles, drops


def _item_to_order(
    item: MenuItem,
    unit: UnitInstance,
    state: GameState,
    intent: str,
    decision: Dict[str, Any],
) -> Tuple[Optional[Order], Optional[Tuple[str, str, Optional[Tuple[int, int]]]]]:
    """Convert one MenuItem into (Order | None, sensor_toggle | None)."""
    kind = item.kind

    if kind == "HOLD":
        return HoldOrder(unit_id=unit.id, intent=intent), None
    if kind == "OVERWATCH":
        return OverwatchOrder(unit_id=unit.id, intent=intent), None
    if kind == "SCOUT":
        return ScoutOrder(unit_id=unit.id, intent=intent), None
    if kind == "MOVE":
        if item.target_hex is None:
            return HoldOrder(unit_id=unit.id, intent=intent), None
        return MoveOrder(
            unit_id=unit.id,
            target_hex=tuple(item.target_hex),  # type: ignore[arg-type]
            intent=intent,
        ), None
    if kind == "STRIKE":
        if item.target_hex is None:
            return HoldOrder(unit_id=unit.id, intent=intent), None
        return StrikeOrder(
            unit_id=unit.id,
            target_hex=tuple(item.target_hex),  # type: ignore[arg-type]
            intent=intent,
        ), None
    if kind == "ACTIVATE_RADAR":
        if item.sensor_key:
            return None, (unit.id, item.sensor_key, True)  # type: ignore[return-value]
        return HoldOrder(unit_id=unit.id, intent=intent), None
    if kind == "DEACTIVATE_RADAR":
        if item.sensor_key:
            return None, (unit.id, item.sensor_key, False)  # type: ignore[return-value]
        return HoldOrder(unit_id=unit.id, intent=intent), None
    if kind == "CUSTOM":
        return _repair_custom(unit, state, intent, decision), None

    log.warning("unknown menu kind %r for %s; defaulting to HOLD", kind, unit.id)
    return HoldOrder(unit_id=unit.id, intent=intent), None


def _repair_custom(
    unit: UnitInstance,
    state: GameState,
    intent: str,
    decision: Dict[str, Any],
) -> Order:
    """LLM picked CUSTOM (action_id=99). Read free-form coords and snap.

    Strategy:
      - If decision has `kind` and target_hex looking like a strike (within
        weapon range and an enemy hex), STRIKE.
      - Else treat as MOVE: snap to nearest reachable hex along the
        straight-line direction.
      - On total failure: HOLD.
    """
    target = decision.get("target_hex")
    if not (isinstance(target, list) and len(target) == 2):
        return HoldOrder(unit_id=unit.id, intent=intent)
    tx, ty = int(target[0]), int(target[1])
    custom_kind = (decision.get("kind") or "").upper()

    # If LLM said STRIKE, try as strike.
    if custom_kind == "STRIKE" and unit.weapons:
        w = unit.weapons[0]
        d = distance(Hex(unit.col, unit.row), Hex(tx, ty))
        if d <= w.range:
            return StrikeOrder(
                unit_id=unit.id, target_hex=(tx, ty), intent=intent,
            )
        # Out of range: fall through to MOVE.

    # Treat as MOVE: snap to the legal hex closest to the LLM's target.
    if unit.speed <= 0:
        return HoldOrder(unit_id=unit.id, intent=intent)
    reach = reachable(state, unit)
    if not reach:
        return HoldOrder(unit_id=unit.id, intent=intent)
    best = min(reach.keys(), key=lambda h: distance(Hex(*h), Hex(tx, ty)))
    return MoveOrder(unit_id=unit.id, target_hex=best, intent=intent)
