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
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Make sibling modules importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env at server boot so ANTHROPIC_API_KEY (and any future LLM creds)
# are available before ai.* modules import the SDK and read os.environ.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # python-dotenv optional; shell exports still work

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
# Eagerly import the AI package so controller subclasses register at server
# boot. /api/ai/controller validates against the registry at request time.
import ai.runner  # noqa: F401  E402


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
    # AI control: per-side controller kind. 'manual' = humans submit via UI;
    # any other value names a registered ai.controller (random / heuristic /
    # llm). The frontend mirrors this state.
    "controllers": {"blue": "manual", "red": "manual"},
    # Last successful AI run per side. Cached so the ReasoningPanel can
    # GET /api/ai/last_reasoning without re-rendering everything.
    "last_reasoning": {"blue": None, "red": None},
    # Per-game accumulated trace (one row per resolved turn). Fed to
    # ai/reflect.py at end-of-game. Cleared on reroll / region swap.
    "game_log": [],
    # Lessons appended in the most recent reflect call (so the UI can pop
    # them up immediately after the game ends).
    "last_lessons": [],
    # Auto-incrementing game id for traceability in the lessons store.
    "current_game_id": "game_init",
    # Placement phase: True at game start; players can drag their own
    # units to new starting hexes. Cleared by POST /api/placement/end
    # (or auto-cleared when no side is manual). While True, /api/orders
    # and /api/resolve are blocked.
    "placement_phase": True,
    # Manual-side units that haven't been dropped onto a hex yet. Each
    # entry is a UnitInstance.model_dump(); on placement_move we pop
    # one out and append it (with new col/row) into state.units. The
    # board therefore renders ONLY units that have been placed (or that
    # belong to a side an AI/random controller is driving).
    "stashed_units": {"blue": [], "red": []},
    # Original (col, row) for every unit at scenario-load time. Used
    # to restore an AI side to its default spawn hex when a controller
    # toggles back from manual to non-manual.
    "default_unit_hex": {},
}


def _snapshot_default_hexes() -> None:
    """Capture each unit's default (col, row) right after scenario load."""
    state = _ensure_state()
    _session["default_unit_hex"] = {
        u.id: (u.col, u.row) for u in state.units
    }


def _stash_side(side: str) -> None:
    """Pull every unit belonging to `side` off the board into the stash.
    Idempotent — a side already fully stashed stays stashed."""
    state = _ensure_state()
    bucket = _session.setdefault(
        "stashed_units", {"blue": [], "red": []},
    ).setdefault(side, [])
    keep = []
    for u in state.units:
        if u.side == side:
            bucket.append(u.model_dump(mode="json"))
        else:
            keep.append(u)
    state.units = keep


def _in_own_half(side: str, state: GameState, tc: int, tr: int) -> bool:
    """True iff the target hex is on `side`'s half of the map. The
    boundary is the perpendicular bisector of the segment joining the
    blue base centroid and the red base centroid — so it works for any
    orientation (north/south, east/west, diagonal). On the boundary
    line both sides are accepted (tie goes to the placer)."""
    blue_centroid: Optional[tuple[float, float]] = None
    red_centroid: Optional[tuple[float, float]] = None
    blue_pts = [(b.col, b.row) for b in (state.bases or []) if b.side == "blue"]
    red_pts = [(b.col, b.row) for b in (state.bases or []) if b.side == "red"]
    if blue_pts:
        blue_centroid = (
            sum(c for c, _ in blue_pts) / len(blue_pts),
            sum(r for _, r in blue_pts) / len(blue_pts),
        )
    if red_pts:
        red_centroid = (
            sum(c for c, _ in red_pts) / len(red_pts),
            sum(r for _, r in red_pts) / len(red_pts),
        )
    if blue_centroid is None or red_centroid is None:
        # No bases yet for one side -> no constraint we can enforce.
        return True
    bx, by = blue_centroid
    rx, ry = red_centroid
    mx, my = (bx + rx) / 2, (by + ry) / 2
    # Vector pointing from blue toward red.
    vx, vy = rx - bx, ry - by
    # Sign of (target - midpoint) . (red - blue):
    #   <= 0 -> on blue's side  (closer to blue centroid)
    #   >= 0 -> on red's side
    dot = (tc - mx) * vx + (tr - my) * vy
    if side == "blue":
        return dot <= 0.0
    return dot >= 0.0


def _exposed_hexes(state: GameState) -> set:
    """Union of every manual side's sensor footprint (bases + already-
    placed units). AI auto-deploy avoids these hexes so the AI roster
    isn't pre-revealed before turn 1."""
    from engine.hex import Hex, hex_range
    out: set = set()
    cols, rows = state.map.cols, state.map.rows
    manual_sides = {
        s for s, k in _session["controllers"].items() if k == "manual"
    }
    if not manual_sides:
        return out
    sources: list[tuple[int, int, int]] = []
    for u in state.units:
        if u.side in manual_sides and u.sensor > 0:
            sources.append((u.col, u.row, u.sensor))
    for b in (state.bases or []):
        if b.side in manual_sides:
            sources.append((b.col, b.row, max(b.sensor, 1)))
    for col, row, rng in sources:
        for h in hex_range(Hex(col, row), rng, cols, rows):
            out.add((h.col, h.row))
    return out


def _auto_deploy_side(side: str) -> None:
    """Re-place every unit on `side` (whether already on the board or
    in the stash) onto a random valid hex on its own half that isn't
    already exposed by a manual side's sensors. Used for AI/random
    sides at game start, and when a controller flips manual→AI mid
    placement."""
    import random
    from engine.state import UnitInstance

    state = _ensure_state()

    pool: list[dict] = []
    keep: list[UnitInstance] = []
    for u in state.units:
        if u.side == side:
            pool.append(u.model_dump(mode="json"))
        else:
            keep.append(u)
    state.units = keep
    bucket = _session.setdefault(
        "stashed_units", {"blue": [], "red": []},
    ).setdefault(side, [])
    pool.extend(bucket)
    bucket.clear()

    occupied: set = set()
    for u in state.units:
        occupied.add((u.col, u.row))
    for b in (state.bases or []):
        occupied.add((b.col, b.row))
    cells_by_pos = {(c.col, c.row): c for c in state.map.cells}
    exposed = _exposed_hexes(state)

    def candidates_for(d: dict, allow_exposed: bool) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for (col, row), cell in cells_by_pos.items():
            if (col, row) in occupied:
                continue
            if not _in_own_half(side, state, col, row):
                continue
            terrain = getattr(cell.terrain, "value", str(cell.terrain))
            if d["domain"] == "land" and terrain == "water":
                continue
            if d["domain"] == "sea" and terrain != "water":
                continue
            if not allow_exposed and (col, row) in exposed:
                continue
            out.append((col, row))
        return out

    for d in pool:
        cands = candidates_for(d, allow_exposed=False)
        if not cands:
            cands = candidates_for(d, allow_exposed=True)
        if not cands:
            # Last resort: keep at the scenario default position even
            # if it conflicts with the half rule. Better than nothing.
            defaults = _session.get("default_unit_hex", {})
            cands = [defaults.get(d["id"], (d["col"], d["row"]))]
        col, row = random.choice(cands)
        d["col"], d["row"] = col, row
        state.units.append(UnitInstance(**d))
        occupied.add((col, row))


def _take_from_stash(side: str, unit_id: str) -> Optional[dict]:
    """Pop one unit from the side's stash by id (or None if not there)."""
    bucket = _session.setdefault(
        "stashed_units", {"blue": [], "red": []},
    ).get(side, [])
    for i, d in enumerate(bucket):
        if d["id"] == unit_id:
            return bucket.pop(i)
    return None


def _begin_placement_phase() -> None:
    """Snapshot defaults, decide whether placement is on, stash every
    manual-side roster, and auto-deploy every AI/random side onto
    private (non-revealed) hexes on its own half. Call this right
    after a fresh scenario has been loaded into _session['state']."""
    _snapshot_default_hexes()
    _session["stashed_units"] = {"blue": [], "red": []}
    anyone_manual = any(v == "manual" for v in _session["controllers"].values())
    _session["placement_phase"] = anyone_manual
    if anyone_manual:
        for side, kind in _session["controllers"].items():
            if kind == "manual":
                _stash_side(side)
        # After stashing manual sides, the AI sides are re-deployed
        # so they (a) sit on their own half and (b) aren't already
        # in any manual base's sensor cone.
        for side, kind in _session["controllers"].items():
            if kind != "manual":
                _auto_deploy_side(side)


def _ensure_state() -> GameState:
    fresh = False
    if _session["state"] is None:
        _session["state"] = load_scenario(SCENARIO_PATH)
        fresh = True
    # Cold-start path: first time we materialize state in this process
    # AND nothing has stashed/snapshotted yet → run the standard
    # placement-phase setup so a manual-side reload sees an empty board
    # and a populated drawer instead of every unit pre-placed.
    if fresh and not _session.get("default_unit_hex"):
        _begin_placement_phase()
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
    # Don't reset controllers — those are session-level config, not per-turn.
    # Don't clear last_reasoning either; the panel keeps showing the last
    # turn's plan until the next AI run overwrites it.


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
    # If this resolve declared a winner, lessons extracted by the auto-
    # reflect pass land here so the UI can pop the lessons drawer
    # without a follow-up call.
    winner: Optional[str] = None
    win_reason: Optional[str] = None
    lessons: list[dict] = []


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
        # New scenario -> wipe per-game session, keep lessons store.
        async with _session_lock:
            _session["state"] = load_scenario(SCENARIO_PATH)
            _reset_orders()
            _session["last_events"] = []
            _session["game_log"] = []
            _session["last_lessons"] = []
            _session["last_reasoning"] = {"blue": None, "red": None}
            import uuid
            _session["current_game_id"] = "game_" + uuid.uuid4().hex[:6]
            # Fresh game: snapshot default hexes, stash manual sides.
            _begin_placement_phase()
            _persist_state()
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
            # Fresh game = fresh trace + new game id; keep the lessons store.
            _session["game_log"] = []
            _session["last_lessons"] = []
            _session["last_reasoning"] = {"blue": None, "red": None}
            import uuid
            _session["current_game_id"] = "game_" + uuid.uuid4().hex[:6]
            # Fresh game: snapshot default hexes, stash manual sides.
            _begin_placement_phase()
            _persist_state()
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
        if _session.get("placement_phase"):
            raise HTTPException(
                409, "still in placement phase — POST /api/placement/end first",
            )
        if not (_session["blue_locked"] and _session["red_locked"]):
            raise HTTPException(
                409,
                "both sides must lock orders before resolution. "
                f"blue_locked={_session['blue_locked']}, "
                f"red_locked={_session['red_locked']}",
            )
        state = _ensure_state()
        # Auto-fill HOLD for any unordered own units (low friction default).
        def _fill_holds(side: str) -> list[Order]:
            existing = list(_session[f"{side}_orders"])
            ordered_ids = {o.unit_id for o in existing}
            for u in state.units:
                if u.side == side and u.id not in ordered_ids:
                    existing.append(HoldOrder(unit_id=u.id))
            return existing
        blue_orders = _fill_holds("blue")
        red_orders = _fill_holds("red")
        turn_started = state.turn
        events: list[Event] = resolve_turn(state, blue_orders, red_orders)
        _session["last_events"] = events
        blue_score, red_score = compute_scores(state)
        # Accumulate per-turn snapshot for the post-game reflect pass.
        # Pulls in each side's last AI summary if there was one.
        last_reasoning = _session.get("last_reasoning") or {}
        _session["game_log"].append({
            "turn": turn_started,
            "blue_summary": (last_reasoning.get("blue") or {}).get("summary", ""),
            "red_summary": (last_reasoning.get("red") or {}).get("summary", ""),
            "scores": {"blue": blue_score, "red": red_score},
            "events": [e.model_dump() for e in events],
        })
        _reset_orders()
        _persist_state()
        winner_now = state.winner
        win_reason_now = state.win_reason
        gid = _session.get("current_game_id") or "game_unknown"
        # Detect natural game-end (winner just got set) so we can fire
        # the auto-reflect pass once. _session.last_lessons is cleared
        # on reroll/swap; if it's empty here AND we have a winner, this
        # is the first resolve to declare it.
        should_reflect = (
            winner_now is not None
            and not _session.get("last_lessons")
        )
        log_rows_snapshot = list(_session["game_log"]) if should_reflect else []
        final_state_snapshot = (
            state.model_dump(mode="json") if should_reflect else None
        )

    # Reflect OUTSIDE the lock — Haiku call ~2s.
    lessons_serialised: list[dict] = []
    if should_reflect:
        from ai.reflect import reflect_and_persist  # lazy import
        try:
            lessons = await reflect_and_persist(
                log_rows_snapshot, final_state_snapshot, game_id=gid,
            )
            lessons_serialised = [
                {
                    "id": l.id, "claim": l.claim, "tags": l.tags,
                    "side": l.side, "outcome": l.outcome,
                }
                for l in lessons
            ]
            async with _session_lock:
                _session["last_lessons"] = lessons_serialised
        except Exception as e:
            # Don't fail the resolve if reflection blows up.
            import logging
            logging.getLogger(__name__).exception("auto-reflect failed: %r", e)

    return ResolveResult(
        ok=True,
        turn_started_at=turn_started,
        turn_ended_at=state.turn,
        events=[e.model_dump() for e in events],
        blue_score=blue_score, red_score=red_score,
        winner=winner_now,
        win_reason=win_reason_now,
        lessons=lessons_serialised,
    )


@app.post("/api/turn/reset")
async def reset_turn() -> dict:
    """Clear queued orders + locks without advancing the turn (e.g. user
    wants to redo this round of planning)."""
    async with _session_lock:
        _reset_orders()
    return {"ok": True}


class SensorToggleReq(BaseModel):
    unit_id: str
    sensor_key: str    # SensorRef.key on the unit (e.g. "radar_3", "radar_4")
    active: bool


@app.post("/api/sensor/toggle")
async def sensor_toggle(req: SensorToggleReq) -> dict:
    """Free action — flip a unit's radar ON/OFF. Recomputes the unit's
    summary sensor range (passive max + active radars), persists the
    state.json so the next refetch reflects it. NOT a turn order; takes
    effect immediately."""
    async with _session_lock:
        state = _ensure_state()
        unit = state.unit_by_id(req.unit_id)
        if unit is None:
            raise HTTPException(404, f"unknown unit {req.unit_id!r}")
        # Find the sensor by key (passive sensors are always_on; toggling
        # them is a no-op but harmless).
        found = False
        for s in unit.sensors:
            if s.key == req.sensor_key:
                s.is_active = bool(req.active)
                found = True
                break
        if not found:
            raise HTTPException(
                404, f"unit {req.unit_id!r} has no sensor {req.sensor_key!r}",
            )
        # Recompute summary range = max range across only the currently-
        # active sensors. Frontend visibility / overlay code reads
        # unit.sensor and stays in sync.
        unit.sensor = max(
            (s.range for s in unit.sensors if s.is_active), default=0,
        )
        _persist_state()
        return {
            "ok": True,
            "unit_id": unit.id,
            "sensor_key": req.sensor_key,
            "is_active": req.active,
            "summary_sensor": unit.sensor,
        }


# ============= AI control endpoints =====================================

class ControllerSetReq(BaseModel):
    side: str        # "blue" | "red"
    kind: str        # "manual" | "random" | "heuristic" | "llm"


class ControllerSetResp(BaseModel):
    ok: bool
    controllers: dict[str, str]


@app.get("/api/ai/controllers", response_model=ControllerSetResp)
async def get_controllers() -> ControllerSetResp:
    return ControllerSetResp(ok=True, controllers=dict(_session["controllers"]))


@app.post("/api/ai/controller", response_model=ControllerSetResp)
async def set_controller(req: ControllerSetReq) -> ControllerSetResp:
    """Set the controller for one side. 'manual' = humans submit via UI."""
    if req.side not in ("blue", "red"):
        raise HTTPException(400, f"side must be blue or red, got {req.side!r}")
    # Lazy import to avoid circulars + keep import-time cheap on cold start.
    from ai.controller import known_kinds
    if req.kind not in known_kinds():
        raise HTTPException(
            400, f"unknown controller kind {req.kind!r}. "
                 f"Known: {known_kinds()}",
        )
    async with _session_lock:
        prev = _session["controllers"].get(req.side)
        _session["controllers"][req.side] = req.kind
        state = _ensure_state()
        pre_game = state.turn == 0
        if pre_game:
            if prev != "manual" and req.kind == "manual":
                # AI → manual at any pre-game point: re-open placement
                # for that side and pull its roster into the drawer.
                # If we'd auto-ended placement when both went AI, this
                # is what brings the drawer back.
                _session["placement_phase"] = True
                _stash_side(req.side)
                _persist_state()
            elif prev == "manual" and req.kind != "manual":
                # Manual → AI: drop the stashed roster onto fresh
                # private hexes on the side's own half. Only meaningful
                # while we're still placing — once the user has hit
                # READY (placement_phase=False) we leave their placed
                # units in place.
                if _session.get("placement_phase"):
                    _auto_deploy_side(req.side)
                    _persist_state()
            # If neither side is manual, there's nobody to drag units —
            # auto-end placement so AI vs AI just rolls into turn 1.
            if not any(v == "manual" for v in _session["controllers"].values()):
                _session["placement_phase"] = False
        # Mid-game (turn>=1): toggling controllers only changes who
        # plans the next move; the board is left untouched.
    return ControllerSetResp(ok=True, controllers=dict(_session["controllers"]))


class AIPlayReq(BaseModel):
    side: str        # "blue" | "red"
    # Optional override; if absent, uses the side's session-level controller.
    controller_kind: Optional[str] = None


class AIPlayResp(BaseModel):
    ok: bool
    side: str
    controller: str
    orders_count: int
    summary: str
    decisions: list[dict]
    fallback: bool = False


@app.post("/api/ai/play", response_model=AIPlayResp)
async def ai_play(req: AIPlayReq) -> AIPlayResp:
    """Run the AI controller for one side, submit + lock its orders.

    Returns the controller's reasoning (summary + per-unit decisions) so the
    UI can render it in the ReasoningPanel without a second roundtrip.
    """
    if req.side not in ("blue", "red"):
        raise HTTPException(400, f"side must be blue or red, got {req.side!r}")
    from ai.runner import play_side as ai_play_side

    async with _session_lock:
        state = _ensure_state()
        kind = req.controller_kind or _session["controllers"].get(req.side, "manual")
        if kind == "manual":
            raise HTTPException(
                400, f"side {req.side} controller is 'manual' — "
                     "humans submit via /api/orders, not /api/ai/play",
            )
        # Run the controller. Returns Pydantic Order objects + meta dict.
        orders, meta = await ai_play_side(req.side, kind, state)

        # Submit + lock through the same code path the manual UI uses.
        _session[f"{req.side}_orders"] = list(orders)
        _session[f"{req.side}_locked"] = True
        _session["last_reasoning"][req.side] = {
            "controller": kind,
            "turn": state.turn,
            "summary": meta.get("summary", ""),
            "decisions": meta.get("decisions", []),
            "fallback": bool(meta.get("fallback", False)),
        }

        return AIPlayResp(
            ok=True,
            side=req.side,
            controller=kind,
            orders_count=len(orders),
            summary=meta.get("summary", ""),
            decisions=meta.get("decisions", []),
            fallback=bool(meta.get("fallback", False)),
        )


@app.get("/api/ai/last_reasoning")
async def last_reasoning(side: str) -> dict:
    """Return the cached reasoning for the given side's last AI turn."""
    if side not in ("blue", "red"):
        raise HTTPException(400, f"side must be blue or red, got {side!r}")
    return _session["last_reasoning"].get(side) or {
        "controller": None,
        "turn": None,
        "summary": "",
        "decisions": [],
        "fallback": False,
    }


# ============= Game-end + reflection ====================================

class EndGameReq(BaseModel):
    # If true, force-end the game even if no winner yet (compute one from
    # current HP totals). Default: only end if engine already declared a
    # winner.
    force: bool = False


class EndGameResp(BaseModel):
    ok: bool
    winner: Optional[str]
    win_reason: Optional[str]
    lessons: list[dict]
    game_id: str


def _hp_total(state: GameState, side: str) -> int:
    return (
        sum(u.hp for u in state.units if u.side == side)
        + sum(b.hp for b in state.bases if b.side == side)
    )


@app.post("/api/game/end", response_model=EndGameResp)
async def end_game(req: EndGameReq) -> EndGameResp:
    """End the current match (manually or because the engine says so),
    run the reflect pass, and append lessons to memory/lessons.jsonl.

    Returns the freshly-extracted lessons so the UI can pop a "lessons
    learned" drawer the moment the game ends.
    """
    from ai.reflect import reflect_and_persist  # lazy import (Anthropic SDK)

    async with _session_lock:
        state = _ensure_state()
        # Determine winner
        winner = state.winner
        win_reason = state.win_reason
        if winner is None:
            if not req.force:
                raise HTTPException(
                    409, "game not over and force=false; pass force=true to forfeit",
                )
            blue_hp = _hp_total(state, "blue")
            red_hp = _hp_total(state, "red")
            if blue_hp > red_hp:
                winner, win_reason = "blue", "manual_forfeit"
            elif red_hp > blue_hp:
                winner, win_reason = "red", "manual_forfeit"
            else:
                winner, win_reason = "draw", "manual_forfeit"
            state.winner = winner
            state.win_reason = win_reason
            _persist_state()

        gid = _session.get("current_game_id") or "game_unknown"
        log_rows = list(_session.get("game_log") or [])
        final_state_dict = state.model_dump(mode="json")

    # Run reflection OUTSIDE the session lock — it blocks on the LLM call.
    lessons = await reflect_and_persist(
        log_rows, final_state_dict, game_id=gid,
    )
    async with _session_lock:
        _session["last_lessons"] = [
            {
                "id": l.id, "claim": l.claim, "tags": l.tags,
                "side": l.side, "outcome": l.outcome,
            }
            for l in lessons
        ]

    return EndGameResp(
        ok=True,
        winner=winner,
        win_reason=win_reason,
        lessons=_session["last_lessons"],
        game_id=gid,
    )


# ============= Placement phase =========================================

class PlacementMoveReq(BaseModel):
    unit_id: str
    target_hex: list[int]   # [col, row]


class PlacementMoveResp(BaseModel):
    ok: bool
    unit_id: str
    target_hex: list[int]


@app.get("/api/placement/state")
async def placement_state() -> dict:
    """Whether the game is currently in placement phase + which sides
    are 'manual' + the un-placed (stashed) roster per side that the
    drawer should render."""
    stashed = _session.get("stashed_units") or {"blue": [], "red": []}
    return {
        "active": bool(_session.get("placement_phase", False)),
        "controllers": dict(_session["controllers"]),
        "stashed": {
            "blue": list(stashed.get("blue", [])),
            "red": list(stashed.get("red", [])),
        },
    }


@app.post("/api/placement/move", response_model=PlacementMoveResp)
async def placement_move(req: PlacementMoveReq) -> PlacementMoveResp:
    """Drop a unit onto a target hex. Two paths:
       (1) unit is in the side's stash → pop it, set col/row, push
           it onto the board (first placement from drawer).
       (2) unit is already on the board → relocate it (re-drag on map).
    Validates: placement phase active; unit belongs to a manual side;
    target in bounds; terrain matches unit domain; hex not occupied.
    """
    async with _session_lock:
        if not _session.get("placement_phase"):
            raise HTTPException(409, "placement phase has ended")
        state = _ensure_state()
        if len(req.target_hex) != 2:
            raise HTTPException(400, "target_hex must be [col, row]")
        tc, tr = int(req.target_hex[0]), int(req.target_hex[1])
        cols, rows = state.map.cols, state.map.rows
        if not (0 <= tc < cols and 0 <= tr < rows):
            raise HTTPException(400, "target_hex out of bounds")

        # Find the unit on the board OR in the stash and figure out the
        # unit metadata we need for terrain / domain checks.
        unit = state.unit_by_id(req.unit_id)
        if unit is not None:
            side = unit.side
            domain = unit.domain
            from_stash = False
        else:
            # Look in both side stashes (we don't yet know which side).
            stashed_dict: Optional[dict] = None
            side: Optional[str] = None
            for s in ("blue", "red"):
                bucket = _session.get("stashed_units", {}).get(s, [])
                for d in bucket:
                    if d["id"] == req.unit_id:
                        stashed_dict = d
                        side = s
                        break
                if stashed_dict is not None:
                    break
            if stashed_dict is None or side is None:
                raise HTTPException(404, f"unknown unit {req.unit_id!r}")
            domain = stashed_dict["domain"]
            from_stash = True

        if _session["controllers"].get(side) != "manual":
            raise HTTPException(
                403, f"side {side!r} is not manual — only humans can place",
            )

        # Own-half constraint, derived from base centroids so it works for
        # any map orientation.
        if not _in_own_half(side, state, tc, tr):
            raise HTTPException(
                400, f"side {side} can only place on its own half of the map",
            )

        # Terrain compatibility
        cell = next(
            (c for c in state.map.cells if c.col == tc and c.row == tr), None,
        )
        if cell is None:
            raise HTTPException(400, "target hex not on map")
        terrain = getattr(cell.terrain, "value", str(cell.terrain))
        if domain == "land" and terrain == "water":
            raise HTTPException(400, "ground unit can't start on water")
        if domain == "sea" and terrain != "water":
            raise HTTPException(400, "ship can only start on water")

        # Occupancy (skip the unit-being-moved, only relevant for case 2).
        for u in state.units:
            if not from_stash and u.id == req.unit_id:
                continue
            if u.col == tc and u.row == tr:
                raise HTTPException(409, f"hex occupied by {u.id}")
        for b in (state.bases or []):
            if b.col == tc and b.row == tr:
                raise HTTPException(409, f"hex occupied by base {b.id}")

        if from_stash:
            from engine.state import UnitInstance
            d = _take_from_stash(side, req.unit_id)
            assert d is not None, "stash row vanished mid-request"
            d["col"], d["row"] = tc, tr
            state.units.append(UnitInstance(**d))
        else:
            unit.col, unit.row = tc, tr  # type: ignore[union-attr]
        _persist_state()
        return PlacementMoveResp(ok=True, unit_id=req.unit_id, target_hex=[tc, tr])


@app.post("/api/placement/end")
async def placement_end() -> dict:
    """Lock in current positions and start turn 1. Errors out if any
    manual side still has unplaced units in the stash."""
    async with _session_lock:
        if not _session.get("placement_phase"):
            return {"ok": True}
        stashed = _session.get("stashed_units") or {}
        unplaced = []
        for side, kind in _session["controllers"].items():
            if kind == "manual" and stashed.get(side):
                unplaced.append(f"{side}({len(stashed[side])})")
        if unplaced:
            raise HTTPException(
                409,
                f"unplaced units remain: {', '.join(unplaced)}",
            )
        _session["placement_phase"] = False
        _persist_state()
    return {"ok": True}


@app.get("/api/memory/lessons")
async def get_lessons() -> dict:
    """Return all lessons currently in memory.jsonl. Used by the UI's
    "memory inspector" panel and by the demo's same-seed A/B harness."""
    from ai.memory import load_lessons
    lessons = load_lessons()
    return {
        "count": len(lessons),
        "lessons": [
            {
                "id": l.id, "claim": l.claim, "tags": l.tags,
                "side": l.side, "outcome": l.outcome,
                "source_game_id": l.source_game_id,
                "source_turn": l.source_turn,
                "created_ts": l.created_ts,
            }
            for l in sorted(lessons, key=lambda x: -x.created_ts)
        ],
    }
