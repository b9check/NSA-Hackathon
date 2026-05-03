"""Game state pydantic models.

These are also the wire format consumed by the frontend (web/public/state.json)
and the future LLM agent driver. Keep field names short and JSON-friendly.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from engine.terrain import Terrain


# ---------------------------------------------------------------------------
# OPLAN — briefing, phased objectives. Optional/defaulted so back-compat holds.
# ---------------------------------------------------------------------------

class Objective(BaseModel):
    label: str
    kind: Literal["destroy", "hold", "transit", "deny", "recon"] = "destroy"
    target_ids: list[str] = Field(default_factory=list)
    target_hex: Optional[tuple[int, int]] = None
    completed: bool = False


class OpPhase(BaseModel):
    name: str
    description: str = ""
    objectives: list[Objective] = Field(default_factory=list)
    completed: bool = False


class Briefing(BaseModel):
    title: str = ""
    situation: str = ""
    mission: str = ""
    rules_of_engagement: str = ""


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
    # Sortie / endurance tracking. 0 endurance = no logistics tracked
    # (ground/sea units). Air units count time_in_air_minutes up toward
    # endurance_minutes; when the remainder gets tight they must RTB.
    endurance_minutes: int = 0
    time_in_air_minutes: int = 0
    home_base_id: Optional[str] = None


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


class Mission(BaseModel):
    """Operational-level standing order for a single unit.

    Replaces per-turn MOVE/STRIKE micromanagement. The mission persists across
    turns; each turn, engine.missions.generate_orders_from_missions emits a
    concrete Order based on current state + ROE. Player intervenes when a halt
    condition fires (e.g. enemy contact made, HP low, target reached).
    """
    unit_id: str
    target_hex: tuple[int, int]                       # final destination
    roe: Literal["engage", "surveil", "avoid"] = "engage"
    radar_state: Literal["on", "off", "auto"] = "auto"
    # Halt-on-X flags. All default to on so the player gets a chance to react.
    halt_on_contact: bool = True
    halt_on_low_hp: bool = True
    halt_on_no_ammo: bool = True
    # Hard cap for runaway protection — stops the auto-loop after this many
    # consecutive turns even if no other halt fires.
    max_turns: int = 8
    # Free-text reasoning (UI tooltip / replay narrative)
    intent: str = ""


class Contact(BaseModel):
    """One side's belief about a single enemy entity (unit or base).

    Decoupled from `UnitInstance` so the frontend can show the *intel
    picture* (with uncertainty + class probabilities) without leaking
    ground truth that the side hasn't observed.

    `currently_observed` False means this is a stale ghost — the entity
    was previously sensed but is not in any active sensor's coverage now.
    Position uncertainty grows for ghosts, more so for likely-mobile
    classes (aircraft expand fast, fixed sites barely move).
    """
    contact_id: str           # mirrors target unit/base id (so click-pick is trivial)
    target_kind: Literal["unit", "base"] = "unit"
    believed_col: int
    believed_row: int
    position_uncertainty_hexes: float       # 0 = exact hex; >0 = could be in a radius
    existence: float                        # P(real) — 0..1
    class_probs: dict[str, float] = Field(default_factory=dict)  # platform-key -> P(class)
    last_refined_turn: int                  # turn the contact was last freshly observed
    currently_observed: bool = True
    contributing_sensor_ids: list[str] = Field(default_factory=list)
    # The sensor modality(ies) currently or most-recently providing the strongest cue.
    # Used by the UI to label the contact ("RADAR FIX", "VISUAL", "SIGINT FIX").
    dominant_modality: str = ""


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
    # Sim-clock in minutes since scenario start. Each resolve_turn advances
    # by `minutes_per_turn` (default 30). The HUD renders this as
    # "D+0 14:32" so the player has a real sense of operational tempo.
    sim_clock_minutes: int = 0
    minutes_per_turn: int = 30
    # OPLAN — optional briefing + phased objectives. None / [] = legacy scenarios.
    briefing: Optional[Briefing] = None
    phases: list[OpPhase] = Field(default_factory=list)
    current_phase: int = 0
    # Per-side fused intelligence picture. Each side's list contains one Contact
    # per enemy entity that side has observed (now or recently). Recomputed by
    # engine.sensing after every resolve_turn.
    contacts: dict[str, list[Contact]] = Field(default_factory=lambda: {"blue": [], "red": []})
    # Standing missions, keyed by unit_id. Persist across turns until cleared
    # or replaced. Generated per-turn into concrete Orders by engine.missions.
    missions: dict[str, Mission] = Field(default_factory=dict)

    def unit_by_id(self, uid: str) -> Optional[UnitInstance]:
        return next((u for u in self.units if u.id == uid), None)

    def units_at(self, col: int, row: int) -> list[UnitInstance]:
        return [u for u in self.units if u.col == col and u.row == row]

    def base_by_id(self, bid: str) -> Optional[BaseInstance]:
        return next((b for b in self.bases if b.id == bid), None)
