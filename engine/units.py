"""Unit catalog: type definitions and base attributes.

Numbers are illustrative for gameplay balance, not validated mil specs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitType:
    key: str           # short id used in YAML / JSON
    display: str       # human label
    side: str          # "blue" or "red" (some types are side-locked for flavor)
    domain: str        # "land" | "air" | "sea"
    glyph: str         # one-letter glyph for the visual
    speed: int         # max move budget (cost units) per turn
    sensor: int        # detection range in hexes
    weapon: int        # weapon range in hexes; 0 = ISR-only
    hp: int
    cost: int          # combat-power weighting for victory calc
    stealth: bool = False


CATALOG: dict[str, UnitType] = {
    # ---------------- Blue ----------------
    "f35": UnitType(
        key="f35", display="F-35", side="blue", domain="air", glyph="A",
        speed=6, sensor=6, weapon=4, hp=3, cost=100, stealth=True,
    ),
    "mq9": UnitType(
        key="mq9", display="MQ-9 Reaper", side="blue", domain="air", glyph="U",
        speed=4, sensor=7, weapon=3, hp=1, cost=30,
    ),
    "aegis": UnitType(
        key="aegis", display="Aegis DDG", side="blue", domain="sea", glyph="N",
        speed=2, sensor=6, weapon=5, hp=8, cost=120,
    ),
    "patriot": UnitType(
        key="patriot", display="Patriot SAM", side="blue", domain="land", glyph="S",
        speed=1, sensor=5, weapon=5, hp=3, cost=60,
    ),
    "m1a2": UnitType(
        key="m1a2", display="M1A2 Abrams", side="blue", domain="land", glyph="T",
        speed=2, sensor=2, weapon=2, hp=6, cost=50,
    ),
    "mech_b": UnitType(
        key="mech_b", display="Mech Infantry", side="blue", domain="land", glyph="I",
        speed=2, sensor=3, weapon=2, hp=4, cost=30,
    ),
    # ---------------- Red ----------------
    "j20": UnitType(
        key="j20", display="J-20", side="red", domain="air", glyph="A",
        speed=6, sensor=5, weapon=4, hp=3, cost=100, stealth=True,
    ),
    "type055": UnitType(
        key="type055", display="Type 055 DDG", side="red", domain="sea", glyph="N",
        speed=2, sensor=6, weapon=5, hp=8, cost=120,
    ),
    "hq9": UnitType(
        key="hq9", display="HQ-9 SAM", side="red", domain="land", glyph="S",
        speed=1, sensor=5, weapon=4, hp=3, cost=60,
    ),
    "shahed": UnitType(
        key="shahed", display="Shahed-136", side="red", domain="air", glyph="U",
        speed=3, sensor=1, weapon=2, hp=1, cost=5,
    ),
    "recon_uav": UnitType(
        key="recon_uav", display="Recon UAV", side="red", domain="air", glyph="U",
        speed=5, sensor=8, weapon=0, hp=1, cost=20,
    ),
    "mech_r": UnitType(
        key="mech_r", display="Mech Infantry", side="red", domain="land", glyph="I",
        speed=2, sensor=3, weapon=2, hp=4, cost=30,
    ),
}


def get(key: str) -> UnitType:
    if key not in CATALOG:
        raise KeyError(f"Unknown unit type: {key!r}. Known: {sorted(CATALOG)}")
    return CATALOG[key]
