"""Player orders submitted at the start of each turn.

Both sides queue orders simultaneously. The server consumes both queues
and runs `engine.resolve.resolve_turn(...)` which emits a deterministic
event log + mutates GameState in place.

Orders use a tagged union (pydantic discriminator) keyed by `kind`.
"""
from __future__ import annotations

from typing import Annotated, Literal, Tuple, Union

from pydantic import BaseModel, Field


HexCoord = Tuple[int, int]


class _BaseOrder(BaseModel):
    unit_id: str
    intent: str = ""        # free-text reasoning, surfaced in the demo UI


class MoveOrder(_BaseOrder):
    kind: Literal["MOVE"] = "MOVE"
    target_hex: HexCoord


class StrikeOrder(_BaseOrder):
    """Hex-targeted strike. Damage is applied to every enemy unit on the
    target hex after the MOVE phase. Friendlies in the hex are unaffected.
    """
    kind: Literal["STRIKE"] = "STRIKE"
    target_hex: HexCoord


class ScoutOrder(_BaseOrder):
    """Drone-only. Unit stays put; reveals every enemy in radius
    `scout_radius` around its own hex this turn."""
    kind: Literal["SCOUT"] = "SCOUT"


class OverwatchOrder(_BaseOrder):
    kind: Literal["OVERWATCH"] = "OVERWATCH"


class HoldOrder(_BaseOrder):
    kind: Literal["HOLD"] = "HOLD"


class ActivateOrder(_BaseOrder):
    """Toggle a unit's radar sensor ON. Free action — does not preclude movement.
    `sensor_key` empty = all togglable (radar) sensors on the unit."""
    kind: Literal["ACTIVATE"] = "ACTIVATE"
    sensor_key: str = ""


class DeactivateOrder(_BaseOrder):
    """Toggle a unit's radar sensor OFF. Free action."""
    kind: Literal["DEACTIVATE"] = "DEACTIVATE"
    sensor_key: str = ""


Order = Annotated[
    Union[
        MoveOrder, StrikeOrder, ScoutOrder,
        OverwatchOrder, HoldOrder,
        ActivateOrder, DeactivateOrder,
    ],
    Field(discriminator="kind"),
]


class OrderBatch(BaseModel):
    """All orders from one side for one turn."""
    side: Literal["blue", "red"]
    orders: list[Order]


# Convenience: empty default order is HOLD.
def hold_order(unit_id: str) -> HoldOrder:
    return HoldOrder(unit_id=unit_id)
