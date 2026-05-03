# Integration Plan — Wargame × Sensor Pipeline

Working document. Revise freely.

Owner: Alex
Status: **proposal / pre-flight**, not yet executing
Last synthesis: from 8 parallel deep-dive agents covering architecture,
spatial bridge, time alignment, data model, visualization, backend,
class taxonomy, migration roadmap, and risk audit.

---

## TL;DR

> **Sidecar library + persistent FusionEngine + feature flag.** One process.
> Hex stays the engine's primary coordinate; km is derived. Engine truth
> stays deterministic via blake2b; fusion is the *intel layer* shown to
> the player, **not** authoritative for combat resolution. Replays are
> deterministic by caching per-turn track snapshots.

---

## Architecture

```
                  ┌─────────────────────────────────────────┐
                  │ server/main.py  (one FastAPI on :8000)  │
                  └─────────────────┬───────────────────────┘
                                    │
                  ┌─────────────────▼───────────────────────┐
                  │ GameSession                             │
                  │   state: GameState  (truth, hex)        │
                  │   blue_orders, red_orders               │
                  │   fusion: {blue: FusionEngine,          │
                  │            red:  FusionEngine}          │
                  │   sensors: {blue: [...], red: [...]}    │
                  └────────┬───────────────────────┬────────┘
                           │                       │
                           ▼                       ▼
                  ┌─────────────────┐   ┌──────────────────────┐
                  │ engine/         │   │ engine/intel/        │
                  │   resolve.py    │──▶│   bridge.py          │  (only file
                  │   (turn-loop,   │   │     hex⇄km           │   that knows
                  │    blake2b)     │   │     UnitInstance→    │   about both)
                  │                 │   │       Target         │
                  │   _is_visible() │   │   observations.py    │
                  │   reads tracks  │◀──│     (was sensors.py) │
                  │   when flag on  │   │   fusion.py          │
                  │                 │   │     (Brian's lib,    │
                  │                 │   │      no edits)       │
                  └─────────────────┘   └──────────────────────┘

          DELETE: sensor_pipeline/backend/server.py
          DELETE: sensor_pipeline/frontend/  (port glyphs into Pixi)
```

### Why these choices

- **One process** — at <24h to demo, two-process beats nothing for
  isolation but loses everything else (port collisions, two CORS configs,
  two state stores diverging, two log streams). Sidecar library import
  is the smallest viable form.
- **Per-side engines** — `blue_engine` and `red_engine` see different
  observations (their own sensors only). Sharing one engine breaks fog
  of war.
- **Engine truth stays authoritative for combat** — replays are bit-
  identical via blake2b. If fusion drove `_is_visible` for STRIKE
  legality, every replay would re-roll observation noise and diverge.
  Fusion is **what the player sees**, not what the engine resolves.

---

## Decisions

### Locked-in (confidence: high consensus across all agents)

| # | Decision | Recommendation | Cost-if-wrong |
|---|---|---|---|
| 1 | One process or two? | **One** (library import) | Hard — module paths leak |
| 2 | Where does fusion code live? | **`engine/intel/`** sub-package; rename `engine/catalog/sensors.py` → `sensor_specs.py` to kill the naming collision | Medium |
| 3 | HEX_SIZE_KM | **5 km/hex** (override per region in YAML when we have lat/lng) | Low — adapter file |
| 4 | Tracks persist across turns? | **Yes** — reset only on region swap / reroll | Low |
| 5 | Per-side or shared engines? | **Per-side** (`blue_engine`, `red_engine`) | High — fog dies if shared |
| 6 | When does fusion run? | **Synchronously inside `resolve_turn`**, response carries a `fusion_timeline: list[FusionFrame]` | Low |
| 7 | Ticks per turn | **2-6**: pre-snapshot, per-mover waypoint (capped at 4), one strike-emission pulse if any STRIKE, post-snapshot. `dt=1.0` only on the pre-tick; rest are `dt≈0` | Medium |
| 8 | Replay determinism | **Cache the resolved track snapshots per turn**; don't reseed Brian's RNG. Fusion is non-deterministic at runtime, replays are bit-identical from the cache | High — affects replay export |
| 9 | Resolver authority | **Engine stays hex-native and deterministic.** `_is_visible` only consults fusion when `WARGAME_FUSION=on`; fallback is per-unit hex sensor | Low |
| 10 | LKP ghosts | **Delete** when fusion is on — fusion's `cycles_since_last_seen` + decay is exactly what LKP was hand-rolling | Low |
| 11 | Visualization | **Single Pixi canvas**. New `trackLayer` above units. Side views: enemies become ellipses + class glyphs (port Brian's glyph alphabet). Inspect panel re-implemented in right rail | Medium — Pixi work |
| 12 | OMNI view | **Truth by default + a `+FUSION` checkbox** that overlays both sides' tracks faintly (the killer demo screenshot is truth ↔ ellipse divergence over turns) | Low |

### Open (need a call)

- **Class taxonomy depth** — we need to add `SHIP` to Brian's
  `Classification` enum. Two further candidates: `LOITERING_MUNITION`
  (Shahed) and `BASE` (vs reusing RADAR_STATION / COMMAND_POST).
  **Lean: SHIP yes, others defer to post-hackathon.**
- **Tactical-category enum** — Brian has 4 (Air, Fixed Site, C2, Mobile
  Ground). Adding SHIP requires `Naval` as a 5th category.
- **OMNI default** — TRUTH-only, BOTH overlay, or TRACKS-only?
  **Lean: TRUTH default + `+FUSION` overlay toggle.**
- **Stealth → RCS or → range scaling at the bridge?**
  **Lean: scale `sensor.max_range_km * 0.5` for stealth targets.**
- **Terrain LOS** — fusion is intentionally LOS-blind. Apply terrain
  blocking at the bridge before generating observations? Tag each
  sensor class with `requires_los: bool`?
  **Lean: yes, gate at bridge with `requires_los`. Air sensors ignore.**

---

## Data model

### UnitInstance (additions, denormalized from Platform defaults)

```
class UnitInstance(BaseModel):
    # ... existing fields
    classification:    str               # one of the 8 abstract types
    emission_type:     EmissionType | None
    is_emitting:       bool              # flips per-turn based on orders
    true_rcs:          float | None      # sticky after first sample
```

`is_emitting` rules-of-thumb (post-resolve):
- SAM batteries / SAM-equipped bases: SEARCH_RADAR baseline; FIRE_CONTROL while firing
- Destroyers / DDG-class ships: SEARCH_RADAR while alive
- AEW / scout drones / recon UAV: SEARCH_RADAR
- Stealth fighters: emitting only on STRIKE turns (FIRE_CONTROL pulse)
- Infantry / armor: nothing
- Strike drones / Shahed: nothing

### GameState (additions)

```
class GameState(BaseModel):
    # ... existing fields
    tracks_by_side:  dict[Side, list[TrackDict]]   # populated by bridge
    objective_points: ...   # already there
```

`tracks_by_side` is plain dicts via `Track.to_dict()` — never import the
`Track` dataclass into the engine. Keep dependency one-way.

### `Track.to_dict()` is the wire format

Documented at length in `SENSOR_PIPELINE_HANDOFF_FOR_ALEX.md`. Key
fields the engine + UI consume:

- `track_id`, `position {x,y}` (km), `velocity {vx,vy}`
- `position_uncertainty {ellipse_major_km, ellipse_minor_km, orientation_deg}`
- `track_confidence`, `currently_detected`, `cycles_since_last_seen`
- `class_probs_conditional` (P(class | track real))
- `class_joint_probs` (× track_confidence — what UI label shows)
- `category_probs_conditional`, `leading_class`, `leading_category`
- `dominant_emission_type`, `contributing_sensor_ids`

---

## Migration plan

5 milestones. Single feature branch: `feat/fusion-integration` off
main. Small PRs per milestone. Env flag `WARGAME_FUSION=off|shadow|on`
so rollback = restart server.

```
M0  verify build green (now)                            0.5h   flag default off
M1  cold-import smoke (just import fusion at startup)   1.5h   catches every seam-bug
M2  shadow mode: bridge runs, /api/tracks live,         4 h    no behavior change yet
    UI ignores it
M3  fusion drives _is_visible behind WARGAME_FUSION=on  6 h    ← DEMO TARGET
M4  ellipse + class-prob overlay in Pixi (polish)       4 h    optional
M5+ LLM agent on fusion belief, multi-game, replay,     post-hackathon
    catalog→8-types, advanced stealth, etc.
```

### M0 — Verify build green
- Both `make dev` and `python3 scripts/test_combat.py` pass.
- `.gitignore` excludes Brian's `__pycache__`, `frontend/node_modules`,
  `server_*.log`.

### M1 — Cold-import smoke
- Add `try: from sensor_pipeline.backend import fusion, sensors;
  FUSION_AVAILABLE = True except Exception: FUSION_AVAILABLE = False`
  to `server/main.py`. Log it on startup.
- Resolve any path / `__init__.py` issues. Brian uses bare
  `from sensors import …` in `fusion.py` — fix to `from .sensors`.
- Merge numpy/scipy from his `requirements.txt` into ours (already there).

**Catches**: every import / package-path / version-skew bug at the seam,
without changing any behavior.

### M2 — Shadow mode
- New `engine/intel/bridge.py` with `hex_to_km`, `km_to_hex`,
  `game_state_to_targets(state, observer_side)`.
- New `engine/intel/coords.py` with `HEX_SIZE_KM = 5` constant.
- New `engine/intel/mapping.py` with `platform_to_classification(unit)`
  and `default_emission(unit, has_strike_order: bool)`.
- After `resolve_turn`, run a fusion cycle in shadow mode and stash
  the track list on `_session["last_tracks"]`.
- Add `GET /api/tracks?side=blue|red` endpoint.

**No behavior change yet.** UI ignores tracks. Validates the bridge.

### M3 — Fusion-driven fog (DEMO TARGET)
- `_is_visible(side, entity, state)` in `engine/resolve.py` branches on
  the env flag. When `on`, looks up tracks from
  `engines[side].get_active_tracks()`; visible iff there's a track
  within `2 × HEX_SIZE_KM` AND `track_confidence ≥ 0.4`.
- Frontend `MapStage.tsx::computeVisibleHexes` reads tracks from
  `tracks_by_side`.
- Delete the `lastSeen` LKP map (`MapStage.tsx:151+`) — fusion
  produces this naturally.

**This is the optimum demo.** Player sees decay-over-time tracks,
classification labels, uncertainty in fog, mystery strikes from
unobserved attackers.

### M4 — Visual polish (optional)
- New `trackLayer` Container in Pixi above the unit layer.
- Render covariance ellipses (stroke + 12% fill, side-color),
  class glyph at center, confidence as stroke alpha.
- Inspect panel in right rail: SELECTED UNIT (truth) | SELECTED TRACK
  (belief) — swap on selection.
- Sensor-modality badge row under each track (radar=blue, EO=green,
  etc).
- Mystery-strike defender variant: incoming damage with no tracer
  origin if defender's fusion didn't track the attacker.

### M5+ — Roadmap
- LLM agent driver consuming the fused belief (`track_confidence`,
  `class_probs_conditional`, `position_uncertainty`)
- Multi-game / multi-session (`_sessions[game_id]`)
- Replay export with fused track timeline
- Fusion gate tuning UI in a debug panel
- Reflection-loop learning
- Refactor 12 named platforms → 8 abstract types from
  `engine/unit_types.json`
- True target motion + radar range-rate
- Stealth-aware sensor curve (vs current ½-range hack)

---

## Questions for Brian (need sign-off before M2)

These cannot be safely done without him:

1. **Add `SHIP` to `Classification` enum.** Also extend
   `RCS_PROFILES[SHIP] = (math.log(3000.0), 0.6)`, `EMITTER_LIBRARY`
   rows, `TACTICAL_CATEGORY_BY_CLASS[SHIP] = "Naval"`,
   `MOBILITY_MODEL_BY_CATEGORY["Naval"] = {"position_q_km2": 8.0,
   "velocity_q_km2": 0.30, "confidence_decay": 0.030}`. Without this,
   Aegis CG / Type 055 misclassify and the naval scenario pitch breaks.

2. **Stealth model.** Engine treats stealth as ½-effective-sensor-range.
   We'll multiply `target.true_rcs * 0.5` and `sensor.max_range_km *
   stealth_factor` at the bridge boundary. Confirm OK or propose a
   different shape.

3. **`dt` floor in `process_observations`.** Currently `dt = max(1.0,
   ...)`. Our sub-tick scheme passes near-zero dt for waypoint /
   strike-emission ticks. Either lower the floor to 0.0 or expose a
   `predict=False` flag on the call.

4. **`SimulationState.reset()` sufficiency.** Confirm `FusionEngine =
   FusionEngine()` is enough to wipe his belief on region swap, or
   there's session-scoped state we'd miss.

5. **`Track.to_dict()` schema stability.** We'll consume it as the LLM
   agent prompt context. Confirm Brian won't change keys without notice.
   If unstable, we add a Pydantic adapter on our side.

---

## Risk register

| Risk | Sev × Lik | Mitigation |
|---|---|---|
| Engine determinism contamination from `random.gauss` | High × High | Snapshot/restore caching strategy (decision #8) |
| Bare `from sensors import …` won't load from package path | High × High | M1 cold-import smoke catches it; fix with `from .sensors` |
| No SHIP class → ships misclassify as RADAR_STATION | High × Cert | Brian Q1; if unreachable, demo with the gap and flag it |
| Manual game regression — fusion crash kills the demo we already have | Crit × Med | Feature flag default off; fallback to per-unit hex visibility |
| LKP ghosts + fusion tracks render as double contacts | Med × High | Delete LKP path when flag on (decision #10) |
| Magic-number drift — Brian's per-cycle constants tuned for 1.5s WS, our turns are seconds-to-minutes | Med × High | Run N synthetic cycles per turn (decision #7) |
| Truth leak in Brian's `/api/scenario` (returns ground truth) | High × High (if mounted) | Don't mount his router. Library-only import. |
| State ownership across resets — fusion engine carries stale tracks after region swap | High × Cert | Reset both `state` AND `fusion` on swap/reroll |
| Coord-system mismatch | High × Cert | `engine/intel/bridge.py` is the only file that knows both; round-trip tested |
| Covariance non-PD edge case (bearing-only collinear SIGINTs) | Med × Med | Wrap fusion call in try/except; on failure fall back |

---

## Effort estimate

- **Best case** (everything bridges cleanly): **6h** to M3
- **Likely** (a couple snags — RNG state, NaN cov, hex-km drift): **12-14h** to M3
- **Worst case** (Brian's per-cycle constants don't survive turn-paced
  cadence; full retune of decay/stale needed): **30+h**

> If estimated worst case > 24h with < 24h to demo: **don't integrate
> for the demo**. Run Brian's server on `:8001`, add a "SENSOR FUSION
> DEMO" link in our HUD, ship the manual game as the polished primary
> demo. Fusion shows up as a sidecar pitch beat, not the main flow.

---

## Out of scope (deferred to post-hackathon)

- Catalog refactor 12 named platforms → 8 abstract types (the
  `engine/unit_types.json` we drafted earlier)
- Multi-WebSocket protocol redesign — keep HTTP, add `/api/tracks`
  snapshot
- Replacing Brian's React inspect panel wholesale — port glyphs only
- Replay export with fused tracks
- Multi-game / multi-session
- Tuning UI for fusion gates
- Reflection-loop learning + LLM agent
- True velocity model + radar range-rate
- Stealth-aware sensor curve

---

## Pre-flight sanity checks (before starting M1)

1. `python -c "import sys; sys.path.insert(0,
   'sensor_pipeline/backend'); from fusion import FusionEngine;
   print('ok')"` — does it import in our venv?
2. `python sensor_pipeline/backend/test_assignment_regressions.py` —
   Brian's regression suite passes today, in our venv.
3. `python scripts/test_combat.py` — our 12 smoke tests pass.
   Tag this as the regression baseline.
4. `lsof -i :8000` while engine server is up — confirm port collision.
5. Manually probe: instantiate one `RadarSensor` and one `Target`,
   call `observe()` 100×, confirm no global state leaks (deterministic
   with `random.seed()`).
6. Confirm `Track.to_dict()` is JSON-serializable through `json.dumps`
   (Classification enum keys are the live concern).
7. `grep -r "import random" sensor_pipeline/` to enumerate every place
   we need to swap to a scoped RNG (or, with caching strategy, can
   leave alone).

---

## What needs to happen before M1 starts

Sign-off from Alex on:

1. Architecture (sub-package + per-side engines + flag) — yes/no?
2. `HEX_SIZE_KM = 5` — yes / different number / "anchor to lat-lng later"?
3. **Engine truth stays the source of truth for combat**, fusion is
   presentation only — yes/no?
   *(This is the load-bearing call. If you want fusion to drive combat
   resolution, replays break and the deterministic combat resolver
   story dies.)*
4. Migration path (M1 → M3 demo target, M4 optional)?
5. Send Brian the 5 questions, or want me to draft the message?

Once those 5 are answered we can kick off M1 (the cold-import smoke),
which is genuinely 1.5h and zero behavior change.

---

## Revision history

- 2026-05-02 — initial plan synthesized from 8 parallel agent reports
  (architecture, spatial bridge, time alignment, data model, viz,
  backend, taxonomy, migration, risks).
