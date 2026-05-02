"""Pre-render a 'satellite-style' terrain backdrop PNG from a scenario YAML.

The frontend (web/src/components/MapStage.tsx) loads the resulting image as a
Pixi sprite at the bottom of the stage and overlays the interactive hex grid
on top. The image dimensions / coordinate system match the frontend's
hex-to-pixel formula (pointy-top, odd-r offset, HEX_SIZE=30, PAD=24).

Pipeline per pixel:
  1. Inverse-map (px,py) -> (col,row) of the owning hex (vectorized).
  2. Look up base biome color + base elevation from the scenario terrain grid.
  3. Soften hex boundaries with a Gaussian blur over color and elevation.
  4. Add multi-octave value noise for organic per-region variation.
  5. Hill-shade from the elevation gradient (sun from upper-left).
  6. Snow caps above an elevation threshold.
  7. Sand / shallows blend within a coastal band of the water-mask.
  8. Mild atmospheric haze + per-channel gamma.

Usage:
    python3 scripts/render_terrain.py scenarios/strait_n7.yaml \\
        web/public/terrain.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image
from scipy.ndimage import distance_transform_edt, gaussian_filter, zoom

# These MUST match web/src/hex.ts.
HEX_SIZE = 30.0
PAD_X = 24
PAD_Y = 24

CHAR_TO_TERRAIN = {"O": "water", "P": "open", "F": "forest", "M": "mountain", "U": "urban"}

# Base biome colors (RGB 0-255). Tuned for a Google-Earth-ish look in dark UI.
TERRAIN_COLOR = {
    "water":    np.array([14, 36, 68],  dtype=np.float32),
    "open":     np.array([110, 124, 72], dtype=np.float32),
    "forest":   np.array([42, 86, 50],   dtype=np.float32),
    "mountain": np.array([110, 96, 78],  dtype=np.float32),
    "urban":    np.array([135, 132, 128],dtype=np.float32),
}

# Per-pixel base elevation (-1..1 ish). Drives hillshade + snow + atmospheric.
TERRAIN_ELEV = {
    "water":    -0.30,
    "open":     0.10,
    "forest":   0.22,
    "mountain": 0.70,
    "urban":    0.16,
}


def parse_scenario(path: Path) -> tuple[int, int, np.ndarray, int]:
    raw = yaml.safe_load(path.read_text())
    cols = int(raw["map"]["cols"])
    rows = int(raw["map"]["rows"])
    seed = int(raw.get("seed", 42))
    lines = raw["map"]["terrain"].strip("\n").splitlines()
    grid = np.empty((rows, cols), dtype=object)
    for r, line in enumerate(lines):
        for c, ch in enumerate(line):
            grid[r, c] = CHAR_TO_TERRAIN[ch]
    return cols, rows, grid, seed


def canvas_size(cols: int, rows: int) -> tuple[int, int]:
    hex_w = np.sqrt(3.0) * HEX_SIZE
    width = int(np.ceil(hex_w * (cols + 0.5)) + PAD_X * 2)
    height = int(np.ceil(HEX_SIZE * 1.5 * rows + HEX_SIZE * 0.5) + PAD_Y * 2)
    return width, height


def pixel_to_hex(px: np.ndarray, py: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized inverse: pixel coords -> approximate (col, row).

    Good enough to assign each pixel to its dominant hex; minor errors near
    corners are smoothed away by the Gaussian blur step.
    """
    py_a = py - PAD_Y
    row = np.round(py_a / (1.5 * HEX_SIZE)).astype(np.int32)
    hex_w = np.sqrt(3.0) * HEX_SIZE
    px_a = px - PAD_X
    col = np.round(px_a / hex_w - 0.5 * (row & 1)).astype(np.int32)
    return col, row


def value_noise(shape: tuple[int, int], rng: np.random.Generator,
                octaves: list[tuple[float, float]]) -> np.ndarray:
    """Multi-octave value noise via random + bicubic upsample.

    `octaves` is a list of (frequency, amplitude). Frequency in cycles/px.
    """
    h, w = shape
    out = np.zeros(shape, dtype=np.float32)
    for freq, amp in octaves:
        ch = max(4, int(np.ceil(h * freq) + 4))
        cw = max(4, int(np.ceil(w * freq) + 4))
        coarse = rng.standard_normal((ch, cw)).astype(np.float32)
        upsampled = zoom(coarse, (h / ch, w / cw), order=3)
        out += upsampled[:h, :w] * amp
    return out


def render(scenario_path: Path, out_path: Path) -> None:
    cols, rows, grid, seed = parse_scenario(scenario_path)
    W, H = canvas_size(cols, rows)
    print(f"  canvas {W} x {H}, hex grid {cols} x {rows}, seed {seed}")

    rng = np.random.default_rng(seed)

    # ---- Multi-octave noise (used everywhere) ----
    macro = value_noise((H, W), rng, [(0.005, 1.0), (0.015, 0.55), (0.04, 0.30)])
    micro = value_noise((H, W), rng, [(0.10, 0.6), (0.25, 0.3)])
    macro_n = (macro - macro.mean()) / (macro.std() + 1e-6)
    micro_n = (micro - micro.mean()) / (micro.std() + 1e-6)
    # Domain-warp field: distort coastlines so they don't look hex-shaped.
    warp_x = value_noise((H, W), rng, [(0.012, 1.0), (0.035, 0.45)])
    warp_y = value_noise((H, W), rng, [(0.012, 1.0), (0.035, 0.45)])
    warp_x = (warp_x - warp_x.mean()) / (warp_x.std() + 1e-6) * 11.0  # px
    warp_y = (warp_y - warp_y.mean()) / (warp_y.std() + 1e-6) * 11.0

    # ---- Per-pixel hex assignment using WARPED sample coordinates ----
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    sx = xx + warp_x
    sy = yy + warp_y
    col_idx, row_idx = pixel_to_hex(sx, sy)
    in_bounds_warp = (col_idx >= 0) & (col_idx < cols) & (row_idx >= 0) & (row_idx < rows)
    col_clamped = np.clip(col_idx, 0, cols - 1)
    row_clamped = np.clip(row_idx, 0, rows - 1)
    terrain_at_pixel = np.where(in_bounds_warp, grid[row_clamped, col_clamped], "water")

    # Original (unwarped) bounds — used for the panel-background mask.
    raw_col, raw_row = pixel_to_hex(xx, yy)
    in_bounds = (raw_col >= 0) & (raw_col < cols) & (raw_row >= 0) & (raw_row < rows)

    # ---- Base color + elevation maps from the warped assignment ----
    base_color = np.zeros((H, W, 3), dtype=np.float32)
    elev = np.zeros((H, W), dtype=np.float32)
    is_water = np.zeros((H, W), dtype=bool)
    for tname, rgb in TERRAIN_COLOR.items():
        m = terrain_at_pixel == tname
        base_color[m] = rgb
        elev[m] = TERRAIN_ELEV[tname]
        if tname == "water":
            is_water |= m

    # ---- Soften any remaining hex boundaries ----
    color_smooth = np.empty_like(base_color)
    for ch in range(3):
        color_smooth[..., ch] = gaussian_filter(base_color[..., ch], sigma=2.5)
    elev_smooth = gaussian_filter(elev, sigma=3.0)

    # ---- Per-pixel color variation ----
    var_strength = np.where(is_water, 3.5, 14.0)
    color = color_smooth + macro_n[..., None] * var_strength[..., None]
    color += micro_n[..., None] * np.where(is_water, 1.0, 3.5)[..., None]

    # ---- Distance fields for coast effects (after warp, so they're organic) ----
    land_mask = ~is_water
    dist_to_land = distance_transform_edt(is_water).astype(np.float32)
    dist_to_water = distance_transform_edt(land_mask).astype(np.float32)

    # Subtle shallows: only the very near-shore band, weaker tint.
    shallow_t = np.clip(1.0 - dist_to_land / 8.0, 0.0, 1.0) * is_water
    shallow_color = np.array([42, 96, 118], dtype=np.float32)
    color = color + (shallow_color - color) * shallow_t[..., None] * 0.32

    # Slim sand band, only at the immediate water boundary.
    sand_t = np.clip(1.0 - dist_to_water / 4.5, 0.0, 1.0) * land_mask
    sand_color = np.array([196, 174, 124], dtype=np.float32)
    color = color + (sand_color - color) * sand_t[..., None] * 0.30

    # Darken open ocean far from any shore.
    deep_t = np.clip(dist_to_land / 70.0, 0.0, 1.0) * is_water
    deep_color = np.array([4, 12, 30], dtype=np.float32)
    color = color + (deep_color - color) * deep_t[..., None] * 0.65

    # ---- Hillshade ----
    elev_with_noise = elev_smooth + macro_n * 0.12 + micro_n * 0.04
    elev_with_noise = gaussian_filter(elev_with_noise, sigma=1.5)
    gy, gx = np.gradient(elev_with_noise)
    sun = np.array([-0.65, -0.65, 0.45], dtype=np.float32)
    sun /= np.linalg.norm(sun)
    nx = -gx
    ny = -gy
    nz = 1.0
    norm_len = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-6
    shade = (nx * sun[0] + ny * sun[1] + nz * sun[2]) / norm_len
    shade = 0.80 + shade * 0.55
    shade = np.clip(shade, 0.55, 1.40).astype(np.float32)
    color *= shade[..., None]

    # ---- Snow caps on high peaks ----
    snow_t = np.clip((elev_with_noise - 0.58) / 0.30, 0.0, 1.0)
    snow_t *= (~is_water).astype(np.float32)
    snow_color = np.array([240, 246, 252], dtype=np.float32)
    color = color + (snow_color - color) * snow_t[..., None] * 0.80

    # ---- Subtle atmospheric haze (only on shaded slopes) ----
    haze_color = np.array([28, 42, 64], dtype=np.float32)
    haze_t = np.clip((1.0 - shade) * 0.25, 0.0, 0.14)[..., None]
    color = color + (haze_color - color) * haze_t

    # ---- Final tone curve ----
    gamma = 0.95
    color = np.clip(color, 0, 255)
    color = 255.0 * np.power(color / 255.0, gamma)

    # Padding outside the hex grid -> panel background.
    bg = np.array([10, 14, 20], dtype=np.float32)
    color = np.where(in_bounds[..., None], color, bg)

    img = Image.fromarray(np.clip(color, 0, 255).astype(np.uint8), mode="RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, optimize=True)
    print(f"  wrote {out_path} ({W}x{H})")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: render_terrain.py SCENARIO.yaml OUT.png", file=sys.stderr)
        return 2
    render(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
