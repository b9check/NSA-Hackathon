# NSA Hackathon — AI Wargaming Gym

Open environment for evaluating LLM agents on multi-domain wargaming under fog
of war. Deterministic, replayable, and built to host humans, baseline agents, and
learning loops side-by-side.

Pitched as the substrate for training next-generation wargaming AI: an open
gym + scoring + visualization, with a credible **decision-support** framing for
military stakeholders (not autonomous engagement).

## Status — branch `alex_game_engine`

0→1 game engine + visual representation. No combat resolution or LLM agents yet.

- Hex grid (20×15, pointy-top, odd-r), 5 terrain types
- 12 unit types across land/air/sea + 3-unit Shahed swarm
- One scenario: "Strait N-7 Crisis" (two land masses, neutral central island
  objective)
- Deterministic state load from YAML → JSON
- React + Pixi.js v8 visualization with click-to-select and live reachability
  / sensor / weapon range overlays for the selected friendly unit
- Animated water shimmer, glowing objectives, terrain ornaments

## Quickstart

One-time setup:

```bash
# Python deps (engine + scenario loader)
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# JS deps (frontend)
cd web && npm install && cd ..
```

Run the demo:

```bash
# 1. Generate the initial game state
source .venv/bin/activate
python3 scripts/dump_state.py scenarios/strait_n7.yaml web/public/state.json

# 2. Start the frontend (separate terminal)
cd web && npm run dev
# open http://127.0.0.1:5173
```

Or use the Makefile:

```bash
make install   # both Python and JS deps
make dump      # regenerate state.json from scenario
make web       # vite dev server
```

## Layout

```
engine/          # game rules: hex math, terrain, units, state, movement
scenarios/       # YAML scenarios (terrain map + unit placements)
scripts/         # CLI tools (dump_state.py)
web/             # React + Vite + Pixi.js frontend
references/      # RFI and source docs
replays/         # game tapes (gitignored)
```

## What's next

- Combat resolution (Pkill matrix, simultaneous turn order)
- Fog of war (per-side observable state)
- LLM agent driver (Claude Opus 4.7, parallel tool use, prompt caching)
- Reflection / lessons.md learning loop
- Replay system (deterministic re-run from cached agent decisions)

See conversation history with Claude for the full plan.
