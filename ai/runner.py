"""Orchestration glue: pick a controller, ask it to decide, return orders.

The server hands a side + the active controller-kind here. We instantiate the
controller, run its decide() with a timeout, and bubble up orders + meta. On
exception or timeout, fall back to all-HOLD so the turn can resolve.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

from ai.controller import ControllerResult, get_controller
from engine.orders import HoldOrder, Order
from engine.state import GameState

# Importing controllers registers them. Add new controllers here as they land.
import ai.random_agent  # noqa: F401  (registers "random")


log = logging.getLogger(__name__)

# Per-turn ceiling. If a controller exceeds this, fall back to HOLD.
DECIDE_TIMEOUT_S = 25.0


async def play_side(
    side: str,
    controller_kind: str,
    state: GameState,
) -> Tuple[List[Order], Dict[str, Any]]:
    """Run the given controller for the given side. Returns (orders, meta).

    Falls back to all-HOLD if the controller errors or times out. Meta is
    always populated with a summary + per-unit decision log so the UI has
    something to render.
    """
    if controller_kind == "manual":
        raise ValueError("play_side called with manual controller — that's a UI bug")
    controller = get_controller(controller_kind)
    own_unit_ids = [u.id for u in state.units if u.side == side]

    try:
        result: ControllerResult = await asyncio.wait_for(
            controller.decide(state, side), timeout=DECIDE_TIMEOUT_S,
        )
        orders, meta = result
    except asyncio.TimeoutError:
        log.warning("controller %s timed out for side %s; falling back to HOLD",
                    controller_kind, side)
        orders = [HoldOrder(unit_id=uid) for uid in own_unit_ids]
        meta = {
            "summary": f"controller {controller_kind!r} timed out — all units HOLD",
            "decisions": [],
            "fallback": True,
        }
    except Exception as e:
        log.exception("controller %s crashed for side %s", controller_kind, side)
        orders = [HoldOrder(unit_id=uid) for uid in own_unit_ids]
        meta = {
            "summary": f"controller {controller_kind!r} errored: {e!r} — all units HOLD",
            "decisions": [],
            "fallback": True,
            "error": repr(e),
        }

    # Fill missing units with HOLD so every controllable unit has an order.
    have_order_for = {o.unit_id for o in orders}
    for uid in own_unit_ids:
        if uid not in have_order_for:
            orders.append(HoldOrder(unit_id=uid))

    return orders, meta
