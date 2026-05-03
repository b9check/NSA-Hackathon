"""Switch the demo to a different region in one command.

Pipeline: fetch satellite -> sample to derive terrain -> rewrite scenario YAML
-> dump state.json. The frontend (Vite dev server) hot-reloads automatically.

Usage:
    python3 scripts/setup_region.py REGION_NAME
    python3 scripts/setup_region.py custom --lat LAT --lng LNG \\
                                            --zoom Z --name "Display"

Curated regions: each one has BOTH significant water and land bridging
the dividing line, so neither side is forced into a single domain
(navy or land) the way pure-archipelago maps do. Galician is the
reference standard.

Available named regions (see REGIONS dict below):
    galicia     - Galician rias, NW Spain (reference)
    bosphorus   - Bosphorus & Sea of Marmara (Istanbul)
    oresund     - Øresund Crossing (Denmark / Sweden)
    brittany    - Brittany Approach, Brest peninsula (France)
    bergen      - Bergen Fjords / Hardangerfjord (Norway)
    severn      - Severn Approach / Bristol Channel (UK)
    chesapeake  - Chesapeake Bay / Hampton Roads (USA)
    trieste     - Adriatic Head / Trieste-Istria (IT/SI/HR)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REGIONS: dict[str, dict] = {
    # Reference standard: rias-style coastline carved into one continuous
    # landmass — both sides reachable by ground AND by sea.
    "galicia":    dict(lat=42.60, lng=-8.90,   zoom=10,
                       name="Galician Approach"),
    # Bosphorus + Sea of Marmara around Istanbul — narrow strait with
    # heavily built-up land each side and a wider sea south of it.
    "bosphorus":  dict(lat=41.05, lng=29.05,   zoom=10,
                       name="Bosphorus Strait"),
    # Øresund: Sjælland <-> Skåne, narrow sound, mainland on each shore.
    "oresund":    dict(lat=55.85, lng=12.85,   zoom=10,
                       name="Øresund Crossing"),
    # Brittany — Brest peninsula. Rade de Brest + Douarnenez Bay carve
    # deep water inlets into a single connected landmass. Galicia twin.
    "brittany":   dict(lat=48.30, lng=-4.40,   zoom=10,
                       name="Brittany Approach"),
    # Bergen / Hardangerfjord — Norwegian Vestlandet fjord coast. The
    # mainland threads continuously between every fjord arm.
    "bergen":     dict(lat=60.30, lng=5.30,    zoom=10,
                       name="Bergen Fjords"),
    # Severn Estuary / Bristol Channel — UK mainland north and south,
    # estuary cuts inland. Both shores ground-connected via Britain.
    "severn":     dict(lat=51.35, lng=-3.30,   zoom=9,
                       name="Severn Approach"),
    # Chesapeake Bay around Hampton Roads — large inland bay with
    # mainland Virginia on the west and the Delmarva peninsula east,
    # with the Bay Bridge-Tunnel implied as a notional land tie.
    "chesapeake": dict(lat=37.00, lng=-76.10,  zoom=10,
                       name="Chesapeake Bay"),
    # Trieste / Istria — head of the Adriatic. Italian, Slovenian and
    # Croatian mainland on three sides of a shallow gulf, all
    # land-connected through the hinterland.
    "trieste":    dict(lat=45.40, lng=13.50,   zoom=10,
                       name="Adriatic Head"),
}


def run(args: list[str]) -> None:
    print(f"$ {' '.join(args)}")
    subprocess.run(args, check=True)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("region", nargs="?", default="bonifacio",
                   help="named region (or 'custom' to use --lat/--lng)")
    p.add_argument("--lat", type=float, help="custom center latitude")
    p.add_argument("--lng", type=float, help="custom center longitude")
    p.add_argument("--zoom", type=int, default=11, help="tile zoom level")
    p.add_argument("--name", default=None, help="display name for the scenario")
    p.add_argument("--list", action="store_true", help="list named regions and exit")
    args = p.parse_args()

    if args.list:
        for k, v in REGIONS.items():
            print(f"  {k:10s}  lat={v['lat']:>+7.2f}  lng={v['lng']:>+8.2f}  "
                  f"zoom={v['zoom']:>2}  '{v['name']}'")
        return 0

    if args.region == "custom":
        if args.lat is None or args.lng is None:
            print("error: --lat and --lng required for custom region",
                  file=sys.stderr)
            return 2
        cfg = dict(lat=args.lat, lng=args.lng, zoom=args.zoom,
                   name=args.name or "Custom Region")
    else:
        if args.region not in REGIONS:
            print(f"error: unknown region {args.region!r}. "
                  f"options: {', '.join(REGIONS)}", file=sys.stderr)
            return 2
        cfg = dict(REGIONS[args.region])
        if args.name:
            cfg["name"] = args.name

    py = sys.executable
    repo = Path(__file__).resolve().parent.parent

    # 1. Fetch tiles.
    run([
        py, str(repo / "scripts" / "fetch_satellite.py"),
        str(repo / "web" / "public" / "terrain.png"),
        "--lat", str(cfg["lat"]),
        "--lng", str(cfg["lng"]),
        "--zoom", str(cfg["zoom"]),
    ])

    # 2. Sample terrain + rewrite scenario.
    run([
        py, str(repo / "scripts" / "sample_terrain.py"),
        str(repo / "web" / "public" / "terrain.png"),
        str(repo / "scenarios" / "strait_n7.yaml"),
        "--name", cfg["name"],
    ])

    # 3. Dump state.json.
    run([
        py, str(repo / "scripts" / "dump_state.py"),
        str(repo / "scenarios" / "strait_n7.yaml"),
        str(repo / "web" / "public" / "state.json"),
    ])

    print(f"\nready: {cfg['name']!r} ({cfg['lat']:.2f}, {cfg['lng']:.2f}, "
          f"z={cfg['zoom']}). Refresh the browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
