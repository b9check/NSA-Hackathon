"""Base catalog — one abstract base type.

Bases are fixed (immobile, hp 12), shared across both sides. `side` and
`spawns` (which platforms spawn here, future) are set per instance in the
scenario YAML, not on the base type itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


BaseDomain = Literal["land", "sea"]


@dataclass(frozen=True)
class Base:
    key: str
    display: str
    domain: BaseDomain
    glyph: str
    hp: int
    capacity: int
    sensors: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()
    spawns: tuple[str, ...] = ()
    role: str = ""


BASES: dict[str, Base] = {
    "base": Base(
        key="base", display="Base",
        domain="land", glyph="B",
        hp=12, capacity=4,
        sensors=("passive_2", "radar_4"),
        weapons=("point_defense",),
        spawns=(),  # set per-instance in scenario yaml
        role="Stationary HP bank; future: spawn point",
    ),
}


def get(key: str) -> Base:
    if key not in BASES:
        raise KeyError(f"Unknown base {key!r}. Known: {sorted(BASES)}")
    return BASES[key]


def for_side(side: str) -> list[Base]:
    return list(BASES.values())
