"""Game state pydantic models.

These are also the wire format consumed by the frontend (web/public/state.json)
and the future LLM agent driver. Keep field names short and JSON-friendly.
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


class SensorRef(BaseModel):
    """Denormalized snapshot of one of a unit's attached sensors.
    Carries everything a fusion layer needs without re-resolving the catalog."""
    key: str
    display: str
    modality: str          # "radar" | "eo" | "ir" | "sigint" | "sonar"
    range: int
    los_required: bool = False
    detects_stealth: bool = True
    target_domains: list[str] = Field(default_factory=list)
    emits: bool = False
    notes: str = ""


class WeaponRef(BaseModel):
    """Denormalized snapshot of one of a unit's attached weapons."""
    key: str
    display: str
    kind: str              # "aam" | "asm_air" | "asm_ship" | "sam" | ...
    range: int
    pkill: dict[str, float] = Field(default_factory=dict)
    ammo: int = -1
    notes: str = ""


class UnitInstance(BaseModel):
    id: str
    type: str               # platform key into engine.catalog.PLATFORMS
    side: Side
    col: int
    row: int
    hp: int
    max_hp: int
    # Denormalized lookup fields (so the frontend / agent never has to
    # resolve the catalog).
    display: str
    role: str = ""
    domain: str             # "land" | "air" | "sea" | "amphib"
    glyph: str
    speed: int
    sensor: int             # max range across attached sensors (summary)
    weapon: int             # max range across attached weapons (summary)
    cost: int
    stealth: bool = False
    sensors: list[SensorRef] = Field(default_factory=list)
    weapons: list[WeaponRef] = Field(default_factory=list)


class BaseInstance(BaseModel):
    """A fixed installation. Cannot move; can be damaged; spawns/repairs
    platforms once the turn loop is in. Rendered as a distinct glyph.
    """
    id: str
    type: str               # base key into engine.catalog.BASES
    side: Side
    col: int
    row: int
    hp: int
    max_hp: int
    display: str
    role: str = ""
    domain: str             # "land" | "sea"
    glyph: str
    capacity: int
    spawns: list[str] = Field(default_factory=list)
    sensor: int = 0
    weapon: int = 0
    sensors: list[SensorRef] = Field(default_factory=list)
    weapons: list[WeaponRef] = Field(default_factory=list)


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
    bases: list[BaseInstance] = Field(default_factory=list)
    victory: VictoryConfig
    # Pre-computed denominators for compute_scores so the score curve
    # doesn't artificially flatten when destroyed units leave the list.
    # Keys: "blue", "red".
    starting_total: dict[str, float] = Field(default_factory=dict)
    # Accumulated objective-control bonus, capped per side. Drives the
    # second half of compute_scores().
    objective_points: dict[str, float] = Field(
        default_factory=lambda: {"blue": 0.0, "red": 0.0},
    )

    def unit_by_id(self, uid: str) -> UnitInstance | None:
        return next((u for u in self.units if u.id == uid), None)

    def units_at(self, col: int, row: int) -> list[UnitInstance]:
        return [u for u in self.units if u.col == col and u.row == row]

    def base_by_id(self, bid: str) -> BaseInstance | None:
        return next((b for b in self.bases if b.id == bid), None)
