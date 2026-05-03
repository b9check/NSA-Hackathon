"""Event log emitted by the combat resolver.

The frontend consumes this list, animates each event, then snaps to the
post-resolution GameState. Each event is pure data (no Python references)
and JSON-serializable so it can also be persisted as a replay tape.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel


HexCoord = tuple[int, int]


class _BaseEvent(BaseModel):
    pass


class ScoutRevealEvent(_BaseEvent):
    type: Literal["scout_reveal"] = "scout_reveal"
    side: Literal["blue", "red"]
    observer: str           # unit_id
    revealed_units: list[str]
    revealed_hexes: list[HexCoord]


class MoveEvent(_BaseEvent):
    type: Literal["move"] = "move"
    unit: str               # unit_id
    path: list[HexCoord]    # waypoint list including start and end
    stopped_short: bool = False


class StrikeEvent(_BaseEvent):
    type: Literal["strike"] = "strike"
    attacker: str
    target: str             # unit_id or base_id
    weapon: str             # weapon key
    weapon_kind: str
    pkill: float
    roll: float
    hit: bool
    damage: int
    remaining_hp: int


class OverwatchFireEvent(_BaseEvent):
    type: Literal["overwatch_fire"] = "overwatch_fire"
    attacker: str
    target: str
    weapon: str
    pkill: float
    roll: float
    hit: bool
    damage: int
    trigger_hex: HexCoord


class CaptureEvent(_BaseEvent):
    type: Literal["capture"] = "capture"
    hex: HexCoord
    side: Literal["blue", "red"]
    counter: int
    threshold: int
    controller: Literal["blue", "red", None] = None


class DestroyedEvent(_BaseEvent):
    type: Literal["destroyed"] = "destroyed"
    entity_id: str
    side: Literal["blue", "red"]
    is_base: bool = False


class TurnEndEvent(_BaseEvent):
    type: Literal["turn_end"] = "turn_end"
    turn: int
    blue_score: float
    red_score: float


Event = Union[
    ScoutRevealEvent,
    MoveEvent,
    StrikeEvent,
    OverwatchFireEvent,
    CaptureEvent,
    DestroyedEvent,
    TurnEndEvent,
]
