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
import random
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
from engine.events import Event  # noqa: E402
from engine.orders import (  # noqa: E402
    HoldOrder, MoveOrder, Order,
    OverwatchOrder, ScoutOrder, StrikeOrder,
)
from engine.resolve import compute_scores, resolve_turn  # noqa: E402
from engine.scenario import load_scenario  # noqa: E402
from engine.state import GameState  # noqa: E402


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

# Serialize region swaps + resolves so two users can't collide on the asset
# files or the in-memory session.
_swap_lock = asyncio.Lock()
_session_lock = asyncio.Lock()


# ----- In-memory turn session ----------------------------------------
# We keep a live GameState in memory so resolve_turn() can mutate it. After
# every state-changing op (resolve, region swap) we write back to
# web/public/state.json so the frontend's static-file cache stays in sync.
_session: dict = {
    "state": None,            # GameState | None
    "blue_orders": [],        # list[Order]
    "red_orders": [],
    "blue_locked": False,
    "red_locked": False,
    "last_events": [],        # list[Event] from the last resolve
}


def _ensure_state() -> GameState:
    if _session["state"] is None:
        _session["state"] = load_scenario(SCENARIO_PATH)
    return _session["state"]


def _persist_state() -> None:
    state = _session["state"]
    if state is None:
        return
    import json
    STATE_PATH.write_text(json.dumps(state.model_dump(mode="json"), indent=2))


def _reset_orders() -> None:
    _session["blue_orders"] = []
    _session["red_orders"] = []
    _session["blue_locked"] = False
    _session["red_locked"] = False


class RegionInfo(BaseModel):
    key: str
    name: str
    lat: float
    lng: float
    zoom: int


class ScenarioInfo(BaseModel):
    name: str
    seed: int
    cols: int
    rows: int
    units: int
    terrain_url: str
    state_url: str


class CustomRegion(BaseModel):
    lat: float
    lng: float
    zoom: int = 11
    name: str = "Custom Region"


class OrdersBody(BaseModel):
    side: str            # "blue" | "red"
    orders: list[dict]   # untyped: re-validated as Order discriminated union below
    lock: bool = True


class OrdersResult(BaseModel):
    ok: bool
    side: str
    count: int
    blue_locked: bool
    red_locked: bool


class TurnState(BaseModel):
    turn: int
    blue_score: float
    red_score: float
    blue_locked: bool
    red_locked: bool
    blue_orders_count: int
    red_orders_count: int
    pending_units: dict   # {"blue": [unit_id...], "red": [...]}


class ResolveResult(BaseModel):
    ok: bool
    turn_started_at: int
    turn_ended_at: int
    events: list[dict]
    blue_score: float
    red_score: float


class SwapResult(BaseModel):
    ok: bool
    name: str
    seed: int
    lat: float
    lng: float
    zoom: int
    duration_ms: int


class RerollResult(BaseModel):
    ok: bool
    name: str
    seed: int
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
        seed=int(raw.get("seed", 42)),
        cols=int(raw["map"]["cols"]),
        rows=int(raw["map"]["rows"]),
        units=len(raw.get("units", [])),
        terrain_url="/terrain.png",
        state_url="/state.json",
    )


# Hard upper bound for a single swap. Tiles fetch in parallel so a fresh
# region is normally ~3-6s; cached regions are <1s. Anything over this means
# the tile server is unhappy — fail fast instead of leaving the UI hanging.
PIPELINE_TIMEOUT_S = 45.0


async def _run_pipeline(lat: float, lng: float, zoom: int, name: str,
                        seed: int, *, fetch: bool = True) -> None:
    """Invoke the existing CLI pipeline as subprocesses (so the venv is
    inherited and the event loop stays free).

    fetch=False skips the (slow) tile-fetch step — used by /api/reroll
    when only the unit placements should change.
    """
    cmds: list[list[str]] = []
    if fetch:
        cmds.append([
            sys.executable,
            str(ROOT / "scripts" / "fetch_satellite.py"),
            str(TERRAIN_PATH),
            "--lat", str(lat),
            "--lng", str(lng),
            "--zoom", str(zoom),
        ])
    cmds.append([
        sys.executable,
        str(ROOT / "scripts" / "sample_terrain.py"),
        str(TERRAIN_PATH),
        str(SCENARIO_PATH),
        "--name", name,
        "--seed", str(seed),
    ])
    cmds.append([
        sys.executable,
        str(ROOT / "scripts" / "dump_state.py"),
        str(SCENARIO_PATH),
        str(STATE_PATH),
    ])

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


def _fresh_seed() -> int:
    return random.randint(1, 999_999)


@app.post("/api/region/custom", response_model=SwapResult)
async def swap_custom(body: CustomRegion) -> SwapResult:
    async with _swap_lock:
        t0 = time.monotonic()
        seed = _fresh_seed()
        await _run_pipeline(body.lat, body.lng, body.zoom, body.name, seed)
        return SwapResult(
            ok=True, name=body.name, seed=seed,
            lat=body.lat, lng=body.lng, zoom=body.zoom,
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
        seed = _fresh_seed()
        await _run_pipeline(cfg["lat"], cfg["lng"], cfg["zoom"], cfg["name"], seed)
        # New scenario -> wipe session.
        async with _session_lock:
            _session["state"] = load_scenario(SCENARIO_PATH)
            _reset_orders()
            _session["last_events"] = []
        return SwapResult(
            ok=True,
            name=cfg["name"],
            seed=seed,
            lat=cfg["lat"],
            lng=cfg["lng"],
            zoom=cfg["zoom"],
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


@app.post("/api/reroll", response_model=RerollResult)
async def reroll() -> RerollResult:
    """Re-roll unit placements using a fresh seed. Keeps the current terrain
    (no tile fetch). Sub-second."""
    if not SCENARIO_PATH.exists():
        raise HTTPException(404, "no scenario loaded yet")
    raw = yaml.safe_load(SCENARIO_PATH.read_text())
    name = raw.get("name", "Custom")
    async with _swap_lock:
        t0 = time.monotonic()
        seed = _fresh_seed()
        # lat/lng/zoom are unused when fetch=False; pass dummies.
        await _run_pipeline(0.0, 0.0, 0, name, seed, fetch=False)
        async with _session_lock:
            _session["state"] = load_scenario(SCENARIO_PATH)
            _reset_orders()
            _session["last_events"] = []
        return RerollResult(
            ok=True, name=name, seed=seed,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


# ============= Turn-flow endpoints =================================

_ORDER_CTORS = {
    "MOVE": MoveOrder,
    "STRIKE": StrikeOrder,
    "SCOUT": ScoutOrder,
    "OVERWATCH": OverwatchOrder,
    "HOLD": HoldOrder,
}


def _parse_orders(raw_orders: list[dict]) -> list[Order]:
    out: list[Order] = []
    for o in raw_orders:
        kind = o.get("kind")
        ctor = _ORDER_CTORS.get(kind or "")
        if ctor is None:
            raise HTTPException(400, f"unknown order kind {kind!r}")
        try:
            out.append(ctor(**o))  # pydantic validates required fields
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"invalid order {o}: {e}")
    return out


@app.get("/api/turn", response_model=TurnState)
async def get_turn_state() -> TurnState:
    state = _ensure_state()
    blue_score, red_score = compute_scores(state)
    blue_pending = [
        u.id for u in state.units if u.side == "blue"
        and u.id not in {o.unit_id for o in _session["blue_orders"]}
    ]
    red_pending = [
        u.id for u in state.units if u.side == "red"
        and u.id not in {o.unit_id for o in _session["red_orders"]}
    ]
    return TurnState(
        turn=state.turn,
        blue_score=blue_score, red_score=red_score,
        blue_locked=_session["blue_locked"],
        red_locked=_session["red_locked"],
        blue_orders_count=len(_session["blue_orders"]),
        red_orders_count=len(_session["red_orders"]),
        pending_units={"blue": blue_pending, "red": red_pending},
    )


@app.post("/api/orders", response_model=OrdersResult)
async def submit_orders(body: OrdersBody) -> OrdersResult:
    if body.side not in ("blue", "red"):
        raise HTTPException(400, "side must be 'blue' or 'red'")
    state = _ensure_state()
    parsed = _parse_orders(body.orders)
    # validate ids
    own_ids = {u.id for u in state.units if u.side == body.side}
    for o in parsed:
        if o.unit_id not in own_ids:
            raise HTTPException(400, f"unit {o.unit_id!r} not on side {body.side!r}")
    async with _session_lock:
        _session[f"{body.side}_orders"] = parsed
        if body.lock:
            _session[f"{body.side}_locked"] = True
        else:
            _session[f"{body.side}_locked"] = False
        return OrdersResult(
            ok=True, side=body.side, count=len(parsed),
            blue_locked=_session["blue_locked"],
            red_locked=_session["red_locked"],
        )


@app.post("/api/resolve", response_model=ResolveResult)
async def resolve_endpoint() -> ResolveResult:
    async with _session_lock:
        if not (_session["blue_locked"] and _session["red_locked"]):
            raise HTTPException(
                409,
                "both sides must lock orders before resolution. "
                f"blue_locked={_session['blue_locked']}, "
                f"red_locked={_session['red_locked']}",
            )
        state = _ensure_state()
        # Auto-fill defensive default for any unordered own units. Units with
        # weapons + ammo + a credible contact in range go to OVERWATCH;
        # otherwise HOLD. (Self-defense doesn't require a mission.)
        from engine.missions import default_unmissioned_order
        def _fill_defaults(side: str) -> list[Order]:
            existing = list(_session[f"{side}_orders"])
            ordered_ids = {o.unit_id for o in existing}
            for u in state.units:
                if u.side == side and u.id not in ordered_ids:
                    existing.append(default_unmissioned_order(u, state))
            return existing
        blue_orders = _fill_defaults("blue")
        red_orders = _fill_defaults("red")
        turn_started = state.turn
        events: list[Event] = resolve_turn(state, blue_orders, red_orders)
        _session["last_events"] = events
        _reset_orders()
        _persist_state()
        blue_score, red_score = compute_scores(state)
        return ResolveResult(
            ok=True,
            turn_started_at=turn_started,
            turn_ended_at=state.turn,
            events=[e.model_dump() for e in events],
            blue_score=blue_score, red_score=red_score,
        )


class MissionBody(BaseModel):
    unit_id: str
    target_hex: tuple[int, int]
    roe: str = "engage"
    radar_state: str = "auto"
    halt_on_contact: bool = True
    halt_on_low_hp: bool = True
    halt_on_no_ammo: bool = True
    max_turns: int = 8
    intent: str = ""


@app.post("/api/mission")
async def set_mission(body: MissionBody) -> dict:
    """Create or replace a unit's standing mission."""
    from engine.state import Mission
    state = _ensure_state()
    if not any(u.id == body.unit_id for u in state.units):
        raise HTTPException(404, f"unit {body.unit_id} not found")
    if body.roe not in ("engage", "surveil", "avoid"):
        raise HTTPException(400, "roe must be engage|surveil|avoid")
    state.missions[body.unit_id] = Mission(
        unit_id=body.unit_id,
        target_hex=tuple(body.target_hex),
        roe=body.roe,
        radar_state=body.radar_state,
        halt_on_contact=body.halt_on_contact,
        halt_on_low_hp=body.halt_on_low_hp,
        halt_on_no_ammo=body.halt_on_no_ammo,
        max_turns=body.max_turns,
        intent=body.intent,
    )
    _persist_state()
    return {"ok": True, "unit_id": body.unit_id}


@app.delete("/api/mission/{unit_id}")
async def clear_mission(unit_id: str) -> dict:
    """Drop a unit's standing mission."""
    state = _ensure_state()
    state.missions.pop(unit_id, None)
    _persist_state()
    return {"ok": True, "unit_id": unit_id}


class RunBody(BaseModel):
    max_turns: int = 8


@app.post("/api/run")
async def run_until_halt(body: RunBody) -> dict:
    """Auto-resolve up to `max_turns` turns from current missions. Stops at
    the first turn any unit hits a halt condition (or game ends).

    Returns the concatenated event log + the halt reasons that fired.
    """
    from engine.missions import (
        evaluate_halts, generate_orders_from_missions, snapshot_contact_ids,
        default_unmissioned_order,
    )
    async with _session_lock:
        state = _ensure_state()
        if not state.missions:
            raise HTTPException(400, "no missions set")

        snapshot_contacts = snapshot_contact_ids(state)
        started_at_target = {
            m.unit_id for m in state.missions.values()
            if any(u.id == m.unit_id and (u.col, u.row) == tuple(m.target_hex)
                   for u in state.units)
        }

        all_events: list = []
        halts: list[dict] = []
        turn_started = state.turn

        max_iters = max(1, min(body.max_turns, 12))
        for i in range(max_iters):
            # Generate orders from missions; auto-fill HOLD for units without one.
            blue_orders = generate_orders_from_missions(state, "blue")
            red_orders = generate_orders_from_missions(state, "red")
            ordered_blue = {o.unit_id for o in blue_orders}
            ordered_red = {o.unit_id for o in red_orders}
            for u in state.units:
                if u.side == "blue" and u.id not in ordered_blue:
                    blue_orders.append(default_unmissioned_order(u, state))
                if u.side == "red" and u.id not in ordered_red:
                    red_orders.append(default_unmissioned_order(u, state))

            events = resolve_turn(state, blue_orders, red_orders)
            all_events.extend([e.model_dump() for e in events])

            if state.winner:
                halts.append({"unit_id": "*", "reason": "game_over"})
                break

            fired = evaluate_halts(state, snapshot_contacts, started_at_target, i + 1)
            if fired:
                halts.extend({"unit_id": uid, "reason": r} for uid, r in fired)
                break

        _reset_orders()
        _persist_state()
        blue_score, red_score = compute_scores(state)
        return {
            "ok": True,
            "turn_started_at": turn_started,
            "turn_ended_at": state.turn,
            "turns_run": state.turn - turn_started,
            "events": all_events,
            "halts": halts,
            "blue_score": blue_score,
            "red_score": red_score,
        }


@app.post("/api/turn/reset")
async def reset_turn() -> dict:
    """Clear queued orders + locks without advancing the turn (e.g. user
    wants to redo this round of planning)."""
    async with _session_lock:
        _reset_orders()
    return {"ok": True}


class SensorToggleBody(BaseModel):
    unit_id: str
    sensor_key: str = ""   # empty = toggle all togglable (radar) sensors
    active: bool


@app.post("/api/sensor/toggle")
async def toggle_sensor(body: SensorToggleBody) -> dict:
    """Immediately set is_active on a unit's radar sensor(s). Free action,
    not gated by turn submission. Refreshes the unit's `sensor` summary so
    visibility is recomputed without needing a resolve."""
    state = _ensure_state()
    unit = next((u for u in state.units if u.id == body.unit_id), None)
    if not unit:
        raise HTTPException(404, f"unit {body.unit_id} not found")
    touched = 0
    for s in unit.sensors:
        if s.modality != "radar":
            continue   # only radars are togglable
        if body.sensor_key and s.key != body.sensor_key:
            continue
        s.is_active = bool(body.active)
        touched += 1
    if touched == 0:
        raise HTTPException(400, "no togglable radar sensor matched")
    # Recompute summary range from active sensors only — own sensor coverage
    # ring updates immediately so the player can plan. The fused intel picture
    # (contacts) is intentionally NOT refreshed here: that only happens on
    # turn resolution, so radar can't be flipped on/off as a free reconnaissance.
    # Coverage ring excludes SIGINT — SIGINT only catches emitters, doesn't light up area.
    unit.sensor = max(
        (s.range for s in unit.sensors if s.is_active and s.modality != "sigint"),
        default=0,
    )
    _persist_state()
    return {"ok": True, "unit_id": unit.id, "sensor_summary_range": unit.sensor}
