"""Load a scenario YAML and build an initial GameState."""
from __future__ import annotations

from pathlib import Path

import yaml

from engine import units as catalog
from engine.state import (
    GameState,
    HexCell,
    MapInfo,
    Objective,
    UnitInstance,
    VictoryConfig,
)
from engine.terrain import TERRAIN_FROM_CHAR


def load_scenario(path: str | Path) -> GameState:
    raw = yaml.safe_load(Path(path).read_text())

    map_block = raw["map"]
    cols = int(map_block["cols"])
    rows = int(map_block["rows"])
    terrain_str = map_block["terrain"].strip("\n")
    terrain_lines = [line.rstrip() for line in terrain_str.splitlines()]
    if len(terrain_lines) != rows:
        raise ValueError(
            f"terrain has {len(terrain_lines)} rows, expected {rows}"
        )

    cells: list[HexCell] = []
    for row_idx, line in enumerate(terrain_lines):
        if len(line) != cols:
            raise ValueError(
                f"row {row_idx} has {len(line)} cols, expected {cols}: {line!r}"
            )
        for col_idx, ch in enumerate(line):
            if ch not in TERRAIN_FROM_CHAR:
                raise ValueError(
                    f"unknown terrain char {ch!r} at ({col_idx},{row_idx})"
                )
            cells.append(
                HexCell(col=col_idx, row=row_idx, terrain=TERRAIN_FROM_CHAR[ch])
            )

    objectives = [
        Objective(col=int(c), row=int(r))
        for c, r in map_block.get("objective_hexes", [])
    ]

    map_info = MapInfo(
        cols=cols, rows=rows, cells=cells, objective_hexes=objectives,
    )

    unit_instances: list[UnitInstance] = []
    for u in raw["units"]:
        utype = catalog.get(u["type"])
        col, row = u["pos"]
        unit_instances.append(
            UnitInstance(
                id=u["id"],
                type=utype.key,
                side=utype.side,  # type: ignore[arg-type]
                col=int(col),
                row=int(row),
                hp=int(u.get("hp", utype.hp)),
                display=utype.display,
                domain=utype.domain,
                glyph=utype.glyph,
                speed=utype.speed,
                sensor=utype.sensor,
                weapon=utype.weapon,
                cost=utype.cost,
                stealth=utype.stealth,
            )
        )

    victory = VictoryConfig(**raw.get("victory", {}))

    return GameState(
        name=raw["name"],
        seed=int(raw.get("seed", 42)),
        turn=0,
        turn_limit=int(raw.get("turn_limit", 30)),
        map=map_info,
        units=unit_instances,
        victory=victory,
    )
