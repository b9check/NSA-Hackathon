"""Switch the demo to a different region in one command.

Pipeline: fetch satellite -> sample to derive terrain -> rewrite scenario YAML
-> dump state.json. The frontend (Vite dev server) hot-reloads automatically.

Usage:
    python3 scripts/setup_region.py REGION_NAME
    python3 scripts/setup_region.py custom --lat LAT --lng LNG \\
                                            --zoom Z --name "Display"

Available named regions (see REGIONS dict below):
    bonifacio   - Bonifacio Strait (Corsica / Sardinia, Mediterranean)
    aegean      - Cyclades archipelago, Greece
    hawaii      - Maui / Lanai / Molokai channel
    solomon     - Solomon Islands central
    faroe       - Faroe Islands, North Atlantic
    aleutian    - Central Aleutian Islands
    cook        - Cook Strait (NZ North/South islands)
    galicia     - Galician rias, NW Spain
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REGIONS: dict[str, dict] = {
    "bonifacio": dict(lat=41.30, lng=9.20,    zoom=11,
                      name="Bonifacio Strait"),
    "aegean":    dict(lat=37.00, lng=25.00,   zoom=10,
                      name="Aegean Archipelago"),
    "hawaii":    dict(lat=20.95, lng=-156.70, zoom=11,
                      name="Maui-Lanai Channel"),
    "solomon":   dict(lat=-9.50, lng=159.50,  zoom=10,
                      name="Solomon Sea"),
    "faroe":     dict(lat=62.00, lng=-7.00,   zoom=10,
                      name="Faroe Approach"),
    "aleutian":  dict(lat=52.50, lng=-174.00, zoom=9,
                      name="Aleutian Strait"),
    "cook":      dict(lat=-41.30, lng=174.50, zoom=10,
                      name="Cook Strait"),
    "galicia":   dict(lat=42.60, lng=-8.90,   zoom=10,
                      name="Galician Approach"),
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
