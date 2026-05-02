"""Game state pydantic models.

These are also the wire format consumed by the frontend (web/public/state.json).
Keep field names short and JSON-friendly.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from engine.terrain import Terrain


Side = Literal["blue", "red"]


class HexCell(BaseModel):
    col: int
    row: int
    terrain: Terrain


class UnitInstance(BaseModel):
    id: str
    type: str       # key into engine.units.CATALOG
    side: Side
    col: int
    row: int
    hp: int
    # cached lookup fields (denormalized for the frontend / agent)
    display: str
    domain: str     # "land" | "air" | "sea" | "amphib"
    glyph: str
    speed: int
    sensor: int
    weapon: int
    cost: int
    stealth: bool = False


class Objective(BaseModel):
    col: int
    row: int


class VictoryConfig(BaseModel):
    capture_hold_turns: int = 2
    combat_power_threshold: float = 0.6


class MapInfo(BaseModel):
    cols: int
    rows: int
    cells: list[HexCell]
    objective_hexes: list[Objective] = Field(default_factory=list)


class GameState(BaseModel):
    name: str
    seed: int
    turn: int = 0
    turn_limit: int
    map: MapInfo
    units: list[UnitInstance]
    victory: VictoryConfig

    def unit_by_id(self, uid: str) -> UnitInstance | None:
        return next((u for u in self.units if u.id == uid), None)

    def units_at(self, col: int, row: int) -> list[UnitInstance]:
        return [u for u in self.units if u.col == col and u.row == row]
