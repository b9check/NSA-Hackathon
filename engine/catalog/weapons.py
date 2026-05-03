"""Weapon catalog.

Deterministic damage. Each weapon declares its max engagement range in
hexes, the flat damage applied to every enemy in the target hex on a hit,
and an ammo budget (-1 = unlimited). `self_destruct=True` means the
attacker dies after firing — kamikaze munitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


WeaponKind = Literal[
    "gun",        # short-range direct fire (rifle, armor gun, point defense)
    "missile",    # general-purpose missile (AAM/ASM, ship battery)
    "sam",        # surface-to-air missile (long-range AAW)
    "kamikaze",   # one-way attack munition (self-destruct)
    "bomb",       # heavy gravity bombs / standoff munitions
]


@dataclass(frozen=True)
class Weapon:
    key: str
    display: str
    kind: WeaponKind
    range: int
    damage: int
    # Which target domains this weapon can damage. Defaults to "any" so
    # most weapons stay versatile. Bombers, MANPADS-style, and SAMs use
    # this to restrict (e.g. bomber can't shoot down fighters).
    target_domains: tuple = ("land", "air", "sea")
    ammo: int = -1                # -1 = unlimited
    self_destruct: bool = False
    notes: str = ""


WEAPONS: dict[str, Weapon] = {
    # ---- Ground ----
    "inf_rifle": Weapon(
        key="inf_rifle", display="Rifle / MANPADS", kind="gun",
        range=1, damage=1, ammo=-1,
        target_domains=("land", "air"),
        notes="Direct fire vs ground; manpads vs low-flying air. No anti-ship.",
    ),
    "armor_gun": Weapon(
        key="armor_gun", display="Armor Gun", kind="gun",
        range=1, damage=2, ammo=-1,
        target_domains=("land",),
        notes="Heavy direct fire vs ground only.",
    ),
    "sam": Weapon(
        key="sam", display="SAM", kind="sam",
        range=4, damage=2, ammo=4,
        target_domains=("air", "sea"),
        notes="Long-range AAW battery; can be cued vs surface ships.",
    ),
    # ---- Air ----
    "kamikaze": Weapon(
        key="kamikaze", display="OWA Warhead", kind="kamikaze",
        range=0, damage=3, ammo=1, self_destruct=True,
        target_domains=("land", "air", "sea"),
        notes="One-way attack munition. Attacker detonates on target hex.",
    ),
    "aam_asm": Weapon(
        key="aam_asm", display="AAM / ASM", kind="missile",
        range=3, damage=2, ammo=4,
        target_domains=("land", "air", "sea"),
        notes="Multi-role air-launched missile.",
    ),
    "bomb": Weapon(
        key="bomb", display="Heavy Bombs", kind="bomb",
        range=2, damage=4, ammo=2,
        target_domains=("land", "sea"),
        notes="Highest single-hit damage. No anti-air capability.",
    ),
    # ---- Sea ----
    "ship_battery": Weapon(
        key="ship_battery", display="Missile Battery", kind="missile",
        range=4, damage=2, ammo=6,
        target_domains=("land", "air", "sea"),
        notes="Naval VLS — multi-role.",
    ),
    # ---- Base ----
    "point_defense": Weapon(
        key="point_defense", display="Point Defense", kind="gun",
        range=2, damage=1, ammo=-1,
        target_domains=("land", "air"),
        notes="Base point-defense battery.",
    ),
}


def get(key: str) -> Weapon:
    if key not in WEAPONS:
        raise KeyError(f"Unknown weapon {key!r}. Known: {sorted(WEAPONS)}")
    return WEAPONS[key]
