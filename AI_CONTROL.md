# AI Control Architecture

The plan for the LLM-driven controller that plays one or both sides of the
wargame, logs its reasoning, and visibly improves over multiple games via a
memory-of-lessons system. **Source of truth — don't let it drift.**

Status: **DESIGN** (not yet implemented). Awaiting "go" before any code.

---

## 1. What we're building

A drop-in side-controller that plugs into the existing engine without touching
the resolver, and a memory layer that lets it improve game-over-game without
weight updates.

**Scope of the demo**:
- Either side (BLUE / RED) can be set to `MANUAL` (existing UI flow) or `LLM`
- One toggle per side. The AI auto-fires the moment the other side locks orders.
- A reasoning panel shows the AI's plan + per-unit intent after each turn.
- A persistent memory of lessons accumulates over games and is consulted on
  every turn.
- A self-play harness (headless, Haiku-cheap) runs ~20-30 games overnight to
  populate the memory.
- The demo shows the same scenario seed played twice — once with empty memory,
  once with curated/matured memory — and the play *visibly* differs.

**Non-goals (explicitly cut)**:
- RL / fine-tuning / weight updates
- Both sides LLM in the live demo (architecturally fine; demo-narratively worse)
- Embedding-based memory retrieval (overkill for ≤30 lessons)
- Live token streaming in the reasoning panel (render-after is enough)
- A skill / playbook tier above lessons (lessons are flat, recency-weighted)

---

## 2. System architecture

```
                     ┌─────────────────────────────────────┐
                     │           Frontend (React)          │
                     │                                     │
                     │  ControllerSelector  (per side)     │
                     │  ReasoningPanel      (last turn)    │
                     │  PostmortemDrawer    (lessons)      │
                     └─────────────────┬───────────────────┘
                                       │ HTTP (existing API + 3 new endpoints)
                                       ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       FastAPI server (Python)                       │
   │                                                                     │
   │  POST /api/orders         (existing)                                │
   │  POST /api/resolve        (existing)                                │
   │  GET  /state.json         (existing)                                │
   │  POST /api/ai/play        (NEW — fires the controller for one side)│
   │  GET  /api/ai/last_reasoning   (NEW)                                │
   │  GET  /api/ai/postmortem  (NEW)                                     │
   │                                                                     │
   │  Auto-trigger: when one side locks AND other side is non-manual,    │
   │  the server queues the AI for that side (asyncio task).             │
   └─────────────────────────────────────────┬───────────────────────────┘
                                             │
                                             ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       ai/ package (new)                             │
   │                                                                     │
   │  controller.py     Controller ABC + registry                        │
   │  random_agent.py   bootstrap baseline                               │
   │  heuristic.py      scripted policy (port of rl_alt/scripted_red)    │
   │  llm_agent.py      Claude-driven controller (the hero)              │
   │  prompt.py         system + state + memory + menu rendering         │
   │  menu.py           legal-action enumerator with tactical pruning    │
   │  validate.py       repair / snap-to-legal for invalid LLM orders    │
   │  memory.py         lessons.jsonl read/write + recency retrieval     │
   │  reflect.py        post-game Claude call → 3 lessons → store        │
   │  runner.py         async play_side(side, controller, state)         │
   │                                                                     │
   │     ↑                                                               │
   │     │  reads from:                                                  │
   │     ├──  /Users/alexcrlhmmr/Documents/nsa_hack_26/engine/*          │
   │     └──  /Users/alexcrlhmmr/Documents/nsa_hack_26/memory/*.jsonl    │
   └─────────────────────────────────────────────────────────────────────┘

                                 ┌──────────┐
                                 │ Anthropic │
                                 │   API    │
                                 └──────────┘

   Headless self-play (separate process):
   ┌─────────────────────────────────────────────────────────────────────┐
   │  scripts/self_play.py                                               │
   │   - bypasses HTTP entirely                                          │
   │   - drives engine via rl_alt/engine_env.py (Brian's RL wrapper)     │
   │   - runs N games concurrently with asyncio + Anthropic batches API  │
   │   - writes:                                                         │
   │       runs/<id>/game_<seed>.jsonl  (full trace per game)            │
   │       runs/<id>/summary.csv        (one row per game)               │
   │       memory/lessons.jsonl         (appended via reflect.py)        │
   └─────────────────────────────────────────────────────────────────────┘
```

---

## 3. The turn loop (data flow)

### Manual side queues, AI side is auto-driven

```
1. User selects RED=LLM in HUD; BLUE=MANUAL.
2. User queues blue orders, clicks LOCK BLUE.
3. Server: blue_locked=true. Auto-trigger fires: ai.runner.play_side(red).
4. Runner builds prompt:
     a. cached system prompt (rules, schema)
     b. state block (ASCII map + own units + visible enemies + ghosts + history)
     c. memory block (top-5 lessons from memory.jsonl, recency-weighted)
     d. menu block (per-unit pruned legal-action menu, ~5-10 options each)
5. Runner POSTs to Anthropic API (Sonnet 4.6, prompt caching, JSON tool call).
6. LLMTurnPlan returned: {summary, decisions: [{unit_id, action_id, intent}]}
7. ai/validate.py maps action_ids → concrete Order objects. Repair if any
   invalid (snap to nearest legal hex; fallback HOLD).
8. Server: submit_orders(red, orders, lock=true). Now both sides locked.
9. Server: resolve_turn() runs (existing path). Events flow back.
10. Frontend: ReasoningPanel updates from /api/ai/last_reasoning.
    Replay animation plays.
11. If state.winner != None:
       ai/reflect.py fires asynchronously:
         - Claude call with full game trace + outcome
         - Returns 3 lessons (Reflexion-style self-critique)
         - Critic LLM cross-checks (separate prompt, no agent persona)
         - Approved lessons appended to memory/lessons.jsonl
       PostmortemDrawer shown to user with score curve + new lessons.
```

### Both sides AI

Same flow, except:
- User clicks one "RUN TURN" button
- `play_side(blue)` and `play_side(red)` fire concurrently (`asyncio.gather`)
- After both return, `submit_orders` for both sides, then resolve

---

## 4. The action space — pruned legal-action menu

### The contract

For each unit on the AI's side, build a **menu of stable-numbered legal
actions**. The LLM picks an `action_id` (integer) per unit plus a one-line
`intent` string. We map IDs back to concrete Order objects and submit.

### Why menu over free-form coords

LLMs hallucinate hex coordinates. Even Sonnet 4.6 drops ~15-30% of MOVE/STRIKE
orders to "out of range" or "blocked path" without grounding. Menu eliminates
this category of failure entirely. The token cost is ~600-800 extra per turn
across all units — comfortably within budget after pruning.

### Menu structure (per unit)

```
blue-fighter-1 — fighter (air) | hp 3/3 | speed 5 | sensor 1 (passive) → 4 if radar ON
weapons: aam_asm range 3 dmg 2 ammo 4 (hits land/air/sea)
position: H(7,4) | last action: MOVE H(5,4)→H(7,4) (turn 2)

threats: red-bomber-1 H(13,4) hp ?/?, red-armor-1 H(11,3) hp ?/?
ghosts:  red-fighter-1 last seen H(2,1) turn 1

legal actions:
  1. HOLD
  2. OVERWATCH                 — auto-fire on movers within range 3
  3. MOVE H(11,4)              — close on red-bomber-1, no cover
  4. MOVE H(9,2)               — flank red rear, no LOS yet
  5. MOVE H(7,9)               — pressure red destroyer
  6. MOVE H(5,5)               — withdraw toward base
  7. STRIKE H(11,3)            — red-armor-1 in range, dmg 2 (ammo→3)
  8. ACTIVATE_RADAR            — see +3 hexes; emits, exposes to red sigint
  9. CUSTOM                    — escape hatch: emit raw {kind, target_hex}
```

### Pruning rules

For MOVE, only include hexes that:
- Are adjacent to a visible enemy unit (engagement)
- Increase LOS to known enemies (recon advance)
- Close most distance to nearest visible enemy (top 2-3)
- Withdraw toward the friendly base (top 2)
- Are the unit's current hex (stay put, useful with terrain bonus)
- Are tactical waypoints toward unscouted territory

For STRIKE, only target hexes that:
- Have a visible enemy or recently-vacated ghost position
- Are within weapon range
- Match weapon's `target_domains` (no SAM-on-ground entries)

For SCOUT (drones only): always available
For OVERWATCH: available iff `unit.weapon > 0`
For HOLD: always available
For ACTIVATE_RADAR / DEACTIVATE_RADAR: available iff unit has a radar sensor

Disabled options stay in the list (greyed-out style) so the model can plan
toward them next turn ("STRIKE H(13,4) — out of range").

### Output schema (Pydantic)

```python
class Decision(BaseModel):
    unit_id: str
    action_id: int  # must exist in this unit's menu
    intent: str     # one-line natural language

class LLMTurnPlan(BaseModel):
    summary: str           # 1-2 sentence top-level commander's intent
    decisions: list[Decision]
```

### Validation + repair

```python
def validate_plan(plan: LLMTurnPlan, menus: dict[str, Menu]) -> list[Order]:
    orders = []
    for d in plan.decisions:
        menu = menus.get(d.unit_id)
        if not menu:
            log_drop(d, "unknown unit_id")
            continue
        action = menu.find(d.action_id)
        if action is None:
            # Repair: try CUSTOM if the LLM tried to be creative
            if d.action_id == 99 and d.intent_has_coords():
                action = repair_custom(d, menu)
            if action is None:
                log_drop(d, "action_id out of menu")
                orders.append(HoldOrder(unit_id=d.unit_id))
                continue
        orders.append(action.to_order(d.intent))
    # Ensure every controllable unit has an order (default HOLD)
    return fill_missing_with_hold(orders, controllable_unit_ids)
```

### CUSTOM escape hatch (action_id=99)

Lets the AI emit free-form coordinates when the menu is too restrictive. We
snap-to-legal: if MOVE target is out of range, walk back along the path to
the farthest legal hex. If STRIKE target is empty (whiff bait), still allow
it (legitimate fog-of-war play).

Track `custom_used_pct` per game. If >20%, our pruner is too tight; if ~0%,
the menu is sufficient.

---

## 5. The observation — what the AI sees

A single user-message block, ~3-4k tokens total. Map is in the cached system
prompt (terrain doesn't change); per-turn block has only the dynamic parts.

### Per-turn block layout

```
# TURN 7 — RED to move
score: blue 87.4 / red 62.1 (HP %)
turn_cap: 20

## Map (your view)
   00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19
00  ~  ~  ~  .  .  .  f  f  .  .  ^  ^  .  .  .  .  .  .  .  .
01   ~  ~  .  .  .  f  f  .  .  ^  ^  ^  .  .  .  .  .  .  .  .
...
04  .  .  .  .  B1 .  .  .  .  .  .  .  .  .  R1 .  .  .  .  .
...

Glyphs: uppercase = your units (RED), lowercase = visible BLUE,
        ? = ghost (last-known), . = open, f = forest, ^ = mountain,
        ~ = water, # = urban.

## Your units
R1 fighter      14,04  hp 3/3   sens[passive*1, radar OFF]    ammo[aam:4]    dom=air
R2 destroyer    16,02  hp 5/5   sens[passive*1, radar OFF]    ammo[ship:6]   dom=sea
...

## Visible BLUE
b1 infantry     04,04  hp ?/?   seen_by:R1                                   dom=land
b3 missile_lnch 05,09  hp ?/?   seen_by:R2                                   dom=land

## BLUE ghosts (last known, hasn't moved from this hex since)
?  b5 unit_class=armor    last_seen 06,07  t=4

## Recent events (last 3 turns)
t5 R1 move 12,04 → 14,04
t5 b3 strike 14,03 → R3 destroyed (was destroyer)
t6 b1 move 03,04 → 04,04 (spotted)

## Side notes
- you are RED. objective: reduce blue HP to 25% or annihilate.
- it is now RED's turn to issue orders for all units below.
```

### Memory block (top-K lessons)

```
## Lessons from prior games (most relevant first)
[L:lsn_a91c | mid | red bomber lost to blue SAM after radar-on at <3hex]
[L:lsn_b2f7 | open | hold radar dark until enemy commits]
[L:lsn_c401 | end  | concentrate fire — 2 strikes on same target wins trades]
...
```

### Menu block

Per-unit menu as shown in section 4.

### System prompt (cached, ~1500 tokens)

- Game rules summary (turn order, win conditions, terrain, fog)
- Unit catalog one-liners (each of the 9 types)
- Output schema reminder + JSON example
- Failure-mode reminders ("do not pick action_ids outside the menu", "use
  CUSTOM only when no menu option fits")

---

## 6. Memory system

### Storage: `memory/lessons.jsonl` (append-only)

Each line is one Lesson:

```json
{
  "id": "lsn_a91c",
  "claim": "Red bomber lost to blue SAM after radar activation within 3 hexes of unscouted coast.",
  "tags": {
    "phase": "mid",
    "side": "red",
    "units_involved": ["bomber", "missile_launcher"],
    "outcome": "loss"
  },
  "source_game_id": "game_3",
  "source_turn": 12,
  "support_count": 1,        // increments on dedup-merge
  "created_ts": "2026-05-03T03:14:22Z"
}
```

**No embeddings.** Retrieval is recency × tag-match. We have ≤50 lessons; this
is enough.

### Retrieval (`ai/memory.py:top_k`)

```python
def top_k(lessons: list[Lesson], state: GameState, side: str, k: int = 5):
    """Score each lesson by:
       0.5 × tag_match_score(lesson.tags, current_state_tags)
     + 0.3 × recency_decay(lesson.created_ts, half_life=10_games)
     + 0.2 × support_count_normalized
    Return top-k.
    """
```

`current_state_tags` is computed cheaply: `phase = "open" if turn ≤ 5 else "mid" if turn ≤ 12 else "end"`, etc.

### Write (`ai/reflect.py`)

After `state.winner != None`:

1. **Agent self-critique pass**: Claude call with full game trace +
   outcome, prompted to write 3-5 lessons in JSON form. Reads its own
   reasoning logs.
2. **Critic LLM pass** (separate model context, no agent persona): same
   trace, asked to score each agent lesson on `{novel, correct, actionable}`
   and emit its own 3-5 lessons.
3. **Merge**: keep critic-approved + critic-original. Dedupe against existing
   memory by claim-similarity (simple string-overlap heuristic for v1).
4. Append to `memory/lessons.jsonl`.

### Hygiene

- Hard cap: 100 lessons. When exceeded, drop oldest with lowest
  support_count.
- Every retrieval increments a `last_retrieved` field. Never-retrieved
  lessons get archived after 5 games.

---

## 7. Self-play overnight harness

### Goal

Generate ~20-30 real game traces with `reflect.py` writing lessons. By morning
we have a populated `memory/lessons.jsonl` to demo from.

### Implementation

`scripts/self_play.py`:

```
python scripts/self_play.py \
  --games 20 \
  --blue llm \
  --red heuristic \
  --model haiku-4-5 \
  --concurrency 4 \
  --max-cost-usd 20 \
  --out runs/$(date +%s)/
```

Drives the engine via `rl_alt/engine_env.py` (no HTTP). Asyncio + semaphore
controls concurrency. Anthropic Message Batches API for 50% discount where
applicable.

Output:
- `runs/<id>/game_<seed>.jsonl` — full trace per game
- `runs/<id>/summary.csv` — one row per game (winner, turns, cost)
- `memory/lessons.jsonl` — appended during run via reflect

### Cost estimate

| Config | Per game | 20 games |
|--------|----------|----------|
| Haiku 4.5 (LLM blue, scripted red) | ~$0.30 | ~$6 |
| Sonnet 4.6 (LLM blue, scripted red) | ~$1.00 | ~$20 |

Tight for hackathon. Run on Haiku. Quality difference for *generating
lessons* matters less than for *playing the live demo turn*.

### Killswitch

- `--max-cost-usd N` aborts when accumulated cost exceeds N
- `runs/<id>/STOP` sentinel file checked between games
- Anthropic API key with $50 monthly cap as the final backstop

---

## 8. Demo plan (how it ties to code)

### The hero shot — same-seed side-by-side

Two map panels, same scenario seed (TBD during Phase E seed-search), both on
the **Galician Approach** map, played by the same Sonnet 4.6 agent:

| Left panel: empty memory | Right panel: curated 8-lesson memory |
|--------------------------|--------------------------------------|
| Activates radar early    | Holds radar dark, sends scout first  |
| Loses destroyer to SAM   | Maps red SAM position, kills it      |
| 8 turns, score 35-100    | 12 turns, score 90-40                |

We don't run live during the demo — both replays are pre-rendered videos
played from a `replay-cache/` folder. Live API call only happens in Beat 1
(one turn) for credibility.

### How we generate "the curated memory"

1. Run `scripts/self_play.py` with 20 games on Haiku overnight.
2. Morning: read `memory/lessons.jsonl`, eyeball, keep the 6-8 lessons that:
   - Are coherent
   - Map to a specific tactical pattern (e.g. radar timing, screening,
     concentration of fire)
   - Are likely to show up in our chosen demo seed
3. Save those into `memory/curated.jsonl`
4. Pre-record both replays on the same Galician seed:
   - `python scripts/replay.py --region galician --seed <S> --memory empty`
   - `python scripts/replay.py --region galician --seed <S> --memory curated`

### What's defensible about this

- Lessons are real (generated by Reflexion-style self-critique on real
  games)
- Mechanism is real (LLM responds to its prompt context — same model, same
  scenario, different memory injection → different play)
- Cherry-pick is the seed (we picked one of 20 we tried)

What would NOT be defensible:
- Hand-writing the lessons ourselves
- Using a different model for the two replays
- Doctoring the engine to favor the second replay

---

## 9. Failure modes + mitigations

| # | Failure | Mitigation |
|---|---------|------------|
| 1 | LLM picks invalid action_id | Validator falls back to HOLD, logs drop |
| 2 | LLM uses CUSTOM with bad coords | repair_custom snaps to nearest legal hex |
| 3 | LLM latency >30s on a turn | `asyncio.wait_for(timeout=20s)`; fallback to heuristic for that turn; UI shows "fallback" badge |
| 4 | Anthropic API down/rate-limited | Retry with backoff (tenacity), then heuristic fallback |
| 5 | Memory doesn't improve play | Pivot demo: drop "improvement" claim, lead with "AI in control + visible reasoning" |
| 6 | Token cost runaway | Hard cap per game; auto-demote to heuristic for remaining turns; UI badge "budget exhausted" |
| 7 | Same-seed replays too similar (no visible improvement) | Re-run self-play with stricter "novel-only" lesson criteria; increase memory recency weight |
| 8 | Reasoning panel renders garbage / truncated | Validate JSON before display; on parse fail, show raw_text |
| 9 | Hot-seat compatibility breaks | Controllers are per-side; existing hot-seat 2-pass replay only fires when realGame=true; AI side fills in orders before lock — no interaction with replay code |
| 10 | UI / engine state desync mid-AI-turn | Pixi click handler already bails on `replaying \|\| resolving`; add `aiThinking` flag to that bail list |

---

## 10. Build order (with checkboxes)

### Phase A — plumbing (2h)

- [ ] `ai/__init__.py`, `ai/controller.py` (ABC + registry)
- [ ] `ai/random_agent.py` (uniform random over legal actions)
- [ ] `ai/runner.py` (`async play_side(side, controller, state) -> None`)
- [ ] `server/main.py`: `_session["controllers"]`, `POST /api/ai/play`,
      auto-trigger on lock
- [ ] `web/src/components/ControllerSelector.tsx` (per-side toggle)
- [ ] Verify end-to-end: BLUE=human, RED=random, full game completes

### Phase B — LLM agent (3h)

- [ ] `ai/menu.py` — `build_legal_action_menu(unit, state)` with pruning
- [ ] `ai/prompt.py` — system + state + memory + menu render
- [ ] `ai/llm_agent.py` — single Anthropic call, JSON tool output
- [ ] `ai/validate.py` — action_id → Order, repair invalid
- [ ] `web/src/components/ReasoningPanel.tsx` (render-after, plain text)
- [ ] `GET /api/ai/last_reasoning` — returns last turn's plan
- [ ] Verify: BLUE=human, RED=LLM(sonnet), full game; reasoning visible

### Phase C — memory + reflection (2h)

- [ ] `ai/memory.py` — JSONL read/write, top-k retrieval
- [ ] `ai/prompt.py` — memory block injection
- [ ] `ai/reflect.py` — post-game lesson extraction (agent + critic)
- [ ] Wire reflect to fire on `state.winner != None`
- [ ] `GET /api/ai/postmortem/{game_id}` — for the drawer
- [ ] `web/src/components/PostmortemDrawer.tsx`
- [ ] Verify: 3-game sequence; lessons accumulate; later games reference them

### Phase D — self-play harness (2h)

- [ ] `ai/heuristic.py` — port of `rl_alt/scripted_red.py`
- [ ] `scripts/self_play.py` — concurrent headless games
- [ ] Cost tracking + STOP sentinel
- [ ] Test: 2-game sweep; verify outputs
- [ ] Kick off overnight 20-game batch on Haiku

### Phase E — demo polish (3h)

- [ ] `scripts/replay.py` — record a game's events for video playback
- [ ] Pick demo seed (try 5-10, find one where memory matters)
- [ ] Curate `memory/curated.jsonl` from overnight run
- [ ] Pre-record both panels (empty vs curated) as MP4
- [ ] Record fallback "everything live" video as risk mitigation
- [ ] Presenter-mode toggle (1.4× font, high contrast)
- [ ] 3-min rehearsal × 3

### Phase F — stretch (if time)

- [ ] Both-sides-AI in live UI
- [ ] Streaming token render in ReasoningPanel
- [ ] Self-play with vN-vs-v(N-1) curriculum
- [ ] AI-vs-heuristic head-to-head browser visualization

---

## 11. Locked decisions / open questions

### Locked

- [x] **Model for live demo turn**: **Claude Sonnet 4.6**. Confirmed.
- [x] **Demo scenario**: **Galician Approach** (`galician` region, see
  `scripts/setup_region.py`). Self-play and pre-recorded A/B replays will
  also use this scenario for consistency.

### Still open

- [ ] **Reflection model**: Haiku 4.5 or Sonnet 4.6 for post-game critic?
  Default to Haiku — task is summarization, not strategy. Revisit if lessons
  feel shallow.
- [ ] **Memory cap**: 100 lessons hard / 50 active? Will revisit after a
  few self-play runs.
- [ ] **Both sides AI in live UI**: do we want a "watch it play itself"
  mode for the demo? Stretch goal — only if Phase E completes early.

### Coordinating with Brian

Brian is in parallel reworking some game-rule / option surfaces. The AI
control architecture is **rule-agnostic** by design:

- The controller reads `state.json` and writes `Order` objects via
  `/api/orders`. As long as the order schema and state schema stay
  compatible, rule tweaks propagate transparently.
- The action menu (`ai/menu.py`) is parameterised by the unit catalog and
  weapon target_domains — it adapts to whatever Brian ships.
- The system prompt's "rules summary" block will need to be re-rendered if
  victory conditions or unit stats change. It's a single file, easy to
  refresh.
- Self-play data generated against an old ruleset will need to be discarded
  (or re-tagged) if Brian's changes alter tactical dynamics meaningfully —
  so we should ideally run the overnight self-play on **post-merge** code.

---

## 12. File tree (after implementation)

```
nsa_hack_26/
├── ai/                         (NEW)
│   ├── __init__.py
│   ├── controller.py
│   ├── random_agent.py
│   ├── heuristic.py
│   ├── llm_agent.py
│   ├── prompt.py
│   ├── menu.py
│   ├── validate.py
│   ├── memory.py
│   ├── reflect.py
│   └── runner.py
├── memory/                     (NEW)
│   ├── lessons.jsonl
│   └── curated.jsonl
├── runs/                       (NEW, gitignored)
│   └── <run-id>/
│       ├── game_<seed>.jsonl
│       └── summary.csv
├── replay-cache/               (NEW, gitignored)
│   ├── seed42_empty.mp4
│   └── seed42_curated.mp4
├── scripts/
│   ├── e2e_smoke.py            (existing)
│   ├── self_play.py            (NEW)
│   └── replay.py               (NEW)
├── server/
│   └── main.py                 (extended)
├── web/src/components/
│   ├── ControllerSelector.tsx  (NEW)
│   ├── ReasoningPanel.tsx      (NEW)
│   └── PostmortemDrawer.tsx    (NEW)
└── AI_CONTROL.md               (this file)
```

---

## 13. The pivot

If at any point during the build we discover memory **doesn't actually
improve play** in our self-play data:

1. Drop the "AI improvement" beat from the demo
2. Lead with "AI in control + visible reasoning" (still strong)
3. Replace Beat 2 with something else — maybe "AI explains its strategy"
   or "AI plays under fog vs omniscient"
4. Be honest in the post-mortem talk track: "we built the memory system,
   discovered the gains were small, here's what we'd try next"

The pivot is the discipline. Don't over-promise to judges. The engine + LLM
controller + reasoning panel is already a strong demo.
