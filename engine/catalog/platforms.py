"""Platform catalog.

A Platform is the ID-card for a real-world unit type (F-35, Type 055,
Patriot battery, etc.). It composes one or more Sensors and Weapons by
key, plus mobility and durability stats.

Use Platform.summary_sensor_range() / summary_weapon_range() to flatten
into the int fields the existing engine and frontend still use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from engine.catalog.sensors import SENSORS
from engine.catalog.weapons import WEAPONS


Side = Literal["blue", "red"]
Domain = Literal["land", "air", "sea", "amphib"]


@dataclass(frozen=True)
class Platform:
    key: str
    display: str
    side: Side
    domain: Domain
    glyph: str
    speed: int
    hp: int
    cost: int
    role: str = ""
    stealth: bool = False
    sensors: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()

    def summary_sensor_range(self) -> int:
        if not self.sensors:
            return 0
        return max(SENSORS[s].range for s in self.sensors)

    def summary_weapon_range(self) -> int:
        if not self.weapons:
            return 0
        return max(WEAPONS[w].range for w in self.weapons)


PLATFORMS: dict[str, Platform] = {
    # =========================================================
    # Blue Order of Battle
    # =========================================================
    "f35a": Platform(
        key="f35a", display="F-35A Lightning II", side="blue",
        domain="air", glyph="A",
        speed=6, hp=3, cost=100, stealth=True,
        sensors=("radar_aam", "rwr"),
        weapons=("aam_amraam", "asm_jassm"),
        role="5th-gen multirole stealth fighter",
    ),
    "mq9": Platform(
        key="mq9", display="MQ-9 Reaper", side="blue",
        domain="air", glyph="U",
        speed=4, hp=1, cost=30,
        sensors=("eo_uav",),
        weapons=("asm_jassm",),
        role="MALE ISR + strike UAV",
    ),
    "cg47": Platform(
        key="cg47", display="USS Aegis CG (Ticonderoga)", side="blue",
        domain="sea", glyph="N",
        speed=2, hp=8, cost=120,
        sensors=("radar_ddg", "esm_ddg", "sonar_hull"),
        weapons=("sam_sm6", "asm_lrasm", "gun_5in"),
        role="Aegis cruiser — primary AAW + cued anti-surface",
    ),
    "patriot": Platform(
        key="patriot", display="Patriot PAC-3 Battery", side="blue",
        domain="land", glyph="S",
        speed=1, hp=3, cost=60,
        sensors=("radar_sam",),
        weapons=("sam_pac3",),
        role="Long-range AAW / ABM battery",
    ),
    "m1a2": Platform(
        key="m1a2", display="M1A2 SEPv3 Abrams", side="blue",
        domain="land", glyph="T",
        speed=2, hp=6, cost=50,
        sensors=("tank_optics",),
        weapons=("gun_armor",),
        role="Main battle tank",
    ),
    "mech_b": Platform(
        key="mech_b", display="Mech Infantry (US)", side="blue",
        domain="amphib", glyph="I",
        speed=2, hp=4, cost=30,
        sensors=("mk1_eyeball",),
        weapons=("manpads", "gun_armor"),
        role="Mechanized infantry company w/ Stinger team",
    ),

    # =========================================================
    # Red Order of Battle
    # =========================================================
    "j20": Platform(
        key="j20", display="J-20 Mighty Dragon", side="red",
        domain="air", glyph="A",
        speed=6, hp=3, cost=100, stealth=True,
        sensors=("radar_aam", "rwr"),
        weapons=("aam_pl15",),
        role="5th-gen stealth fighter",
    ),
    "recon_uav": Platform(
        key="recon_uav", display="WZ-7 Soaring Dragon", side="red",
        domain="air", glyph="U",
        speed=5, hp=1, cost=20,
        sensors=("eo_recon",),
        weapons=(),
        role="High-altitude long-endurance ISR",
    ),
    "type055": Platform(
        key="type055", display="Type 055 Renhai DDG", side="red",
        domain="sea", glyph="N",
        speed=2, hp=8, cost=120,
        sensors=("radar_ddg", "esm_ddg", "sonar_hull"),
        weapons=("asm_yj18", "gun_5in"),
        role="PLAN cruiser — heavy ASuW + AAW",
    ),
    "hq9": Platform(
        key="hq9", display="HQ-9B SAM Battery", side="red",
        domain="land", glyph="S",
        speed=1, hp=3, cost=60,
        sensors=("radar_sam",),
        weapons=("sam_hq9",),
        role="Long-range SAM",
    ),
    "shahed": Platform(
        key="shahed", display="Shahed-136", side="red",
        domain="air", glyph="U",
        speed=3, hp=1, cost=5,
        sensors=(),
        weapons=("shahed_oneway",),
        role="Loitering one-way attack munition",
    ),
    "mech_r": Platform(
        key="mech_r", display="Mech Infantry (OPFOR)", side="red",
        domain="amphib", glyph="I",
        speed=2, hp=4, cost=30,
        sensors=("mk1_eyeball",),
        weapons=("manpads", "gun_armor"),
        role="Mechanized infantry company w/ MANPADS team",
    ),
}


def get(key: str) -> Platform:
    if key not in PLATFORMS:
        raise KeyError(f"Unknown platform {key!r}. Known: {sorted(PLATFORMS)}")
    return PLATFORMS[key]
