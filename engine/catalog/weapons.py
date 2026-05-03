"""Weapon catalog.

Pkill is a per-target-domain probability of kill on a single engagement
(no overlapping shots aggregated yet). Weapons declare their max engagement
range in hexes; finite ammo is supported (`ammo`) and infinite-ish weapons
use `ammo=-1`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


WeaponKind = Literal[
    "aam",            # air-to-air missile
    "asm_air",        # air-launched anti-surface missile
    "asm_ship",       # ship-launched anti-surface (incl. anti-ship)
    "sam",            # surface-to-air missile
    "gun_naval",      # naval main gun
    "gun_armor",      # armor / direct-fire gun
    "manpads",        # short-range man-portable AAM
    "loitering",      # one-way attack munition
]
TargetDomain = Literal["land", "air", "sea", "amphib", "fixed"]


@dataclass(frozen=True)
class Weapon:
    key: str
    display: str
    kind: WeaponKind
    range: int  # hexes
    pkill: dict[str, float] = field(default_factory=dict)
    ammo: int = -1     # -1 = unlimited
    notes: str = ""


WEAPONS: dict[str, Weapon] = {
    # ---- Air-to-air ----
    "aam_amraam": Weapon(
        key="aam_amraam", display="AIM-120D AMRAAM", kind="aam",
        range=4, ammo=4,
        pkill={"air": 0.75},
        notes="Active-radar BVR AAM.",
    ),
    "aam_pl15": Weapon(
        key="aam_pl15", display="PL-15", kind="aam",
        range=4, ammo=4,
        pkill={"air": 0.65},
        notes="PLAAF long-range AAM.",
    ),
    # ---- Air-to-surface ----
    "asm_jassm": Weapon(
        key="asm_jassm", display="JASSM-ER", kind="asm_air",
        range=5, ammo=2,
        pkill={"land": 0.65, "sea": 0.55, "fixed": 0.85},
        notes="Stealthy air-launched cruise missile.",
    ),
    "asm_lrasm": Weapon(
        key="asm_lrasm", display="LRASM", kind="asm_ship",
        range=5, ammo=4,
        pkill={"sea": 0.65},
        notes="Anti-ship cruise missile.",
    ),
    "asm_yj18": Weapon(
        key="asm_yj18", display="YJ-18 ASCM", kind="asm_ship",
        range=5, ammo=4,
        pkill={"sea": 0.65},
        notes="PLAN long-range anti-ship missile.",
    ),
    # ---- Surface-to-air ----
    "sam_pac3": Weapon(
        key="sam_pac3", display="MIM-104 Patriot PAC-3", kind="sam",
        range=5, ammo=4,
        pkill={"air": 0.75},
        notes="Hit-to-kill AAW + ABM interceptor.",
    ),
    "sam_sm6": Weapon(
        key="sam_sm6", display="RIM-174 SM-6", kind="sam",
        range=5, ammo=8,
        pkill={"air": 0.70, "sea": 0.30},
        notes="Multi-role naval SAM, also surface engagement.",
    ),
    "sam_hq9": Weapon(
        key="sam_hq9", display="HQ-9B", kind="sam",
        range=4, ammo=4,
        pkill={"air": 0.65},
        notes="PLA long-range SAM.",
    ),
    # ---- Guns / direct fire ----
    "gun_5in": Weapon(
        key="gun_5in", display="Mk 45 5-inch Gun", kind="gun_naval",
        range=2, ammo=-1,
        pkill={"land": 0.30, "sea": 0.25, "fixed": 0.40},
        notes="Naval main battery.",
    ),
    "gun_armor": Weapon(
        key="gun_armor", display="120mm APFSDS / Sabot", kind="gun_armor",
        range=1, ammo=-1,
        pkill={"land": 0.55, "amphib": 0.55},
        notes="Tank main gun.",
    ),
    # ---- Short-range AAW ----
    "manpads": Weapon(
        key="manpads", display="FIM-92 Stinger / equiv.", kind="manpads",
        range=1, ammo=2,
        pkill={"air": 0.40},
        notes="Squad-level point-defense AA.",
    ),
    # ---- Loitering munitions ----
    "shahed_oneway": Weapon(
        key="shahed_oneway", display="Shahed-136 Warhead", kind="loitering",
        range=0, ammo=1,
        pkill={"land": 0.40, "sea": 0.25, "fixed": 0.55},
        notes="One-way attack drone; self-destructs on target.",
    ),
}


def get(key: str) -> Weapon:
    if key not in WEAPONS:
        raise KeyError(f"Unknown weapon {key!r}. Known: {sorted(WEAPONS)}")
    return WEAPONS[key]
