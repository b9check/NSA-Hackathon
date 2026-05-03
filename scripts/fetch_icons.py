"""Download a curated set of game-icons.net SVGs and save them as
white-on-transparent under web/public/icons/<platform_or_base_key>.svg.

The raw upstream SVG ships with a black 512x512 background rect that we strip
so the asset can be tinted by Pixi at runtime.

Run once at setup. Resulting files are committed to the repo.

Imagery licensed CC BY 3.0 — credit `game-icons.net` in the project credits.

Usage:
    python3 scripts/fetch_icons.py
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path


# Mapping: output filename -> upstream "<author>/<icon>.svg" path on
# https://raw.githubusercontent.com/game-icons/icons/master/
# Picked for readability at ~30-40 px on a satellite backdrop.
ICON_MAP: dict[str, str] = {
    # ---- Mobile platforms ----
    "f35a":      "delapouite/jet-fighter.svg",
    "j20":       "delapouite/jet-fighter.svg",
    "mq9":       "delapouite/delivery-drone.svg",
    "recon_uav": "delapouite/delivery-drone.svg",
    "cg47":      "delapouite/interceptor-ship.svg",
    "type055":   "delapouite/interceptor-ship.svg",
    "patriot":   "lorc/missile-pod.svg",
    "hq9":       "lorc/missile-pod.svg",
    "m1a2":      "lorc/tank.svg",
    "mech_b":    "lorc/visored-helm.svg",
    "mech_r":    "lorc/visored-helm.svg",
    "shahed":    "lorc/missile-swarm.svg",
    # ---- Bases ----
    "blue_airbase":    "delapouite/airplane-departure.svg",
    "red_airbase":     "delapouite/airplane-departure.svg",
    "blue_navalbase":  "lorc/anchor.svg",
    "red_navalbase":   "lorc/anchor.svg",
    "blue_fob":        "lorc/castle.svg",
    "red_launchsite":  "lorc/castle.svg",
}

UPSTREAM = "https://raw.githubusercontent.com/game-icons/icons/master/{}"
USER_AGENT = "Mozilla/5.0 (NSAHack icon fetcher)"


# Removes the leading background rect/path that game-icons.net adds.
# Two common forms in the upstream SVGs:
#   <path d="M0 0h512v512H0z"/>
#   <path d="M0 0h512v512H0z" fill="#000"/>  (rare)
BG_PATH_RE = re.compile(
    r'<path\s+d="M0 0h512v512H0z"\s*(?:fill="[^"]*")?\s*/>',
    re.IGNORECASE,
)


def fetch_white_svg(upstream_path: str) -> str:
    url = UPSTREAM.format(upstream_path)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
    # Strip the black background rect.
    cleaned = BG_PATH_RE.sub("", raw, count=1)
    if cleaned == raw:
        print(f"  WARNING: bg path not found in {upstream_path}", file=sys.stderr)
    return cleaned


def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "web" / "public" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)
    fails: list[str] = []
    cache: dict[str, str] = {}
    for key, upstream in ICON_MAP.items():
        if upstream not in cache:
            try:
                cache[upstream] = fetch_white_svg(upstream)
                print(f"  fetched {upstream}")
            except Exception as e:  # noqa: BLE001
                fails.append(f"{upstream}: {e}")
                continue
        out_file = out_dir / f"{key}.svg"
        out_file.write_text(cache[upstream])
        print(f"  -> {out_file.relative_to(out_dir.parent.parent.parent)}")
    if fails:
        print("\nFailures:", file=sys.stderr)
        for f in fails:
            print("  " + f, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
