"""Base / installation catalog.

Bases are fixed (immobile) entities that:
  - host platforms (capacity, spawn list)
  - may carry their own sensors and weapons (e.g., base air defense)
  - take damage but cannot move
  - are high-value targets — knock out the airbase, lose the F-35 sortie
    generation.

In the current build, bases are treated as platforms with `domain="fixed"`
and `speed=0`; combat resolution / sortie generation will key off the
spawns/capacity fields once the turn loop is in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["blue", "red"]
BaseDomain = Literal["land", "sea"]


@dataclass(frozen=True)
class Base:
    key: str
    display: str
    side: Side
    domain: BaseDomain
    glyph: str
    hp: int
    capacity: int           # max platforms simultaneously based here
    sensors: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()
    spawns: tuple[str, ...] = ()   # platform keys that originate here
    role: str = ""


BASES: dict[str, Base] = {
    # ============= Blue =============
    "blue_airbase": Base(
        key="blue_airbase", display="NAS Sigonella-Forward", side="blue",
        domain="land", glyph="B", hp=15, capacity=4,
        sensors=("radar_sam",),
        weapons=("sam_pac3",),
        spawns=("f35a", "mq9"),
        role="Joint air operations + ISR launch",
    ),
    "blue_navalbase": Base(
        key="blue_navalbase", display="Forward Operating Pier", side="blue",
        domain="land", glyph="B", hp=12, capacity=2,
        sensors=("radar_ddg",),
        weapons=("gun_5in",),
        spawns=("cg47",),
        role="Naval logistics + ASW boats",
    ),
    "blue_fob": Base(
        key="blue_fob", display="Forward Operating Base", side="blue",
        domain="land", glyph="B", hp=10, capacity=4,
        sensors=("radar_sam",),
        weapons=("sam_pac3",),
        spawns=("m1a2", "mech_b", "patriot"),
        role="Combined-arms FOB (armor + infantry + AAW)",
    ),

    # ============= Red =============
    "red_airbase": Base(
        key="red_airbase", display="PLAAF Forward Air Base", side="red",
        domain="land", glyph="B", hp=15, capacity=4,
        sensors=("radar_sam",),
        weapons=("sam_hq9",),
        spawns=("j20", "recon_uav"),
        role="Fixed-wing operations",
    ),
    "red_navalbase": Base(
        key="red_navalbase", display="PLAN Naval Base", side="red",
        domain="land", glyph="B", hp=12, capacity=2,
        sensors=("radar_ddg",),
        weapons=("gun_5in",),
        spawns=("type055",),
        role="Surface-action group home port",
    ),
    "red_launchsite": Base(
        key="red_launchsite", display="Shahed Launch Site", side="red",
        domain="land", glyph="B", hp=8, capacity=8,
        sensors=(),
        weapons=(),
        spawns=("shahed", "hq9", "mech_r"),
        role="OWA drone launch + ground reserves",
    ),
}


def get(key: str) -> Base:
    if key not in BASES:
        raise KeyError(f"Unknown base {key!r}. Known: {sorted(BASES)}")
    return BASES[key]


def for_side(side: str) -> list[Base]:
    return [b for b in BASES.values() if b.side == side]
