"""Player orders submitted at the start of each turn.

Both sides queue orders simultaneously. The server consumes both queues
and runs `engine.resolve.resolve_turn(...)` which emits a deterministic
event log + mutates GameState in place.

Orders use a tagged union (pydantic discriminator) keyed by `kind`.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


HexCoord = tuple[int, int]


class _BaseOrder(BaseModel):
    unit_id: str
    intent: str = ""        # free-text reasoning, surfaced in the demo UI


class MoveOrder(_BaseOrder):
    kind: Literal["MOVE"] = "MOVE"
    target_hex: HexCoord


class StrikeOrder(_BaseOrder):
    kind: Literal["STRIKE"] = "STRIKE"
    target_id: str


class ScoutOrder(_BaseOrder):
    kind: Literal["SCOUT"] = "SCOUT"
    target_hex: HexCoord


class OverwatchOrder(_BaseOrder):
    kind: Literal["OVERWATCH"] = "OVERWATCH"


class HoldOrder(_BaseOrder):
    kind: Literal["HOLD"] = "HOLD"


class CaptureOrder(_BaseOrder):
    kind: Literal["CAPTURE"] = "CAPTURE"
    target_hex: HexCoord


Order = Annotated[
    Union[
        MoveOrder, StrikeOrder, ScoutOrder,
        OverwatchOrder, HoldOrder, CaptureOrder,
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
