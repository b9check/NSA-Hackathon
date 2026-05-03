"""Random controller — uniform random over a curated legal-action menu.

Used as the bootstrap baseline for the plumbing test, and as the sparring
partner for early LLM development. Picks one action per controllable unit:
HOLD, OVERWATCH, MOVE to a random reachable hex, or STRIKE a random visible
enemy hex (if any).
"""
from __future__ import annotations

import random
from typing import List

from ai.controller import Controller, ControllerResult, register_controller
from engine.movement import reachable
from engine.orders import (
    HoldOrder, MoveOrder, Order, OverwatchOrder, ScoutOrder, StrikeOrder,
)
from engine.state import GameState, UnitInstance


@register_controller("random")
class RandomController(Controller):
    """Uniform random over a small per-unit action menu. Deterministic given
    a seed; otherwise uses Python's default RNG."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    async def decide(self, state: GameState, side: str) -> ControllerResult:
        own_units = [u for u in state.units if u.side == side]
        orders: List[Order] = []
        per_unit_log: list[dict] = []

        for u in own_units:
            choices = self._build_choices(state, u)
            pick = self._rng.choice(choices)
            kind = pick["kind"]

            if kind == "HOLD":
                orders.append(HoldOrder(unit_id=u.id))
            elif kind == "OVERWATCH":
                orders.append(OverwatchOrder(unit_id=u.id))
            elif kind == "SCOUT":
                orders.append(ScoutOrder(unit_id=u.id))
            elif kind == "MOVE":
                orders.append(MoveOrder(
                    unit_id=u.id,
                    target_hex=tuple(pick["target_hex"]),  # type: ignore[arg-type]
                ))
            elif kind == "STRIKE":
                orders.append(StrikeOrder(
                    unit_id=u.id,
                    target_hex=tuple(pick["target_hex"]),  # type: ignore[arg-type]
                ))

            per_unit_log.append({
                "unit_id": u.id, "kind": kind,
                "target_hex": pick.get("target_hex"),
                "rationale": "random",
            })

        return orders, {
            "summary": f"random controller chose {len(orders)} actions",
            "decisions": per_unit_log,
        }

    # ------------------------------------------------------------------

    def _build_choices(self, state: GameState, u: UnitInstance) -> list[dict]:
        """Small menu of legal-ish actions. Mirrors what the LLM-side
        menu builder will do later, much simpler."""
        out: list[dict] = [{"kind": "HOLD"}]

        # OVERWATCH iff the unit has a weapon
        if u.weapon > 0:
            out.append({"kind": "OVERWATCH"})

        # SCOUT iff this is a drone with scout capability
        if u.type == "scout_drone":
            out.append({"kind": "SCOUT"})

        # MOVE — pick a few reachable hexes
        if u.speed > 0:
            reach = reachable(state, u)
            # Drop the unit's own hex (cost 0)
            destinations = [k for k, c in reach.items() if c > 0]
            # Sample up to 6 random destinations
            if destinations:
                sampled = self._rng.sample(
                    destinations, min(6, len(destinations)),
                )
                for d in sampled:
                    out.append({"kind": "MOVE", "target_hex": list(d)})

        # STRIKE — only target visible enemy hexes within weapon range
        if u.weapon > 0 and u.weapons:
            w = u.weapons[0]
            # Only consider enemies whose current hex is within range AND
            # whose domain matches what this weapon can hit.
            candidates: list[tuple[int, int]] = []
            for e in state.units:
                if e.side == u.side:
                    continue
                if w.target_domains and e.domain not in w.target_domains:
                    continue
                from engine.hex import Hex, distance
                d = distance(Hex(u.col, u.row), Hex(e.col, e.row))
                if d <= w.range:
                    candidates.append((e.col, e.row))
            for hx in candidates:
                out.append({"kind": "STRIKE", "target_hex": list(hx)})

        return out
