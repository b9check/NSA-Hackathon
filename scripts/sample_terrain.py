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


# ---- Hex topology helpers (mirrors engine/hex.py at low fidelity) ----
_EVEN_NB = [(+1, 0), (-1, 0), (0, -1), (-1, -1), (0, +1), (-1, +1)]
_ODD_NB = [(+1, 0), (-1, 0), (+1, -1), (0, -1), (+1, +1), (0, +1)]


def neighbors(col: int, row: int) -> list[tuple[int, int]]:
    deltas = _ODD_NB if (row & 1) else _EVEN_NB
    return [(col + dc, row + dr) for dc, dr in deltas]


def hex_distance(c1: int, r1: int, c2: int, r2: int) -> int:
    def to_cube(c: int, r: int) -> tuple[int, int, int]:
        x = c - (r - (r & 1)) // 2
        z = r
        y = -x - z
        return x, y, z
    ax, ay, az = to_cube(c1, r1)
    bx, by, bz = to_cube(c2, r2)
    return (abs(ax - bx) + abs(ay - by) + abs(az - bz)) // 2


def is_coastal(grid, c: int, r: int, cols: int, rows: int) -> bool:
    """Land hex with at least one water neighbor (good site for AAW radar)."""
    if grid[r][c] == "water":
        return False
    for nc, nr in neighbors(c, r):
        if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == "water":
            return True
    return False


def is_deep_water(grid, c: int, r: int, cols: int, rows: int) -> bool:
    """Water hex with all 6 neighbors also water (no shore in spitting distance)."""
    if grid[r][c] != "water":
        return False
    for nc, nr in neighbors(c, r):
        if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != "water":
            return False
    return True


def place_units(grid, cols: int, rows: int, seed: int) -> list[dict]:
    """Tactically-aware auto-placement of 7 Blue + 9 Red units.

    Each unit has a stack of preferences (best-fit first, fallback later)
    so we never get stuck on regions that lack ideal terrain.
    """
    rng = random.Random(seed)
    used: set[tuple[int, int]] = set()

    def all_hexes() -> list[tuple[int, int]]:
        return [(c, r) for r in range(rows) for c in range(cols)]

    def candidates(predicate) -> list[tuple[int, int]]:
        return [(c, r) for c, r in all_hexes()
                if (c, r) not in used and predicate(c, r)]

    def pick(*preds):
        """Try each preference in order; return first that has candidates."""
        for p in preds:
            cands = candidates(p)
            if cands:
                pos = rng.choice(cands)
                used.add(pos)
                return [pos[0], pos[1]]
        return None

    def near(anchor: list[int] | None, max_dist: int):
        """Predicate factory: within `max_dist` of `anchor` if anchor exists."""
        if anchor is None:
            return lambda c, r: False
        ac, ar = anchor[0], anchor[1]
        return lambda c, r: hex_distance(c, r, ac, ar) <= max_dist

    # Side bands (more aggressive than half-split for character).
    south_band  = lambda c, r: r >= rows - max(3, rows // 4)
    north_band  = lambda c, r: r < max(3, rows // 4)
    south_half  = lambda c, r: r >= rows / 2
    north_half  = lambda c, r: r < rows / 2
    south_push  = lambda c, r: rows / 2 <= r < rows - max(3, rows // 4)
    north_push  = lambda c, r: max(3, rows // 4) <= r < rows / 2

    is_water     = lambda c, r: grid[r][c] == "water"
    is_land      = lambda c, r: grid[r][c] != "water"
    is_settled   = lambda c, r: grid[r][c] in ("urban", "open")
    is_coast     = lambda c, r: is_coastal(grid, c, r, cols, rows)
    is_deep      = lambda c, r: is_deep_water(grid, c, r, cols, rows)
    not_coastal  = lambda c, r: is_land(c, r) and not is_coast(c, r)

    def AND(*ps):
        return lambda c, r: all(p(c, r) for p in ps)

    units: list[dict] = []

    def add(uid, side, utype, pos):
        if pos is None:
            return
        units.append({"id": uid, "side": side, "type": utype, "pos": pos})

    # ============== Blue (south) ==============
    add("blue-destroyer-1", "blue", "destroyer", pick(
        AND(south_half, is_deep),
        AND(south_half, is_water),
        is_water,
    ))
    add("blue-fighter-1", "blue", "fighter", pick(
        south_band,
        south_half,
    ))
    add("blue-bomber-1", "blue", "bomber", pick(
        south_band,
        south_half,
    ))
    add("blue-scout_drone-1", "blue", "scout_drone", pick(
        south_push,
        south_half,
    ))
    add("blue-missile_launcher-1", "blue", "missile_launcher", pick(
        AND(south_half, is_coast, is_settled),
        AND(south_half, is_coast, is_land),
        AND(south_half, is_settled),
        AND(south_half, is_land),
    ))
    add("blue-armor-1", "blue", "armor", pick(
        AND(south_half, is_settled, not_coastal),
        AND(south_half, is_settled),
        AND(south_half, is_land),
    ))
    blue_inf_anchor = pick(
        AND(south_half, is_coast, is_land),
        AND(south_half, is_land),
    )
    add("blue-infantry-1", "blue", "infantry", blue_inf_anchor)
    add("blue-infantry-2", "blue", "infantry", pick(
        AND(south_half, near(blue_inf_anchor, 2), is_land),
        AND(south_half, near(blue_inf_anchor, 4), is_land),
        AND(south_half, is_land),
    ))

    # ============== Red (north) ==============
    add("red-destroyer-1", "red", "destroyer", pick(
        AND(north_half, is_deep),
        AND(north_half, is_water),
        is_water,
    ))
    add("red-fighter-1", "red", "fighter", pick(
        north_band,
        north_half,
    ))
    add("red-bomber-1", "red", "bomber", pick(
        north_band,
        north_half,
    ))
    add("red-scout_drone-1", "red", "scout_drone", pick(
        north_push,
        north_half,
    ))
    add("red-missile_launcher-1", "red", "missile_launcher", pick(
        AND(north_half, is_coast, is_settled),
        AND(north_half, is_coast, is_land),
        AND(north_half, is_settled),
        AND(north_half, is_land),
    ))
    add("red-armor-1", "red", "armor", pick(
        AND(north_half, is_settled, not_coastal),
        AND(north_half, is_settled),
        AND(north_half, is_land),
    ))
    red_inf_anchor = pick(
        AND(north_half, is_coast, is_land),
        AND(north_half, is_land),
    )
    add("red-infantry-1", "red", "infantry", red_inf_anchor)
    add("red-infantry-2", "red", "infantry", pick(
        AND(north_half, near(red_inf_anchor, 2), is_land),
        AND(north_half, near(red_inf_anchor, 4), is_land),
        AND(north_half, is_land),
    ))
    # Strike-drone pair: kamikaze, clustered in red rear.
    sd_anchor = pick(
        AND(north_band, is_land),
        north_band,
        north_half,
    )
    add("red-strike_drone-1", "red", "strike_drone", sd_anchor)
    add("red-strike_drone-2", "red", "strike_drone", pick(
        near(sd_anchor, 2),
        near(sd_anchor, 4),
        north_half,
    ))

    return [u for u in units if u["pos"] is not None]


def place_bases(grid, cols: int, rows: int, seed: int,
                placed_units: list[dict]) -> list[dict]:
    """Auto-place 3 bases per side (airbase, naval base, FOB / launch site).

    Bases are fixed installations that spawn / repair platforms. They sit on
    settled land hexes well inside their own side's territory and avoid
    overlapping unit positions. Naval base hugs the coast.
    """
    rng = random.Random(seed ^ 0x5eed_ba5e)  # deterministic but distinct stream
    used: set[tuple[int, int]] = {(u["pos"][0], u["pos"][1]) for u in placed_units}

    south_band = lambda c, r: r >= rows - max(3, rows // 4)
    north_band = lambda c, r: r < max(3, rows // 4)
    south_half = lambda c, r: r >= rows / 2
    north_half = lambda c, r: r < rows / 2

    is_water = lambda c, r: grid[r][c] == "water"
    is_land = lambda c, r: grid[r][c] != "water"
    is_settled = lambda c, r: grid[r][c] in ("urban", "open")
    is_coast = lambda c, r: is_coastal(grid, c, r, cols, rows)

    def AND(*ps):
        return lambda c, r: all(p(c, r) for p in ps)

    def pick(*preds):
        for p in preds:
            cands = [(c, r) for r in range(rows) for c in range(cols)
                     if (c, r) not in used and p(c, r)]
            if cands:
                pos = rng.choice(cands)
                used.add(pos)
                return [pos[0], pos[1]]
        return None

    bases: list[dict] = []

    def add(bid, side, pos):
        if pos is None:
            return
        bases.append({"id": bid, "side": side, "type": "base", "pos": pos})

    # One base per side (collapsed from prior 3-per-side).
    add("blue-base-1", "blue", pick(
        AND(south_band, is_settled),
        AND(south_half, is_settled),
        AND(south_half, is_land),
    ))
    add("red-base-1", "red", pick(
        AND(north_band, is_settled),
        AND(north_half, is_settled),
        AND(north_half, is_land),
    ))

    return [b for b in bases if b["pos"] is not None]


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


def _entity_block(entries: list[dict]) -> str:
    lines: list[str] = []
    for e in entries:
        c, r = e["pos"][0], e["pos"][1]
        lines.append(
            f'  - {{ id: {e["id"]:<24}, side: {e["side"]:<5}, '
            f'type: {e["type"]:<18}, pos: [{c:>2}, {r:>2}] }}'
        )
    return "\n".join(lines)


def write_yaml(yaml_path: Path, terrain: list[list[str]],
               objectives: list[list[int]], units: list[dict],
               bases: list[dict], cols: int, rows: int, seed: int,
               name: str) -> None:
    terrain_block = "\n".join("    " + "".join(row) for row in
                              [[CHAR[t] for t in line] for line in terrain])
    units_block = _entity_block(units)
    bases_block = _entity_block(bases)

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

victory:
  hp_loss_threshold: 0.25
  turn_cap: 30

# Mobile units (auto-placed by scripts/sample_terrain.py per seed).
units:
{units_block}

# One base per side.
bases:
{bases_block}
"""
    yaml_path.write_text(text)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("img", help="terrain.png to sample")
    p.add_argument("yaml", help="scenario YAML to rewrite")
    p.add_argument("--name", default=None, help="override scenario display name")
    p.add_argument("--seed", type=int, default=None,
                   help="override scenario seed (drives unit placement RNG)")
    args = p.parse_args()
    img_path, yaml_path = Path(args.img), Path(args.yaml)
    if not img_path.exists():
        print(f"missing {img_path}", file=sys.stderr)
        return 1

    raw = yaml.safe_load(yaml_path.read_text()) if yaml_path.exists() else {}
    cols = int(raw.get("map", {}).get("cols", 20))
    rows = int(raw.get("map", {}).get("rows", 15))
    seed = args.seed if args.seed is not None else int(raw.get("seed", 42))
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
    bases = place_bases(grid, cols, rows, seed, units)
    print(f"placed {len(units)} units, {len(bases)} bases")

    write_yaml(yaml_path, grid, objectives, units, bases, cols, rows, seed, name)
    print(f"wrote {yaml_path} (name: {name!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
