"""Terrain types and per-domain movement / detection modifiers."""
from __future__ import annotations

from enum import Enum


class Terrain(str, Enum):
    OPEN = "open"
    URBAN = "urban"
    FOREST = "forest"
    MOUNTAIN = "mountain"
    WATER = "water"


# ASCII char in scenario YAML -> Terrain
TERRAIN_FROM_CHAR: dict[str, Terrain] = {
    "P": Terrain.OPEN,
    "U": Terrain.URBAN,
    "F": Terrain.FOREST,
    "M": Terrain.MOUNTAIN,
    "O": Terrain.WATER,
}


# Movement cost per (domain, terrain). math.inf means impassable.
# Domains: "land", "air", "sea".
INF = 10_000


MOVE_COST: dict[tuple[str, Terrain], int] = {
    ("land", Terrain.OPEN): 1,
    ("land", Terrain.URBAN): 2,
    ("land", Terrain.FOREST): 2,
    ("land", Terrain.MOUNTAIN): 3,
    ("land", Terrain.WATER): INF,
    ("air", Terrain.OPEN): 1,
    ("air", Terrain.URBAN): 1,
    ("air", Terrain.FOREST): 1,
    ("air", Terrain.MOUNTAIN): 2,
    ("air", Terrain.WATER): 1,
    ("sea", Terrain.OPEN): INF,
    ("sea", Terrain.URBAN): INF,
    ("sea", Terrain.FOREST): INF,
    ("sea", Terrain.MOUNTAIN): INF,
    ("sea", Terrain.WATER): 1,
}


# Detection modifier when sensing INTO a hex of this terrain.
# 1.0 = no change; <1.0 = harder to detect things in that hex.
DETECTION_MOD: dict[Terrain, float] = {
    Terrain.OPEN: 1.0,
    Terrain.URBAN: 0.6,
    Terrain.FOREST: 0.7,
    Terrain.MOUNTAIN: 0.8,
    Terrain.WATER: 1.0,
}


def can_traverse(domain: str, terrain: Terrain) -> bool:
    return MOVE_COST[(domain, terrain)] < INF


def move_cost(domain: str, terrain: Terrain) -> int:
    return MOVE_COST[(domain, terrain)]


def detection_mod(terrain: Terrain) -> float:
    return DETECTION_MOD[terrain]
