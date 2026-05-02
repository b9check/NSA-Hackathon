"""Tiny FastAPI control server for the wargame demo.

Run with:
    uvicorn server.main:app --reload --port 8000

Endpoints:
    GET  /api/regions             -> list of named region presets.
    POST /api/region/{key}        -> swap to named region (re-fetch satellite,
                                    re-derive scenario, re-dump state.json).
    POST /api/region/custom       -> {lat, lng, zoom, name?} JSON body.
    GET  /api/scenario            -> current scenario name + meta.
    GET  /api/healthz             -> liveness.

Vite proxies /api/* here in dev (see web/vite.config.ts).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Make sibling modules importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_region import REGIONS  # noqa: E402
from scripts.fetch_satellite import prewarm as _prewarm_region  # noqa: E402


app = FastAPI(title="Wargame Gym Control Server")

# Vite dev runs on a different port; permit during dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SCENARIO_PATH = ROOT / "scenarios" / "strait_n7.yaml"
TERRAIN_PATH = ROOT / "web" / "public" / "terrain.png"
STATE_PATH = ROOT / "web" / "public" / "state.json"

# Serialize region swaps so two users can't collide on the asset files.
_swap_lock = asyncio.Lock()


class RegionInfo(BaseModel):
    key: str
    name: str
    lat: float
    lng: float
    zoom: int


class ScenarioInfo(BaseModel):
    name: str
    cols: int
    rows: int
    units: int
    objectives: int
    terrain_url: str
    state_url: str


class CustomRegion(BaseModel):
    lat: float
    lng: float
    zoom: int = 11
    name: str = "Custom Region"


class SwapResult(BaseModel):
    ok: bool
    name: str
    lat: float
    lng: float
    zoom: int
    duration_ms: int


@app.get("/api/healthz")
async def healthz() -> dict:
    return {"ok": True}


_prewarm_started = False


@app.on_event("startup")
async def _start_prewarm() -> None:
    """Best-effort: fire off cache warming for every preset in the background
    so the second click on any region is sub-second. Runs off-loop in a thread
    pool so server startup isn't blocked by network."""
    global _prewarm_started
    if _prewarm_started:
        return
    _prewarm_started = True
    loop = asyncio.get_running_loop()

    async def warm_one(key: str, cfg: dict) -> None:
        try:
            await loop.run_in_executor(
                None, _prewarm_region, cfg["lat"], cfg["lng"], cfg["zoom"]
            )
            print(f"  prewarmed {key:10s} -> {cfg['name']}")
        except Exception as e:  # noqa: BLE001
            print(f"  prewarm failed for {key}: {e}")

    async def warm_all() -> None:
        # Warm one region at a time so we don't slam the tile server with 80
        # concurrent connections — within each region we still parallelize.
        for key, cfg in REGIONS.items():
            await warm_one(key, cfg)

    asyncio.create_task(warm_all())


@app.get("/api/regions", response_model=list[RegionInfo])
async def list_regions() -> list[RegionInfo]:
    return [
        RegionInfo(key=k, name=v["name"], lat=v["lat"], lng=v["lng"], zoom=v["zoom"])
        for k, v in REGIONS.items()
    ]


@app.get("/api/scenario", response_model=ScenarioInfo)
async def current_scenario() -> ScenarioInfo:
    if not SCENARIO_PATH.exists():
        raise HTTPException(404, "scenario not found")
    raw = yaml.safe_load(SCENARIO_PATH.read_text())
    return ScenarioInfo(
        name=raw["name"],
        cols=int(raw["map"]["cols"]),
        rows=int(raw["map"]["rows"]),
        units=len(raw.get("units", [])),
        objectives=len(raw["map"].get("objective_hexes", [])),
        terrain_url="/terrain.png",
        state_url="/state.json",
    )


# Hard upper bound for a single swap. Tiles fetch in parallel so a fresh
# region is normally ~3-6s; cached regions are <1s. Anything over this means
# the tile server is unhappy — fail fast instead of leaving the UI hanging.
PIPELINE_TIMEOUT_S = 45.0


async def _run_pipeline(lat: float, lng: float, zoom: int, name: str) -> None:
    """Invoke the existing CLI pipeline (fetch -> sample -> dump) in a
    subprocess so we don't tie up the event loop and inherit the venv."""
    cmds = [
        [
            sys.executable,
            str(ROOT / "scripts" / "fetch_satellite.py"),
            str(TERRAIN_PATH),
            "--lat", str(lat),
            "--lng", str(lng),
            "--zoom", str(zoom),
        ],
        [
            sys.executable,
            str(ROOT / "scripts" / "sample_terrain.py"),
            str(TERRAIN_PATH),
            str(SCENARIO_PATH),
            "--name", name,
        ],
        [
            sys.executable,
            str(ROOT / "scripts" / "dump_state.py"),
            str(SCENARIO_PATH),
            str(STATE_PATH),
        ],
    ]

    async def _inner() -> None:
        for cmd in cmds:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=dict(os.environ),
            )
            try:
                out, _ = await proc.communicate()
            except asyncio.CancelledError:
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode != 0:
                raise HTTPException(
                    500,
                    f"pipeline step failed ({cmd[1].rsplit('/', 1)[-1]}):\n"
                    + out.decode(errors="replace")[-2000:],
                )

    try:
        await asyncio.wait_for(_inner(), timeout=PIPELINE_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(
            504,
            f"pipeline exceeded {PIPELINE_TIMEOUT_S:.0f}s — "
            "tile server is slow or unreachable. retry shortly.",
        )


@app.post("/api/region/custom", response_model=SwapResult)
async def swap_custom(body: CustomRegion) -> SwapResult:
    async with _swap_lock:
        t0 = time.monotonic()
        await _run_pipeline(body.lat, body.lng, body.zoom, body.name)
        return SwapResult(
            ok=True, name=body.name, lat=body.lat, lng=body.lng, zoom=body.zoom,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


@app.post("/api/region/{key}", response_model=SwapResult)
async def swap_region(key: str) -> SwapResult:
    if key not in REGIONS:
        raise HTTPException(
            404,
            f"unknown region {key!r}. options: {', '.join(REGIONS)}",
        )
    cfg = REGIONS[key]
    async with _swap_lock:
        t0 = time.monotonic()
        await _run_pipeline(cfg["lat"], cfg["lng"], cfg["zoom"], cfg["name"])
        return SwapResult(
            ok=True,
            name=cfg["name"],
            lat=cfg["lat"],
            lng=cfg["lng"],
            zoom=cfg["zoom"],
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
