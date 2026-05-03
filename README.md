# Wargame Gym

An open environment for evaluating LLM agents on multi-domain wargaming under
fog of war. Deterministic, replayable, and built so humans, scripted baselines,
and learning loops can all play side-by-side.

Built for the **xTech National Security Hackathon** (May 2-3 2026, SHACK 15
SF). Pitched as the substrate for training next-generation wargaming AI:
an open gym + scoring + visualization, framed as **decision support for
human commanders** — never autonomous engagement.

## Status

End-to-end **manual human-vs-human** game is live. Both sides are queued on
one screen with a viewmode toggle, orders are committed simultaneously, then
animated through the resolver. AI agents (LLM-driven) are the next chunk.

## What's playable today

- **Real Esri World Imagery** satellite backdrops (8 region presets +
  arbitrary lat/lng), terrain auto-derived from the photo by sampling each
  hex center, units auto-placed on terrain-legal hexes, in-UI region
  picker with a 🎲 reroll for fresh seeds.
- **20×15 hex grid** (pointy-top, odd-r), 5 terrain types with per-domain
  movement costs, fog of war driven by per-unit sensor ranges,
  last-known-position ghosts that decay over 3 turns.
- **12 named platforms** + **6 fixed bases** (3 per side: airbase, naval
  base, FOB / launch site), composable Sensor / Weapon catalog with
  per-target Pkill tables.
  *(A behavior-first refactor to 8 abstract types lives in
  `engine/unit_types.json` — proposed but not yet wired.)*
- **Simultaneous-orders turn loop** with a clean state machine:
  ```
  QUEUEING_BLUE → BLUE_LOCKED → QUEUEING_RED → RED_LOCKED → RESOLVING → next
  ```
- **6 actions per unit**: MOVE / STRIKE / SCOUT / OVERWATCH / HOLD / CAPTURE
  with two-step targeting and a transient "TARGET NOT SENSED" banner when
  a click would be silently dropped at resolution.
- **Keyboard shortcuts**: `M S V O H C X` for actions, `ESC` to cancel,
  `Space` to lock the active side or resolve.
- **Deterministic combat resolver** (blake2b-seeded rolls), simultaneous
  strikes against pre-strike HP, weapon-typed damage, stealth penalty,
  4-phase pipeline (SCOUT → MOVE+OW → STRIKE → UPDATE).
- **Animated playback**: per-platform move speeds (jet 600 ms/hex,
  drone 700, ship 1100, infantry 1500, SAM stationary), in-flight dashed
  path with bright "traveled" overlay, glowing strike tracers, fire/smoke
  particle bursts, "DESTROYED" popups, HP bars colour-coded
  green/amber/red that drop in real time on each hit.
- **Score & timer**: cost-weighted health (max 100) + objective bonus
  (+5/turn per held objective, capped at +30). 5-minute match clock that
  pauses during animations. End-game overlay with breakdown + REPLAY.
- **Battle log** — scrolling event narrative pinned at bottom of screen.

## Architecture

```
engine/                  pure-Python game logic
  catalog/                 sensors, weapons, platforms, bases (CURRENT)
  unit_types.json          single-file abstract spec (PROPOSED)
  hex.py                   pointy-top odd-r hex math
  terrain.py               5 terrain types, per-domain move costs
  movement.py              dijkstra reachability + path_to
  state.py                 GameState pydantic models (wire format)
  scenario.py              YAML loader, denormalizes catalog refs
  orders.py                Order discriminated union (6 kinds)
  events.py                Event log shapes the resolver emits
  resolve.py               deterministic 4-phase combat resolver

scenarios/
  strait_n7.yaml           current scenario (auto-rewritten on region swap)

scripts/
  setup_region.py          orchestrator: fetch + sample + dump
  fetch_satellite.py       Esri tile fetcher (parallel, with cache)
  sample_terrain.py        derive terrain + place units from PNG
  dump_state.py            scenario YAML -> web/public/state.json
  fetch_icons.py           pull 18 game-icons.net SVGs (CC BY 3.0)
  test_combat.py           engine smoke tests
  render_terrain.py        offline procedural fallback (no internet)

server/
  main.py                  FastAPI control plane

web/                     React + Vite + Pixi.js v8 + Tailwind + Zustand
  src/anim/                tween.ts, primitives.ts, timing.ts
  src/render/              units.ts (per-id Containers), icons.ts
  src/components/          MapStage, HUD, TurnBar, ActionMenu,
                           RegionPicker, ViewModeToggle, BattleLog,
                           EndGameOverlay, Timer

references/                RFI PDF
replays/                   reserved for replay tapes (gitignored)
```

## Quickstart

```bash
# 1. install deps
make install

# 2. fetch satellite + render scenario + state.json (default Bonifacio Strait)
make terrain

# 3. start backend + frontend (separate terminals or via `make dev`)
make server      # FastAPI on :8000
make web         # Vite on :5173

# OR everything at once
make dev
```

Open http://127.0.0.1:5173.

### Controls

- Click any friendly unit → action menu in right rail
- `M` MOVE · `S` STRIKE · `V` SCOUT · `O` OVERWATCH · `H` HOLD · `C` CAPTURE
- `X` clear the selected unit's queued order
- `ESC` cancel targeting / deselect
- `Space` lock current side, or resolve when both locked

### Region swap

```bash
python3 scripts/setup_region.py aegean       # named preset
python3 scripts/setup_region.py custom \      # custom point
    --lat 36.0 --lng 28.0 --zoom 11 --name "Cyprus"
python3 scripts/setup_region.py --list       # list presets
```

Or use the **REGION** dropdown in the top bar.

## Game mechanics

**Win condition.** Highest score after 5:00 wins. Score =
`100 × cost-weighted health remaining` + `min(30, objective_points)`.
Annihilation (your score reaches 0) = instant loss.

**Sensor model.** Sensor ranges are intentionally tight (most ground
units = own hex only, scout drones = 4 hexes). Combined arms emerges:
SAM batteries and fighters need a UAV / forward observer to cue them.
Killing the enemy's scout drone blinds them.

**Determinism.** Every roll seeds a PCG64 from
`blake2b(game_seed, turn, attacker_id, target_id, roll_index)`. Replay
is exact: same seed + same orders → bitwise-identical events.

**Pkill / damage split.** Two numbers per strike:
- `pkill[target_domain]` = per-shot HIT probability (0..1)
- `damage` = HP loss on a successful hit

So a missile launcher (Pkill 0.70 vs air, damage 2) firing at a fighter
(3 HP) needs ~3 shots in expectation. The launcher only carries 4
missiles total.

**Animation pause.** The match clock pauses while the resolver
animation plays so judges see deliberate decisions, not panic clicking.

## Testing

```bash
. .venv/bin/activate
python3 scripts/test_combat.py
```

12 smoke tests covering MOVE / STRIKE in-range / out-of-range / SCOUT /
DESTROY / SCORE / OVERWATCH / CAPTURE / simultaneous strikes / HOLD
no-op / unreachable MOVE / initial state. Runs in ~1s.

## Roadmap

1. **Refactor catalog** to `engine/unit_types.json` (8 abstract types).
2. **LLM agent** driver: Claude Opus 4.7 with parallel tool use + prompt
   caching, takes COP + ROE, returns Order list per turn. Same wire
   format the manual UI produces.
3. **Reflection-loop learning**: agent reflects after each game, writes
   lessons to disk, feeds top-K into the next game. Plot win-rate
   improvement over N games vs a frozen baseline.
4. **Spawn from base** mechanic.
5. **Last-known-position uncertainty cone** instead of point ghosts.
6. Replay export / scrub.

## Attribution

- Imagery © Esri, Maxar Technologies, Earthstar Geographics — used
  under fair use for hackathon demonstration.
- Icons by [game-icons.net](https://game-icons.net/) contributors,
  CC BY 3.0.
- xTech RFI PDF in `references/`.
