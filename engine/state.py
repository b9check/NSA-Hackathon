"""Game state pydantic models.

These are also the wire format consumed by the frontend (web/public/state.json)
and the future LLM agent driver. Keep field names short and JSON-friendly.
"""
from __future__ import annotations

from typing import Literal, Optional

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
    # Toggle: only meaningful for `radar` modality. Passive sensors are always on.
    is_active: bool = True


class WeaponRef(BaseModel):
    """Denormalized snapshot of one of a unit's attached weapons."""
    key: str
    display: str
    kind: str              # "gun" | "missile" | "sam" | "kamikaze" | "bomb"
    range: int
    damage: int = 1                # deterministic HP removed per hit
    self_destruct: bool = False    # attacker dies after firing (one-shot)
    target_domains: list[str] = Field(default_factory=lambda: ["land", "air", "sea"])
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


class VictoryConfig(BaseModel):
    # Side loses if its current HP total drops to <= this fraction of its
    # starting HP total.
    hp_loss_threshold: float = 0.25
    # Hard cap so a turtle deadlock can't run forever. Higher HP%
    # wins on cap; ties = draw.
    turn_cap: int = 20


class MapInfo(BaseModel):
    cols: int
    rows: int
    cells: list[HexCell]


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
    # Total HP at game start (units + bases), per side. Denominator for
    # the HP-threshold win condition.
    starting_hp: dict[str, int] = Field(default_factory=dict)
    # Game-over decision once the winner is determined. None = match
    # in progress.
    winner: Optional[str] = None       # "blue" | "red" | "draw" | None
    win_reason: Optional[str] = None   # "hp_collapse" | "turn_cap" | "annihilation"

    def unit_by_id(self, uid: str) -> Optional[UnitInstance]:
        return next((u for u in self.units if u.id == uid), None)

    def units_at(self, col: int, row: int) -> list[UnitInstance]:
        return [u for u in self.units if u.col == col and u.row == row]

    def base_by_id(self, bid: str) -> Optional[BaseInstance]:
        return next((b for b in self.bases if b.id == bid), None)
