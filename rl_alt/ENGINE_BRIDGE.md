# EngineEnv scoping: bridging PPO to the real engine

Read-only scoping doc. Not a design freeze — a v0 cut to decide whether to invest a week.

## 1. Where to wrap

Single chokepoint: `engine/resolve.py:424` —
`resolve_turn(state: GameState, blue_orders: list[Order], red_orders: list[Order]) -> list[Event]`.

It mutates `state` in place and returns the deterministic event log. The
server uses exactly this entrypoint (`server/main.py:41,473`); no other glue
is required to drive the engine. `engine.scenario.load_scenario(path)`
(`engine/scenario.py:101`) builds the initial `GameState` from the only
scenario YAML present, `scenarios/strait_n7.yaml` (20×15 hexes, 16 units, 6
bases, 2 objective hexes, turn_limit 30, seed 258966).

`EngineEnv.step(action)` would:
1. Decode `action` -> `list[Order]` for the agent's side.
2. Build the opponent's `list[Order]` via a scripted policy (see §4).
3. Call `resolve_turn(state, blue_orders, red_orders)`.
4. Compute reward from `compute_scores(state)` (`engine/resolve.py:394`).
5. Build obs from `state` (already a pydantic model, JSON-dumpable via
   `state.model_dump(mode="json")`, same as `scripts/dump_state.py:26`).

That's the entire surface. No FastAPI, no session lock, no event animation.

## 2. Action space sketch

Order types (`engine/orders.py`): MOVE(target_hex), STRIKE(target_id),
SCOUT(target_hex), OVERWATCH, HOLD, CAPTURE(target_hex). Targets are
free-form: any hex on a 20×15 grid (300 cells) for MOVE/SCOUT/CAPTURE; any
unit-or-base id for STRIKE.

**Recommended encoding (per-unit MultiDiscrete, fixed roster):**

Pad unit slots to `MAX_UNITS = 12` per side (the scenario has 7 blue / 9
red mobile units; 12 covers attrition headroom — units only leave the list,
never join, since there is no spawning yet). Each slot gets a discrete
action drawn from a compact menu:

```
{HOLD, OVERWATCH,
 MOVE_NE, MOVE_E, MOVE_SE, MOVE_SW, MOVE_W, MOVE_NW,    # 1-step move per turn
 STRIKE_NEAREST_VISIBLE_ENEMY,
 SCOUT_FORWARD,
 CAPTURE_HERE}
```

11 sub-actions × 12 slots = `MultiDiscrete([11]*12)` ≈ 11^12 joint, but
factored. Dead/missing slots get a no-op mask. This **drops** multi-hex
moves and explicit target choice — the engine's pathfinder
(`engine.movement.path_to`) is already speed-aware so a one-hex direction
maps cleanly to a `MoveOrder(target_hex=neighbor)`. STRIKE picks the
nearest visible enemy via `engine.resolve._is_visible` semantics, which
costs ~O(units²) per step but is fine for 16 units.

**Tradeoff vs full encoding:** giving the agent any-hex MOVE target would
be `MultiDiscrete([cols*rows]*MAX_UNITS) = [300]*12`, learnable in
principle but huge for v0. The reduced menu is the minimum that still
exercises every order kind in `engine/orders.py`.

**Action masking** is required (illegal moves: water for land, mountains
for amphib, STRIKE with no visible target, CAPTURE off an objective hex).
SB3's MaskablePPO covers this. See §6.

## 3. Observation space sketch

Use a `Dict` obs:

- `terrain` — `Box(uint8, (rows, cols))`, 5 values from `engine.terrain.Terrain`.
  Static, but include it so the policy net can learn terrain effects.
  20×15 = 300 cells. (Pointy-top odd-r per `engine/hex.py:1`; the policy
  doesn't need to know that — it's just a 2D grid.)
- `objective_mask` — `Box(uint8, (rows, cols))`, the 2 objective hexes.
- `units` — `Box(float32, (MAX_UNITS_TOTAL=24, F))` per-unit features:
  `[col/cols, row/rows, hp/max_hp, side, domain_onehot(4),
   speed, sensor, weapon, cost/100, stealth, alive_mask]` — F ≈ 14.
- `bases` — same shape, 6 slots.
- `fog_self` — `Box(uint8, (rows, cols))`, hexes the agent's side can see,
  built from `_is_visible`-style sensor coverage (already implemented at
  `engine/resolve.py:106`).
- `turn` — `Box(int, (1,))`, normalized.

Total obs ≈ 4×300 + 24×14 + 6×14 = ~1620 floats. Trivial for PPO. The
`scripts/dump_state.py` helper proves the state is fully serializable; it
doesn't compute fog, but `_is_visible` is reusable. **Important:** for fog
of war we'd be cheating to give the agent enemy-unit features it can't
see — gate the `units` slots for the opposing side by visibility (zero out
features when not visible).

## 4. What's missing for RL

- **Turn cadence:** confirmed synchronous. `engine/orders.py:1-8` docstring
  says "both sides queue simultaneously"; `resolve_turn` takes both order
  lists at once. The server enforces this with a lock-then-resolve pattern
  (`server/main.py:451`). For PPO this is fine — `EngineEnv.step()` is one
  full turn (agent + opponent both act, atomically). Single-agent PPO with
  a frozen scripted opponent is the standard reduction.
- **Opponent:** none exists. `server/main.py` has no AI red — both sides
  are user-driven via REST. Grep across the repo finds no `scripted_red`,
  `bot`, or red-policy module. **You will write this.** Cheap v0: per
  enemy unit, MOVE one hex toward the nearest visible blue unit, else
  STRIKE if in range, else HOLD. Reuses `path_to` and `_is_visible`.
  rl_alt has a `baseline.py` with a similar idea — port the heuristic, do
  not import the toy env.
- **Reward signal:** `compute_scores(state)` (`engine/resolve.py:394-419`)
  returns `(blue_score, red_score)` ∈ ~[0, 130]. Per-step reward =
  `Δ(my_score) − Δ(enemy_score)`. Already includes the +5/turn objective
  bonus capped at +30 (the recent commit) at `engine/resolve.py:53-54`.
- **Termination:** the scenario carries `turn_limit=30` (`scenarios/
  strait_n7.yaml:3`, `engine/state.py:116`) but **nothing in the engine or
  server enforces it** — grep confirms zero `turn_limit` references in
  `server/main.py` and only the field declaration in `engine/state.py`.
  `VictoryConfig` exists (`engine/state.py:100`) with `capture_hold_turns`
  / `combat_power_threshold` but is not consulted anywhere in the
  resolver. EngineEnv will need to terminate manually: `done = state.turn
  >= state.turn_limit or one side annihilated`. Trivial, but call it out
  — currently the engine will happily run turn 9999.

## 5. Effort estimate

v0 wrapper running PPO end-to-end (not "winning" — just learning above
random):

- Gym wrapper + obs/action codec: 4–6h
- Action masking + illegal-order filtering: 2–4h
- Scripted red opponent: 2–3h
- Reward + termination: 1h
- SB3 MaskablePPO config + first training run: 2–3h
- Debugging the inevitable resolver-mutation / vec-env / pickling issue: 3–8h

**Low: 14h. Expected: 22h. High: 35h.** The high end assumes one nasty
surprise (most likely: `GameState` is a pydantic model and SB3 vec envs
will deepcopy it per step — see §6).

## 6. Risks

1. **State mutation + vec envs (HIGH).** `resolve_turn` mutates
   `GameState` in place (`engine/resolve.py:429`). pydantic v2 models are
   deepcopyable but per-step deepcopy of the full state for `n_envs=8`
   PPO rollout is the obvious perf cliff. Mitigation: one `GameState` per
   subprocess env, no copy-on-step; reset by reloading the scenario.
2. **Action masking correctness (MED).** Lots of illegal-order branches —
   land-into-water MOVE, STRIKE on invisible target, CAPTURE off-objective,
   OVERWATCH with no weapon. The resolver silently ignores most of these
   (`engine/resolve.py:194,306-312`) so the agent will get zero feedback
   on illegal picks unless we mask. Without masking, training stalls.
3. **No scripted opponent + no terminal condition (MED).** Both have to
   be written before a single PPO rollout works. They're not hard, just
   on the critical path. Determinism itself is a non-risk: `state.seed`
   is honored end-to-end via blake2b (`engine/resolve.py:70-81`); same
   seed + same orders => same events.

(Honorable mention: the engine has 12 platforms × variable
sensor/weapon loadouts. For a fixed scenario this is constant, but if you
want a curriculum across scenarios, the obs encoder must handle a varying
roster. Out of scope for v0.)

## 7. Recommendation

**Build the v0 — narrow cut.** ~22h expected, single scenario, frozen
roster, scripted red.

Minimum viable cut:

- One scenario only (`scenarios/strait_n7.yaml`).
- Frozen roster, no spawning, no base repair.
- Action menu of 11 per-unit choices (§2). **Drop SCOUT and CAPTURE for
  v0** — SCOUT's effect is event-only (no state change, see
  `engine/resolve.py:148`) so it's not learnable from `compute_scores`
  alone, and CAPTURE is just "stand on objective" which the objective
  bonus already incentivizes. That cuts the menu to 9.
- Single agent controls blue; scripted red. No self-play yet.
- MaskablePPO; reward = score delta; episode = 30 turns or annihilation.

If the v0 doesn't show learning above the scripted-baseline within ~1M
steps, the alternative is to keep iterating on the existing toy env in
`rl_alt/` — it's already producing a clean baseline-vs-RL signal and the
real engine adds a lot of surface area (fog, terrain, multi-domain
catalog) that may be more than PPO needs to demo learning. The toy is
not throwaway; the EngineEnv is the bet that the engine's score signal is
rich enough to learn from. Probably yes; worth one week to find out.
