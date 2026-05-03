"""Event log emitted by the combat resolver.

The frontend consumes this list, animates each event, then snaps to the
post-resolution GameState. Each event is pure data (no Python references)
and JSON-serializable so it can also be persisted as a replay tape.
"""
from __future__ import annotations

from typing import Literal, Tuple, Union

from pydantic import BaseModel


HexCoord = Tuple[int, int]


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
    """One STRIKE order resolved.

    `target_hex` is what was aimed at; `targets_hit` lists every enemy
    entity_id in that hex after MOVE that took damage. `whiffed=True`
    means the target hex had no enemy units after MOVE — but the strike
    still consumed its ammo and a self-destruct attacker still dies.
    """
    type: Literal["strike"] = "strike"
    attacker: str
    weapon: str
    weapon_kind: str
    target_hex: HexCoord
    damage: int                       # base damage applied to each target
    targets_hit: list[str] = []       # entity_ids damaged this strike
    whiffed: bool = False
    self_destruct: bool = False       # attacker died as part of firing
    counter_damage: int = 0           # damage taken by attacker (melee)


class OverwatchFireEvent(_BaseEvent):
    type: Literal["overwatch_fire"] = "overwatch_fire"
    attacker: str
    target: str
    weapon: str
    damage: int
    trigger_hex: HexCoord


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
    DestroyedEvent,
    TurnEndEvent,
]
