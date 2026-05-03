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
        _session["controllers"][req.side] = req.kind
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
