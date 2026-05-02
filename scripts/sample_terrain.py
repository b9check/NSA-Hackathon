"""Sample the satellite backdrop at each hex center to derive a terrain map
that matches the actual imagery, then rebuild scenarios/strait_n7.yaml so the
hex grid + unit placements line up with what the eye sees.

Output:
  - Prints the derived ASCII terrain map (15 rows x 20 cols).
  - Rewrites scenarios/strait_n7.yaml with the new map block + objectives +
    auto-placed unit starting positions (deterministic per scenario seed).

Usage:
    python3 scripts/sample_terrain.py web/public/terrain.png \
        scenarios/strait_n7.yaml
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image


HEX_SIZE = 30.0
PAD_X = 24
PAD_Y = 24

CHAR = {"water": "O", "open": "P", "forest": "F", "mountain": "M", "urban": "U"}


def hex_to_pixel(col: int, row: int) -> tuple[int, int]:
    w = math.sqrt(3.0) * HEX_SIZE
    x = w * (col + 0.5 * (row & 1)) + PAD_X
    y = HEX_SIZE * 1.5 * row + PAD_Y
    return int(round(x)), int(round(y))


def classify(rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    mx = max(r, g, b)
    mn = min(r, g, b)
    val = mx
    sat = 0.0 if mx == 0 else (mx - mn) / mx

    # Water: blue dominates and overall dark/saturated.
    if b > r + 8 and b > g - 4 and val < 110:
        return "water"
    # Beach / very pale = open (sandy or clearings).
    if val > 175 and sat < 0.18:
        return "open"
    # Urban: medium-bright neutral gray (fairly desaturated, mid-bright).
    if sat < 0.18 and 110 < val < 175:
        return "urban"
    # Mountain: warm brownish with red >= green and low blue, mid brightness.
    if r >= g and (r - b) > 18 and val > 85:
        return "mountain"
    # Forest: green dominant, darker.
    if g >= r and g >= b and val <= 130:
        return "forest"
    # Default open (lighter vegetation).
    return "open"


def derive_terrain(img: np.ndarray, cols: int, rows: int) -> list[list[str]]:
    grid: list[list[str]] = []
    for row in range(rows):
        line: list[str] = []
        for col in range(cols):
            x, y = hex_to_pixel(col, row)
            x0, y0 = max(0, x - 4), max(0, y - 4)
            x1, y1 = min(img.shape[1], x + 5), min(img.shape[0], y + 5)
            patch = img[y0:y1, x0:x1]
            avg = patch.mean(axis=(0, 1))
            line.append(classify(tuple(float(v) for v in avg[:3])))
        grid.append(line)
    return grid


def hex_in_bounds(col: int, row: int, cols: int, rows: int) -> bool:
    return 0 <= col < cols and 0 <= row < rows


def find_hex(grid, predicate, rng, exclude: set, cols: int, rows: int):
    """Pick a random hex matching predicate, excluding already-used positions."""
    candidates = [
        (c, r)
        for r in range(rows) for c in range(cols)
        if predicate(grid[r][c]) and (c, r) not in exclude
    ]
    if not candidates:
        return None
    return rng.choice(candidates)


def place_units(grid, cols: int, rows: int, seed: int) -> list[dict]:
    """Auto-place 16 units (7 Blue south + 9 Red north) on terrain-legal hexes."""
    rng = random.Random(seed)
    used: set[tuple[int, int]] = set()

    # Side-half filters
    south = lambda c, r: r >= rows * 2 / 3
    north = lambda c, r: r < rows / 3
    south_mid = lambda c, r: r >= rows / 2
    north_mid = lambda c, r: r < rows / 2

    def pick(side_pred, terrain_pred, fallback_terrain_pred=None):
        cells = [
            (c, r) for r in range(rows) for c in range(cols)
            if side_pred(c, r) and terrain_pred(grid[r][c]) and (c, r) not in used
        ]
        if not cells and fallback_terrain_pred:
            cells = [
                (c, r) for r in range(rows) for c in range(cols)
                if side_pred(c, r) and fallback_terrain_pred(grid[r][c]) and (c, r) not in used
            ]
        if not cells:
            return None
        c, r = rng.choice(cells)
        used.add((c, r))
        return [c, r]

    is_water = lambda t: t == "water"
    is_land = lambda t: t != "water"
    is_settled = lambda t: t in ("urban", "open")

    units: list[dict] = []

    # ---- Blue (south) ----
    units.append({"id": "blue-aegis-1",   "type": "aegis",   "pos": pick(south_mid, is_water)})
    units.append({"id": "blue-f35-1",     "type": "f35",     "pos": pick(south_mid, lambda t: True)})
    units.append({"id": "blue-mq9-1",     "type": "mq9",     "pos": pick(south_mid, lambda t: True)})
    units.append({"id": "blue-patriot-1", "type": "patriot", "pos": pick(south, is_settled, is_land)})
    units.append({"id": "blue-m1a2-1",    "type": "m1a2",    "pos": pick(south, is_settled, is_land)})
    units.append({"id": "blue-mech-1",    "type": "mech_b",  "pos": pick(south, is_land)})
    units.append({"id": "blue-mech-2",    "type": "mech_b",  "pos": pick(south, is_land)})

    # ---- Red (north) ----
    units.append({"id": "red-type055-1",  "type": "type055",   "pos": pick(north_mid, is_water)})
    units.append({"id": "red-j20-1",      "type": "j20",       "pos": pick(north_mid, lambda t: True)})
    units.append({"id": "red-recon-1",    "type": "recon_uav", "pos": pick(north_mid, lambda t: True)})
    units.append({"id": "red-hq9-1",      "type": "hq9",       "pos": pick(north, is_settled, is_land)})
    units.append({"id": "red-mech-1",     "type": "mech_r",    "pos": pick(north, is_land)})
    units.append({"id": "red-mech-2",     "type": "mech_r",    "pos": pick(north, is_land)})
    units.append({"id": "red-shahed-1",   "type": "shahed",    "pos": pick(north_mid, lambda t: True)})
    units.append({"id": "red-shahed-2",   "type": "shahed",    "pos": pick(north_mid, lambda t: True)})
    units.append({"id": "red-shahed-3",   "type": "shahed",    "pos": pick(north_mid, lambda t: True)})

    return [u for u in units if u["pos"] is not None]


def pick_objectives(grid, cols: int, rows: int) -> list[list[int]]:
    """Pick objective hexes — small middle-band islands (settled hexes near
    the strait center), preferring hexes with land neighbors but mostly
    surrounded by water (an archipelago feel)."""
    mid_rows = list(range(rows // 3, (2 * rows) // 3))
    candidates: list[tuple[int, int, int]] = []
    for r in mid_rows:
        for c in range(cols):
            if grid[r][c] == "water":
                continue
            # count water neighbors (rough — uses Moore-style 8-neighborhood)
            water_nb = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == "water":
                        water_nb += 1
            candidates.append((water_nb, c, r))
    candidates.sort(reverse=True)
    picked = [[c, r] for _, c, r in candidates[:2]]
    return picked


def write_yaml(yaml_path: Path, terrain: list[list[str]],
               objectives: list[list[int]], units: list[dict],
               cols: int, rows: int, seed: int, name: str) -> None:
    terrain_block = "\n".join("    " + "".join(row) for row in
                              [[CHAR[t] for t in line] for line in terrain])
    units_block = "\n".join(
        f'  - {{ id: {u["id"]:<16} type: {u["type"]:<10} pos: [{u["pos"][0]:>2}, {u["pos"][1]:>2}] }}'.replace(
            f'id: {u["id"]:<16}', f'id: {u["id"]:<16},').replace(
            f'type: {u["type"]:<10}', f'type: {u["type"]:<10},')
        for u in units
    )

    obj_block = "\n".join(f"    - [{o[0]}, {o[1]}]" for o in objectives)

    text = f"""name: "{name}"
seed: {seed}
turn_limit: 30

# 20 cols x 15 rows. Pointy-top, odd-r offset.
# Terrain auto-derived from web/public/terrain.png by scripts/sample_terrain.py.
# O = water, P = open, U = urban, F = forest, M = mountain.
map:
  cols: {cols}
  rows: {rows}
  terrain: |
{terrain_block}
  objective_hexes:
{obj_block}

victory:
  capture_hold_turns: 2
  combat_power_threshold: 0.6

# Auto-placed by scripts/sample_terrain.py (deterministic per seed).
units:
{units_block}
"""
    yaml_path.write_text(text)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("img", help="terrain.png to sample")
    p.add_argument("yaml", help="scenario YAML to rewrite")
    p.add_argument("--name", default=None, help="override scenario display name")
    args = p.parse_args()
    img_path, yaml_path = Path(args.img), Path(args.yaml)
    if not img_path.exists():
        print(f"missing {img_path}", file=sys.stderr)
        return 1

    raw = yaml.safe_load(yaml_path.read_text())
    cols = int(raw["map"]["cols"])
    rows = int(raw["map"]["rows"])
    seed = int(raw.get("seed", 42))
    name = args.name or raw.get("name", "Strait N-7 Crisis")

    img = np.array(Image.open(img_path).convert("RGB"))
    grid = derive_terrain(img, cols, rows)

    print("derived terrain:")
    for line in grid:
        print("  " + "".join(CHAR[t] for t in line))

    counts: dict[str, int] = {}
    for line in grid:
        for t in line:
            counts[t] = counts.get(t, 0) + 1
    print(f"counts: {counts}")

    objectives = pick_objectives(grid, cols, rows)
    print(f"objectives: {objectives}")

    units = place_units(grid, cols, rows, seed)
    print(f"placed {len(units)} units")

    write_yaml(yaml_path, grid, objectives, units, cols, rows, seed, name)
    print(f"wrote {yaml_path} (name: {name!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
