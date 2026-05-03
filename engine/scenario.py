"""Load a scenario YAML and build an initial GameState."""
from __future__ import annotations

from pathlib import Path

import yaml

from engine.catalog import BASES, PLATFORMS, SENSORS, WEAPONS
from engine.catalog.bases import Base
from engine.catalog.platforms import Platform
from engine.state import (
    BaseInstance,
    GameState,
    HexCell,
    MapInfo,
    SensorRef,
    UnitInstance,
    VictoryConfig,
    WeaponRef,
)
from engine.terrain import TERRAIN_FROM_CHAR


def _sensor_refs(keys: tuple[str, ...]) -> list[SensorRef]:
    out: list[SensorRef] = []
    for k in keys:
        s = SENSORS[k]
        # Radars default OFF — player flips them ON via /api/sensor/toggle
        # when they actually want long-range coverage (and accept the
        # emission visibility). Passive sensors (eo/ir/sigint/sonar) are
        # always on.
        is_active = s.modality != "radar"
        out.append(SensorRef(
            key=s.key, display=s.display, modality=s.modality, range=s.range,
            los_required=s.los_required, detects_stealth=s.detects_stealth,
            target_domains=list(s.target_domains), emits=s.emits,
            is_active=is_active, notes=s.notes,
        ))
    return out


def _summary_sensor_range(sensors: list[SensorRef]) -> int:
    """Max range across only ACTIVE sensors. Radars contribute only when
    powered on; passives always count."""
    return max((s.range for s in sensors if s.is_active), default=0)


def _weapon_refs(keys: tuple[str, ...]) -> list[WeaponRef]:
    out: list[WeaponRef] = []
    for k in keys:
        w = WEAPONS[k]
        out.append(WeaponRef(
            key=w.key, display=w.display, kind=w.kind, range=w.range,
            damage=w.damage, self_destruct=w.self_destruct,
            target_domains=list(w.target_domains),
            ammo=w.ammo, notes=w.notes,
        ))
    return out


def _platform_to_unit(u: dict, p: Platform) -> UnitInstance:
    col, row = u["pos"]
    sensors = _sensor_refs(p.sensors)
    weapons = _weapon_refs(p.weapons)
    side = u["side"]
    return UnitInstance(
        id=u["id"],
        type=p.key,
        side=side,
        col=int(col),
        row=int(row),
        hp=int(u.get("hp", p.hp)),
        max_hp=p.hp,
        display=p.display,
        role=p.role,
        domain=p.domain,
        glyph=p.glyph,
        speed=p.speed,
        # Live summary range: only counts sensors currently active. Radars
        # don't contribute until the player turns them on.
        sensor=_summary_sensor_range(sensors),
        weapon=p.summary_weapon_range(),
        cost=p.cost,
        stealth=p.stealth,
        sensors=sensors,
        weapons=weapons,
    )


def _base_to_instance(b: dict, bt: Base) -> BaseInstance:
    col, row = b["pos"]
    sensors = _sensor_refs(bt.sensors)
    weapons = _weapon_refs(bt.weapons)
    sensor_max = _summary_sensor_range(sensors)
    weapon_max = max((w.range for w in weapons), default=0)
    side = b["side"]
    spawns = list(b.get("spawns", bt.spawns))
    return BaseInstance(
        id=b["id"],
        type=bt.key,
        side=side,
        col=int(col),
        row=int(row),
        hp=int(b.get("hp", bt.hp)),
        max_hp=bt.hp,
        display=bt.display,
        role=bt.role,
        domain=bt.domain,
        glyph=bt.glyph,
        capacity=bt.capacity,
        spawns=spawns,
        sensor=sensor_max,
        weapon=weapon_max,
        sensors=sensors,
        weapons=weapons,
    )


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

    map_info = MapInfo(cols=cols, rows=rows, cells=cells)

    unit_instances: list[UnitInstance] = []
    for u in raw.get("units", []):
        ptype = PLATFORMS.get(u["type"])
        if ptype is None:
            raise KeyError(f"unknown platform {u['type']!r}")
        unit_instances.append(_platform_to_unit(u, ptype))

    base_instances: list[BaseInstance] = []
    for b in raw.get("bases", []):
        btype = BASES.get(b["type"])
        if btype is None:
            raise KeyError(f"unknown base {b['type']!r}")
        base_instances.append(_base_to_instance(b, btype))

    victory = VictoryConfig(**raw.get("victory", {}))

    starting_total: dict[str, float] = {"blue": 0.0, "red": 0.0}
    starting_hp: dict[str, int] = {"blue": 0, "red": 0}
    for u in unit_instances:
        starting_total[u.side] += u.cost
        starting_hp[u.side] += u.max_hp
    for b in base_instances:
        starting_total[b.side] += 50.0  # bases worth 50 each (matches resolver)
        starting_hp[b.side] += b.max_hp

    return GameState(
        name=raw["name"],
        seed=int(raw.get("seed", 42)),
        turn=0,
        turn_limit=int(raw.get("turn_limit", 30)),
        map=map_info,
        units=unit_instances,
        bases=base_instances,
        victory=victory,
        starting_total=starting_total,
        starting_hp=starting_hp,
    )
