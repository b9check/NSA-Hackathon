"""Fetch real satellite imagery and stitch into a backdrop PNG.

Source: Esri World Imagery tile service (free, no API key).
Region: Bonifacio Strait — Corsica (north), La Maddalena archipelago
(center, our objective region), Sardinia (south). Picked because:
 - Two large land masses with a strait between them (matches our scenario)
 - A real archipelago of small islands in the middle (objective hexes)
 - Mediterranean / no active conflict / instantly Google-Earth-y

The output PNG is sized to match the frontend canvas exactly (matches
HEX_SIZE / PAD constants in web/src/hex.ts), so the existing Pixi
backdrop sprite picks it up unchanged.

Imagery © Esri, Maxar, Earthstar Geographics — used under fair use for
this hackathon demo.

Usage:
    python3 scripts/fetch_satellite.py web/public/terrain.png
"""
from __future__ import annotations

import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance


# Frontend constants — keep aligned with web/src/hex.ts
HEX_SIZE = 30.0
PAD_X = 24
PAD_Y = 24

# Scenario constants — keep aligned with scenarios/strait_n7.yaml
COLS = 20
ROWS = 15

# Region selection (defaults — overridable via CLI args).
DEFAULT_LAT = 41.30
DEFAULT_LNG = 9.20
DEFAULT_ZOOM = 11        # 11 covers a typical strait; lower for wider regions.
TILE_PX = 256

# 5 wide x 3 tall = 1280 x 768, leaves room to crop to the canvas.
TILES_WIDE = 5
TILES_TALL = 3

ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
USER_AGENT = "Mozilla/5.0 (compatible; NSAHack/0.1; +hackathon-demo)"

# Cache fetched tiles between runs.
CACHE_DIR = Path("/tmp/nsa_satellite_tiles")


def canvas_size(cols: int, rows: int) -> tuple[int, int]:
    hex_w = math.sqrt(3.0) * HEX_SIZE
    width = int(math.ceil(hex_w * (cols + 0.5)) + PAD_X * 2)
    height = int(math.ceil(HEX_SIZE * 1.5 * rows + HEX_SIZE * 0.5) + PAD_Y * 2)
    return width, height


def lat_lng_to_tile(lat: float, lng: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


PER_TILE_TIMEOUT = 8       # seconds per HTTP attempt
PER_TILE_RETRIES = 2       # short — total per tile <= ~25s
MAX_PARALLEL_FETCHES = 10  # tile server tolerates this comfortably


def fetch_tile(z: int, x: int, y: int) -> Image.Image:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"esri_{z}_{x}_{y}.jpg"
    if cache_path.exists():
        return Image.open(cache_path)
    url = ESRI_TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err: Exception | None = None
    for attempt in range(PER_TILE_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=PER_TILE_TIMEOUT) as r:
                data = r.read()
            cache_path.write_bytes(data)
            return Image.open(BytesIO(data))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < PER_TILE_RETRIES:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"failed to fetch tile {z}/{x}/{y}: {last_err}")


def fetch_to(out_path: Path, lat: float, lng: float, zoom: int) -> None:
    cw, ch = canvas_size(COLS, ROWS)
    global ZOOM, TILES_WIDE, TILES_TALL
    cx, cy = lat_lng_to_tile(lat, lng, zoom)
    print(f"  canvas {cw}x{ch}, center tile z={zoom} x={cx} y={cy} "
          f"(lat={lat:.3f}, lng={lng:.3f})")
    mosaic = _fetch_mosaic_at(zoom, cx, cy)
    mw, mh = mosaic.size
    left = (mw - cw) // 2
    top = (mh - ch) // 2
    cropped = mosaic.crop((left, top, left + cw, top + ch))
    cropped = ImageEnhance.Brightness(cropped).enhance(0.92)
    cropped = ImageEnhance.Contrast(cropped).enhance(1.08)
    cropped = ImageEnhance.Color(cropped).enhance(1.10)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out_path, format="PNG", optimize=True)
    print(f"  wrote {out_path} ({cw}x{ch}, imagery © Esri / Maxar)")


def _fetch_mosaic_at(zoom: int, center_x: int, center_y: int) -> Image.Image:
    half_w = TILES_WIDE // 2
    half_h = TILES_TALL // 2
    mosaic_w = TILES_WIDE * TILE_PX
    mosaic_h = TILES_TALL * TILE_PX
    mosaic = Image.new("RGB", (mosaic_w, mosaic_h))
    coords: list[tuple[int, int, int, int]] = [
        (i, j, center_x - half_w + i, center_y - half_h + j)
        for i in range(TILES_WIDE) for j in range(TILES_TALL)
    ]
    t0 = time.monotonic()
    print(f"  fetching {len(coords)} tiles in parallel around z={zoom} "
          f"x={center_x} y={center_y}")
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as ex:
        futures = {
            ex.submit(fetch_tile, zoom, x, y): (i, j)
            for i, j, x, y in coords
        }
        done = 0
        for fut in as_completed(futures):
            i, j = futures[fut]
            tile = fut.result().convert("RGB")
            mosaic.paste(tile, (i * TILE_PX, j * TILE_PX))
            done += 1
            sys.stdout.write(".")
            sys.stdout.flush()
    print(f" {time.monotonic() - t0:.1f}s")
    return mosaic


def prewarm(lat: float, lng: float, zoom: int) -> None:
    """Touch every tile in this region's mosaic so the cache is hot.

    Runs in parallel; safe to invoke from a background thread on server
    startup. Errors are swallowed — pre-warming is best-effort.
    """
    cx, cy = lat_lng_to_tile(lat, lng, zoom)
    half_w = TILES_WIDE // 2
    half_h = TILES_TALL // 2
    coords = [
        (cx - half_w + i, cy - half_h + j)
        for i in range(TILES_WIDE) for j in range(TILES_TALL)
    ]
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as ex:
        futs = [ex.submit(fetch_tile, zoom, x, y) for x, y in coords]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception:
                pass


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Fetch real satellite imagery for the wargame backdrop.")
    p.add_argument("out", help="output PNG path (e.g. web/public/terrain.png)")
    p.add_argument("--lat", type=float, default=DEFAULT_LAT)
    p.add_argument("--lng", type=float, default=DEFAULT_LNG)
    p.add_argument("--zoom", type=int, default=DEFAULT_ZOOM)
    args = p.parse_args()
    fetch_to(Path(args.out), args.lat, args.lng, args.zoom)
    return 0


if __name__ == "__main__":
    sys.exit(main())
