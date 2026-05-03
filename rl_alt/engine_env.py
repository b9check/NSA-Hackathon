"""Gymnasium environment wrapping the wargame engine (v0).

Single-agent: the agent controls `agent_side` (default "blue"); the opposing
side is driven by `rl_alt.scripted_red.choose_orders`. One env step = one
full engine turn (both sides resolved atomically by `resolve_turn`).

Action space:    MultiDiscrete([9] * MAX_UNITS_PER_SIDE)
Observation:     Dict(terrain, units, bases, fog_self, turn, hp_pct)
Reward:          Δ(hp_pct_self) − Δ(hp_pct_opp) per step + terminal ±1 from winner.
"""
from __future__ import annotations

from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from engine.hex import Hex, distance, in_bounds, neighbors
from engine.orders import (
    HoldOrder,
    MoveOrder,
    Order,
    OverwatchOrder,
    StrikeOrder,
)
from engine.resolve import _is_visible, compute_scores, resolve_turn
from engine.scenario import load_scenario
from engine.state import GameState
from engine.terrain import Terrain, can_traverse

from rl_alt import scripted_red


MAX_UNITS_PER_SIDE = 12
N_ACTIONS = 9
UNIT_FEATURES = 13  # see _encode_unit (3 domain lanes, no amphib)
DOMAINS = ("land", "air", "sea")

# Action codes
A_HOLD = 0
A_OVERWATCH = 1
A_MOVE_NE = 2
A_MOVE_E = 3
A_MOVE_SE = 4
A_MOVE_SW = 5
A_MOVE_W = 6
A_MOVE_NW = 7
A_STRIKE = 8

# odd-r neighbor deltas in the order [NE, E, SE, SW, W, NW]
# row-parity-dependent
_DELTAS_EVEN = [(0, -1), (+1, 0), (0, +1), (-1, +1), (-1, 0), (-1, -1)]
_DELTAS_ODD = [(+1, -1), (+1, 0), (+1, +1), (0, +1), (-1, 0), (0, -1)]


def _move_delta(row: int, action: int) -> tuple[int, int]:
    """Return (dc, dr) for action in [A_MOVE_NE..A_MOVE_NW]."""
    deltas = _DELTAS_ODD if (row & 1) else _DELTAS_EVEN
    return deltas[action - A_MOVE_NE]


def _terrain_grid(state: GameState) -> dict[tuple[int, int], Terrain]:
    return {(c.col, c.row): c.terrain for c in state.map.cells}


def _encode_unit(
    u, cols: int, rows: int, *, visible: bool = True,
) -> np.ndarray:
    if not visible:
        return np.zeros(UNIT_FEATURES, dtype=np.float32)
    side_val = 0.0 if u.side == "blue" else 1.0
    domain_oh = [0.0, 0.0, 0.0]
    if u.domain in DOMAINS:
        domain_oh[DOMAINS.index(u.domain)] = 1.0
    feats = [
        u.col / max(cols, 1),
        u.row / max(rows, 1),
        u.hp / max(u.max_hp, 1),
        side_val,
        *domain_oh,
        getattr(u, "speed", 0) / 10.0,
        getattr(u, "sensor", 0) / 20.0,
        getattr(u, "weapon", 0) / 20.0,
        getattr(u, "cost", 0) / 100.0,
        1.0 if getattr(u, "stealth", False) else 0.0,
        1.0,  # alive_mask
    ]
    return np.asarray(feats, dtype=np.float32)


class EngineEnv(gym.Env):
    """Gymnasium env wrapping `engine.resolve.resolve_turn`."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        scenario_path: str = "scenarios/strait_n7.yaml",
        agent_side: str = "blue",
        max_turns: int | None = None,
    ) -> None:
        super().__init__()
        self.scenario_path = scenario_path
        self.agent_side = agent_side
        self.opponent_side = "red" if agent_side == "blue" else "blue"
        self._max_turns_override = max_turns

        # Probe the scenario to size obs/action spaces.
        probe = load_scenario(scenario_path)
        self.cols = probe.map.cols
        self.rows = probe.map.rows
        # Use victory.turn_cap as the canonical horizon; engine drives it.
        self.turn_cap = probe.victory.turn_cap
        self.max_turns = max_turns if max_turns is not None else self.turn_cap
        self.n_bases = max(len(probe.bases), 1)

        self.action_space = spaces.MultiDiscrete([N_ACTIONS] * MAX_UNITS_PER_SIDE)
        n_unit_slots = MAX_UNITS_PER_SIDE * 2
        self.observation_space = spaces.Dict({
            "terrain": spaces.Box(low=0, high=4, shape=(self.rows, self.cols), dtype=np.uint8),
            "units": spaces.Box(low=-1.0, high=10.0, shape=(n_unit_slots, UNIT_FEATURES), dtype=np.float32),
            "bases": spaces.Box(low=-1.0, high=10.0, shape=(self.n_bases, UNIT_FEATURES), dtype=np.float32),
            "fog_self": spaces.Box(low=0, high=1, shape=(self.rows, self.cols), dtype=np.uint8),
            "turn": spaces.Box(low=0.0, high=1.0, shape=(1,), dtype=np.float32),
            "hp_pct": spaces.Box(low=0.0, high=1.0, shape=(2,), dtype=np.float32),
        })

        self.state: GameState | None = None
        self._prev_hp_self: float = 1.0
        self._prev_hp_opp: float = 1.0
        self._terrain_index = {t: i for i, t in enumerate(Terrain)}

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------
    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        super().reset(seed=seed)
        self.state = load_scenario(self.scenario_path)
        if seed is not None:
            self.state.seed = int(seed)
        self._prev_hp_self = self._hp_pct(self.agent_side)
        self._prev_hp_opp = self._hp_pct(self.opponent_side)
        return self._build_obs(), self._build_info(events=[])

    def step(
        self, action: np.ndarray | list[int],
    ) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        assert self.state is not None, "call reset() first"
        action = np.asarray(action, dtype=np.int64).reshape(-1)

        agent_units = [u for u in self.state.units if u.side == self.agent_side]
        agent_orders = self._decode_actions(action, agent_units)
        opp_orders = scripted_red.choose_orders(self.state, side=self.opponent_side)

        if self.agent_side == "blue":
            blue_orders, red_orders = agent_orders, opp_orders
        else:
            blue_orders, red_orders = opp_orders, agent_orders

        events = resolve_turn(self.state, blue_orders, red_orders)

        cur_self = self._hp_pct(self.agent_side)
        cur_opp = self._hp_pct(self.opponent_side)
        d_self = cur_self - self._prev_hp_self
        d_opp = cur_opp - self._prev_hp_opp
        reward = float(d_self - d_opp)
        self._prev_hp_self, self._prev_hp_opp = cur_self, cur_opp

        # termination: engine-driven via state.winner
        winner = self.state.winner
        terminated = winner is not None
        if terminated:
            if winner == self.agent_side:
                reward += 1.0
            elif winner == self.opponent_side:
                reward -= 1.0
            # draw -> 0
        truncated = (
            (not terminated)
            and self.state.turn >= self.turn_cap
        )

        obs = self._build_obs()
        info = self._build_info(events=events)
        return obs, reward, terminated, truncated, info

    def render(self):
        return None

    # ------------------------------------------------------------------
    # Action decoding
    # ------------------------------------------------------------------
    def _decode_actions(
        self, action: np.ndarray, agent_units: list,
    ) -> list[Order]:
        assert self.state is not None
        cols, rows = self.cols, self.rows
        orders: list[Order] = []
        for slot in range(MAX_UNITS_PER_SIDE):
            if slot >= len(agent_units):
                break
            unit = agent_units[slot]
            if unit.hp <= 0:
                continue
            a = int(action[slot]) if slot < len(action) else A_HOLD
            if a == A_HOLD:
                orders.append(HoldOrder(unit_id=unit.id))
            elif a == A_OVERWATCH:
                if unit.weapon > 0:
                    orders.append(OverwatchOrder(unit_id=unit.id))
                else:
                    orders.append(HoldOrder(unit_id=unit.id))
            elif A_MOVE_NE <= a <= A_MOVE_NW:
                dc, dr = _move_delta(unit.row, a)
                tc, tr = unit.col + dc, unit.row + dr
                if not in_bounds(Hex(tc, tr), cols, rows):
                    orders.append(HoldOrder(unit_id=unit.id))
                    continue
                orders.append(MoveOrder(unit_id=unit.id, target_hex=(tc, tr)))
            elif a == A_STRIKE:
                target = self._nearest_visible_enemy(unit)
                if target is None or unit.weapon <= 0:
                    orders.append(HoldOrder(unit_id=unit.id))
                else:
                    orders.append(StrikeOrder(
                        unit_id=unit.id,
                        target_hex=(target.col, target.row),
                    ))
            else:
                orders.append(HoldOrder(unit_id=unit.id))
        return orders

    def _nearest_visible_enemy(self, unit):
        assert self.state is not None
        candidates = []
        for e in self.state.units:
            if e.side == unit.side or e.hp <= 0:
                continue
            if not _is_visible(unit.side, e, self.state):
                continue
            candidates.append(e)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda e: distance(Hex(unit.col, unit.row), Hex(e.col, e.row)),
        )

    # ------------------------------------------------------------------
    # Action masking
    # ------------------------------------------------------------------
    def valid_action_mask(self) -> list[np.ndarray]:
        """Return list of 12 bool arrays of length 9, one per unit slot."""
        assert self.state is not None
        cols, rows = self.cols, self.rows
        terrain_grid = _terrain_grid(self.state)
        agent_units = [u for u in self.state.units if u.side == self.agent_side]
        masks: list[np.ndarray] = []
        for slot in range(MAX_UNITS_PER_SIDE):
            mask = np.zeros(N_ACTIONS, dtype=bool)
            mask[A_HOLD] = True
            if slot >= len(agent_units):
                masks.append(mask)
                continue
            unit = agent_units[slot]
            if unit.hp <= 0:
                masks.append(mask)
                continue
            # OVERWATCH legal iff weapon
            if unit.weapon > 0:
                mask[A_OVERWATCH] = True
            # MOVE_X
            for a in range(A_MOVE_NE, A_MOVE_NW + 1):
                dc, dr = _move_delta(unit.row, a)
                tc, tr = unit.col + dc, unit.row + dr
                if not in_bounds(Hex(tc, tr), cols, rows):
                    continue
                terr = terrain_grid.get((tc, tr))
                if terr is None or not can_traverse(unit.domain, terr):
                    continue
                mask[a] = True
            # STRIKE
            if unit.weapon > 0:
                tgt = self._nearest_visible_enemy(unit)
                if tgt is not None and distance(
                    Hex(unit.col, unit.row), Hex(tgt.col, tgt.row),
                ) <= unit.weapon:
                    mask[A_STRIKE] = True
            masks.append(mask)
        return masks

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------
    def _build_obs(self) -> dict[str, np.ndarray]:
        assert self.state is not None
        rows, cols = self.rows, self.cols

        terrain = np.zeros((rows, cols), dtype=np.uint8)
        for c in self.state.map.cells:
            terrain[c.row, c.col] = self._terrain_index.get(c.terrain, 0)

        # fog: hexes visible to agent_side
        fog = np.zeros((rows, cols), dtype=np.uint8)
        # build sensor sources once
        sources: list[tuple[int, int, int]] = []
        for u in self.state.units:
            if u.side == self.agent_side:
                sources.append((u.col, u.row, u.sensor))
        for b in self.state.bases:
            if b.side == self.agent_side:
                sources.append((b.col, b.row, b.sensor))
        if sources:
            for r in range(rows):
                for c in range(cols):
                    for sc, sr, sn in sources:
                        if distance(Hex(c, r), Hex(sc, sr)) <= sn:
                            fog[r, c] = 1
                            break

        # units: agent first 12, then opponent 12
        n_slots = MAX_UNITS_PER_SIDE * 2
        units_arr = np.zeros((n_slots, UNIT_FEATURES), dtype=np.float32)
        agent_units = [u for u in self.state.units if u.side == self.agent_side]
        opp_units = [u for u in self.state.units if u.side == self.opponent_side]
        for i, u in enumerate(agent_units[:MAX_UNITS_PER_SIDE]):
            units_arr[i] = _encode_unit(u, cols, rows, visible=True)
        for j, u in enumerate(opp_units[:MAX_UNITS_PER_SIDE]):
            visible = _is_visible(self.agent_side, u, self.state)
            units_arr[MAX_UNITS_PER_SIDE + j] = _encode_unit(
                u, cols, rows, visible=visible,
            )

        bases_arr = np.zeros((self.n_bases, UNIT_FEATURES), dtype=np.float32)
        for i, b in enumerate(self.state.bases[:self.n_bases]):
            visible = (
                b.side == self.agent_side
                or _is_visible(self.agent_side, b, self.state)
            )
            bases_arr[i] = _encode_unit(b, cols, rows, visible=visible)

        turn_arr = np.asarray(
            [self.state.turn / max(self.turn_cap, 1)], dtype=np.float32,
        )
        hp_pct = np.asarray(
            [self._hp_pct(self.agent_side), self._hp_pct(self.opponent_side)],
            dtype=np.float32,
        )

        return {
            "terrain": terrain,
            "units": units_arr,
            "bases": bases_arr,
            "fog_self": fog,
            "turn": turn_arr,
            "hp_pct": hp_pct,
        }

    def _hp_pct(self, side: str) -> float:
        assert self.state is not None
        denom = self.state.starting_hp.get(side, 0) if self.state.starting_hp else 0
        if denom <= 0:
            return 0.0
        cur = sum(u.hp for u in self.state.units if u.side == side and u.hp > 0)
        cur += sum(b.hp for b in self.state.bases if b.side == side and b.hp > 0)
        return float(cur) / float(denom)

    def _build_info(self, events: list) -> dict[str, Any]:
        assert self.state is not None
        blue_s, red_s = compute_scores(self.state)
        evs = []
        for e in events:
            try:
                evs.append(e.model_dump())
            except Exception:
                try:
                    evs.append(e.dict())
                except Exception:
                    evs.append(repr(e))
        return {
            "events": evs,
            "blue_score": blue_s,
            "red_score": red_s,
            "turn": self.state.turn,
        }
