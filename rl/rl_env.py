"""Standalone Gymnasium environment for reward-conditioned wargame training.

This intentionally does not depend on Alex's hex engine. It is a compact
orthogonal-grid abstraction meant to prove the PPO loop, fog of war, scripted
opposition, and reward conditioning before bridging back to the full engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces


MOVE_DELTAS: dict[int, tuple[int, int]] = {
    0: (0, 0),    # hold
    1: (0, -1),   # north
    2: (0, 1),    # south
    3: (-1, 0),   # west
    4: (1, 0),    # east
}

POLICY_STRIKE = 0
POLICY_SURVEIL = 1
POLICY_RETREAT = 2

TERRAIN_OPEN = 0
TERRAIN_ROUGH = 1
TERRAIN_BLOCKED = 2


@dataclass
class RLUnit:
    id: str
    side: str
    kind: str
    x: int
    y: int
    hp: int
    max_hp: int
    value: float
    domain: str
    move: int
    sensor_range: int
    strike_range: int
    can_hit_air: bool
    can_hit_ground: bool
    alive: bool = True


@dataclass
class HVT:
    id: str
    side: str
    x: int
    y: int
    value: float
    alive: bool = True


@dataclass
class CombatEvent:
    attacker: str
    target: str
    hit: bool
    damage: int
    destroyed: bool


class OverwatchEnv(gym.Env):
    """Small reward-conditioned wargame.

    Observation is a single flattened vector:
        6 grid channels * height * width + 4 reward weights.

    Action is:
        MultiDiscrete([5, 3, 5, 3, 5, 3, 2])

    The first six entries are per-blue-unit movement and contact policy for:
        recon drone, strike drone, SAM battery.

    The last entry is the blue SAM radar state:
        0 silent, 1 emit.
    """

    metadata = {"render_modes": ["ansi", "human"], "render_fps": 4}

    def __init__(
        self,
        grid_size: int = 10,
        max_steps: int = 75,
        render_mode: str | None = None,
    ) -> None:
        super().__init__()
        if grid_size < 8:
            raise ValueError("grid_size must be at least 8")
        self.width = grid_size
        self.height = grid_size
        self.max_steps = max_steps
        self.render_mode = render_mode

        self.action_space = spaces.MultiDiscrete([5, 3, 5, 3, 5, 3, 2])
        self.grid_channels = 6
        self.obs_size = self.grid_channels * self.height * self.width + 4
        self.observation_space = spaces.Box(
            low=-1.0,
            high=1.0,
            shape=(self.obs_size,),
            dtype=np.float32,
        )

        self.fixed_weights: np.ndarray | None = None
        self.w_enemy = 0.0
        self.w_own = 0.0
        self.w_info = 0.0
        self.w_time = 0.0

        self.t = 0
        self.terrain = np.zeros((self.height, self.width), dtype=np.int8)
        self.units: list[RLUnit] = []
        self.hvts: list[HVT] = []
        self.blue_visible = np.zeros((self.height, self.width), dtype=bool)
        self.red_visible = np.zeros((self.height, self.width), dtype=bool)
        self.blue_seen_ever = np.zeros((self.height, self.width), dtype=bool)
        self.known_enemy_hvts = np.zeros((self.height, self.width), dtype=bool)
        self.blue_sam_emit = True
        self.red_sam_emit = True
        self.last_events: list[CombatEvent] = []
        self.last_blue_policies: dict[str, int] = {}
        self.last_red_policies: dict[str, int] = {}

    def set_reward_weights(self, weights: list[float] | tuple[float, ...] | np.ndarray) -> None:
        """Fix reward weights for evaluation until clear_reward_weights is called."""
        arr = np.asarray(weights, dtype=np.float32)
        if arr.shape != (4,):
            raise ValueError("weights must be [w_enemy, w_own, w_info, w_time]")
        self.fixed_weights = np.clip(arr, [-1.0, -1.0, 0.0, -1.0], [1.0, 0.0, 1.0, 0.0])

    def clear_reward_weights(self) -> None:
        self.fixed_weights = None

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        if options and "reward_weights" in options:
            self.set_reward_weights(options["reward_weights"])

        self.t = 0
        self.last_events = []
        self.blue_sam_emit = True
        self.red_sam_emit = True

        self._build_terrain()
        self._spawn_units()
        self._spawn_hvts()
        self._set_episode_weights()

        self.last_blue_policies = {u.id: POLICY_SURVEIL for u in self.units if u.side == "blue"}
        self.last_red_policies = {u.id: POLICY_SURVEIL for u in self.units if u.side == "red"}
        self._update_visibility(self.last_blue_policies, self.last_red_policies)
        self.blue_seen_ever = self.blue_visible.copy()
        self._update_known_enemy_hvts()

        return self._get_obs(), self._info()

    def step(self, action: np.ndarray | list[int]) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        action_arr = np.asarray(action, dtype=np.int64)
        if action_arr.shape != (7,):
            raise ValueError("action must have shape (7,)")

        prev_visible = self.blue_seen_ever.copy()
        prev_blue_value = self._side_value("blue")
        prev_red_value = self._side_value("red")

        blue_moves, blue_policies = self._decode_blue_action(action_arr)
        red_moves, red_policies = self._apply_red_scripted_opponent()

        self._apply_moves("blue", blue_moves, blue_policies)
        self._apply_moves("red", red_moves, red_policies)

        self._update_visibility(blue_policies, red_policies)
        self.blue_seen_ever |= self.blue_visible
        self._update_known_enemy_hvts()

        self.last_events = self._resolve_combat(blue_policies, red_policies)
        self._remove_dead()

        enemy_value_destroyed = prev_red_value - self._side_value("red")
        own_value_lost = prev_blue_value - self._side_value("blue")
        new_cells_revealed = float(np.logical_and(self.blue_seen_ever, ~prev_visible).sum())
        reward = (
            self.w_enemy * enemy_value_destroyed
            + self.w_own * own_value_lost
            + self.w_info * new_cells_revealed
            + self.w_time * 1.0
        )

        self.t += 1
        terminated = self._is_terminal()
        truncated = self.t >= self.max_steps
        info = self._info()
        info.update(
            {
                "enemy_value_destroyed": float(enemy_value_destroyed),
                "own_value_lost": float(own_value_lost),
                "new_cells_revealed": float(new_cells_revealed),
                "combat_events": [event.__dict__ for event in self.last_events],
            }
        )

        obs = self._get_obs()
        if self.render_mode == "human":
            print(self._render_text())
        return obs, float(reward), terminated, truncated, info

    def render(self) -> str | None:
        out = self._render_text()
        if self.render_mode == "human":
            print(out)
            return None
        return out

    def _render_text(self) -> str:
        lines = [
            (
                f"t={self.t} weights=[{self.w_enemy:.2f}, {self.w_own:.2f}, "
                f"{self.w_info:.2f}, {self.w_time:.2f}] blue_value={self._side_value('blue'):.1f} "
                f"red_value={self._side_value('red'):.1f} radar={'emit' if self.blue_sam_emit else 'silent'}"
            )
        ]
        for y in range(self.height):
            chars: list[str] = []
            for x in range(self.width):
                blue_unit = self._unit_at(x, y, "blue")
                own_hvt = self._hvt_at(x, y, "blue")
                if blue_unit is not None:
                    chars.append(self._unit_symbol(blue_unit).upper())
                    continue
                if own_hvt is not None:
                    chars.append("B")
                    continue
                if not self.blue_visible[y, x]:
                    chars.append("?")
                    continue
                red_unit = self._unit_at(x, y, "red")
                red_hvt = self._hvt_at(x, y, "red")
                if red_unit is not None:
                    chars.append(self._unit_symbol(red_unit).lower())
                elif red_hvt is not None and self.known_enemy_hvts[y, x]:
                    chars.append("h")
                else:
                    chars.append(self._terrain_symbol(x, y))
            lines.append("".join(chars))
        if self.last_events:
            lines.append(
                "events: "
                + ", ".join(
                    f"{ev.attacker}->{ev.target}:{'hit' if ev.hit else 'miss'}"
                    for ev in self.last_events[-4:]
                )
            )
        return "\n".join(lines)

    def _build_terrain(self) -> None:
        self.terrain = np.zeros((self.height, self.width), dtype=np.int8)
        rough = [
            (3, 2), (4, 2), (6, 2),
            (2, 4), (3, 4), (6, 5),
            (7, 5), (4, 7), (5, 7),
        ]
        blocked = [(5, 3), (5, 4), (1, 6), (8, 6)]
        for x, y in rough:
            if self._in_bounds(x, y):
                self.terrain[y, x] = TERRAIN_ROUGH
        for x, y in blocked:
            if self._in_bounds(x, y):
                self.terrain[y, x] = TERRAIN_BLOCKED

    def _spawn_units(self) -> None:
        self.units = [
            RLUnit("blue-recon", "blue", "recon", 1, 8, 1, 1, 5.0, "air", 2, 4, 0, False, False),
            RLUnit("blue-strike", "blue", "strike", 2, 8, 2, 2, 10.0, "air", 2, 3, 3, True, True),
            RLUnit("blue-sam", "blue", "sam", 1, 9, 3, 3, 12.0, "ground", 1, 4, 4, True, False),
            RLUnit("red-recon", "red", "recon", self.width - 2, 1, 1, 1, 5.0, "air", 2, 4, 0, False, False),
            RLUnit("red-strike", "red", "strike", self.width - 3, 1, 2, 2, 10.0, "air", 2, 3, 3, True, True),
            RLUnit("red-sam", "red", "sam", self.width - 2, 0, 3, 3, 12.0, "ground", 1, 4, 4, True, False),
        ]

    def _spawn_hvts(self) -> None:
        blue_candidates = [(0, 7), (1, 7), (3, 9), (2, 6)]
        red_candidates = [
            (self.width - 1, 2),
            (self.width - 2, 2),
            (self.width - 4, 0),
            (self.width - 3, 3),
        ]
        blue_pick = self.np_random.choice(len(blue_candidates), size=2, replace=False)
        red_pick = self.np_random.choice(len(red_candidates), size=2, replace=False)
        values = [8.0, 12.0]
        self.hvts = []
        for idx, pick in enumerate(blue_pick):
            x, y = blue_candidates[int(pick)]
            self.hvts.append(HVT(f"blue-hvt-{idx}", "blue", x, y, values[idx]))
        for idx, pick in enumerate(red_pick):
            x, y = red_candidates[int(pick)]
            self.hvts.append(HVT(f"red-hvt-{idx}", "red", x, y, values[idx]))
        self.known_enemy_hvts = np.zeros((self.height, self.width), dtype=bool)

    def _set_episode_weights(self) -> None:
        if self.fixed_weights is not None:
            weights = self.fixed_weights
        else:
            weights = np.array(
                [
                    self.np_random.uniform(0.2, 1.0),
                    self.np_random.uniform(-1.0, -0.1),
                    self.np_random.uniform(0.0, 0.5),
                    self.np_random.uniform(-1.0, 0.0),
                ],
                dtype=np.float32,
            )
        self.w_enemy, self.w_own, self.w_info, self.w_time = [float(v) for v in weights]

    def _decode_blue_action(self, action: np.ndarray) -> tuple[dict[str, int], dict[str, int]]:
        blue_units = [u for u in self.units if u.side == "blue" and u.alive]
        by_kind = {u.kind: u for u in blue_units}
        ordered_ids = [
            by_kind[kind].id
            for kind in ("recon", "strike", "sam")
            if kind in by_kind
        ]
        moves: dict[str, int] = {}
        policies: dict[str, int] = {}
        for idx, unit_id in enumerate(ordered_ids):
            moves[unit_id] = int(action[idx * 2])
            policies[unit_id] = int(action[idx * 2 + 1])
        self.blue_sam_emit = bool(action[6])
        self.last_blue_policies = policies
        return moves, policies

    def _apply_red_scripted_opponent(self) -> tuple[dict[str, int], dict[str, int]]:
        moves: dict[str, int] = {}
        policies: dict[str, int] = {}
        red_units = [u for u in self.units if u.side == "red" and u.alive]
        visible_blue = self._visible_targets_for("red", include_hvts=False)

        for unit in red_units:
            if unit.kind == "sam":
                moves[unit.id] = 0
                policies[unit.id] = POLICY_STRIKE
                continue
            if unit.kind == "recon":
                moves[unit.id] = int(self.np_random.choice([0, 1, 2, 3, 4]))
                policies[unit.id] = POLICY_SURVEIL
                continue
            target = self._nearest_target(unit, visible_blue)
            policies[unit.id] = POLICY_STRIKE if target is not None else POLICY_SURVEIL
            if target is None:
                moves[unit.id] = int(self.np_random.choice([0, 1, 2, 3, 4]))
            elif self._manhattan(unit.x, unit.y, target.x, target.y) <= unit.strike_range:
                moves[unit.id] = 0
            else:
                moves[unit.id] = self._direction_toward(unit.x, unit.y, target.x, target.y)
        self.last_red_policies = policies
        return moves, policies

    def _apply_moves(self, side: str, moves: dict[str, int], policies: dict[str, int]) -> None:
        occupied = {(u.x, u.y) for u in self.units if u.alive}
        for unit in [u for u in self.units if u.side == side and u.alive]:
            move_code = moves.get(unit.id, 0)
            dx, dy = MOVE_DELTAS.get(move_code, (0, 0))
            steps = unit.move
            if policies.get(unit.id) == POLICY_RETREAT:
                threat = self._nearest_target(
                    unit,
                    self._visible_targets_for(unit.side, include_hvts=False),
                )
                if threat is not None:
                    dx, dy = self._retreat_delta(unit.x, unit.y, threat.x, threat.y)
                    steps = max(steps, unit.move + 1)
            occupied.discard((unit.x, unit.y))
            for _ in range(steps):
                nx, ny = unit.x + dx, unit.y + dy
                if not self._can_enter(unit, nx, ny, occupied):
                    break
                unit.x, unit.y = nx, ny
            occupied.add((unit.x, unit.y))

    def _update_visibility(self, blue_policies: dict[str, int], red_policies: dict[str, int]) -> None:
        self.blue_visible = self._compute_visibility("blue", blue_policies)
        self.red_visible = self._compute_visibility("red", red_policies)

        red_sam = self._unit_by_id("red-sam")
        if red_sam is not None and red_sam.alive and self.red_sam_emit:
            self.blue_visible[red_sam.y, red_sam.x] = True
        blue_sam = self._unit_by_id("blue-sam")
        if blue_sam is not None and blue_sam.alive and self.blue_sam_emit:
            self.red_visible[blue_sam.y, blue_sam.x] = True

    def _compute_visibility(self, side: str, policies: dict[str, int]) -> np.ndarray:
        visible = np.zeros((self.height, self.width), dtype=bool)
        for unit in self.units:
            if unit.side != side or not unit.alive:
                continue
            rng = self._effective_sensor_range(unit, policies.get(unit.id, POLICY_SURVEIL), side)
            for y in range(self.height):
                for x in range(self.width):
                    if self._manhattan(unit.x, unit.y, x, y) <= rng:
                        visible[y, x] = True
        return visible

    def _effective_sensor_range(self, unit: RLUnit, policy: int, side: str) -> int:
        rng = unit.sensor_range
        if unit.kind == "sam":
            emit = self.blue_sam_emit if side == "blue" else self.red_sam_emit
            rng = unit.sensor_range + 1 if emit else 1
        if policy == POLICY_SURVEIL:
            rng += 2
        if policy == POLICY_RETREAT:
            rng = max(1, rng - 1)
        return max(0, rng)

    def _update_known_enemy_hvts(self) -> None:
        for hvt in self.hvts:
            if hvt.side == "red" and hvt.alive and self.blue_visible[hvt.y, hvt.x]:
                self.known_enemy_hvts[hvt.y, hvt.x] = True

    def _resolve_combat(
        self,
        blue_policies: dict[str, int],
        red_policies: dict[str, int],
    ) -> list[CombatEvent]:
        attacks: list[tuple[RLUnit, RLUnit | HVT, int, float]] = []
        for side, policies in (("blue", blue_policies), ("red", red_policies)):
            for attacker in [u for u in self.units if u.side == side and u.alive]:
                if policies.get(attacker.id) != POLICY_STRIKE:
                    continue
                if attacker.kind == "sam":
                    emit = self.blue_sam_emit if side == "blue" else self.red_sam_emit
                    if not emit:
                        continue
                target = self._select_attack_target(attacker)
                if target is None:
                    continue
                damage, pkill = self._attack_profile(attacker, target)
                attacks.append((attacker, target, damage, pkill))

        events: list[CombatEvent] = []
        pending_damage: dict[str, int] = {}
        for attacker, target, damage, pkill in attacks:
            evade_policy = (
                self.last_blue_policies.get(target.id)
                if isinstance(target, RLUnit) and target.side == "blue"
                else self.last_red_policies.get(target.id)
                if isinstance(target, RLUnit)
                else None
            )
            if evade_policy == POLICY_RETREAT:
                pkill *= 0.7
            hit = bool(self.np_random.random() < pkill)
            actual_damage = damage if hit else 0
            if hit:
                pending_damage[target.id] = pending_damage.get(target.id, 0) + actual_damage
            destroyed = hit and self._target_hp(target) - pending_damage.get(target.id, 0) <= 0
            events.append(CombatEvent(attacker.id, target.id, hit, actual_damage, destroyed))

        for target_id, damage in pending_damage.items():
            unit = self._unit_by_id(target_id)
            if unit is not None:
                unit.hp = max(0, unit.hp - damage)
                if unit.hp <= 0:
                    unit.alive = False
                continue
            hvt = self._hvt_by_id(target_id)
            if hvt is not None and damage > 0:
                hvt.alive = False
        return events

    def _select_attack_target(self, attacker: RLUnit) -> RLUnit | HVT | None:
        candidates = self._visible_targets_for(attacker.side, include_hvts=True)
        in_range: list[RLUnit | HVT] = []
        for target in candidates:
            if self._manhattan(attacker.x, attacker.y, target.x, target.y) > attacker.strike_range:
                continue
            if isinstance(target, RLUnit):
                if target.domain == "air" and not attacker.can_hit_air:
                    continue
                if target.domain == "ground" and not attacker.can_hit_ground:
                    continue
            elif not attacker.can_hit_ground:
                continue
            in_range.append(target)
        if not in_range:
            return None
        return max(
            in_range,
            key=lambda target: (
                float(target.value),
                -self._manhattan(attacker.x, attacker.y, target.x, target.y),
            ),
        )

    def _attack_profile(self, attacker: RLUnit, target: RLUnit | HVT) -> tuple[int, float]:
        if attacker.kind == "sam":
            return 2, 0.75
        if isinstance(target, HVT):
            return 1, 0.8
        if target.domain == "ground":
            return 1, 0.65
        return 1, 0.55

    def _remove_dead(self) -> None:
        for unit in self.units:
            if unit.hp <= 0:
                unit.alive = False
        for hvt in self.hvts:
            if not hvt.alive:
                self.known_enemy_hvts[hvt.y, hvt.x] = False

    def _visible_targets_for(self, side: str, include_hvts: bool) -> list[RLUnit | HVT]:
        visible = self.blue_visible if side == "blue" else self.red_visible
        enemy_side = "red" if side == "blue" else "blue"
        targets: list[RLUnit | HVT] = [
            u for u in self.units
            if u.side == enemy_side and u.alive and visible[u.y, u.x]
        ]
        if include_hvts:
            targets.extend(
                h for h in self.hvts
                if h.side == enemy_side and h.alive and visible[h.y, h.x]
            )
        return targets

    def _get_obs(self) -> np.ndarray:
        grid = np.zeros((self.grid_channels, self.height, self.width), dtype=np.float32)
        grid[0] = self.terrain.astype(np.float32) / float(TERRAIN_BLOCKED)
        for unit in self.units:
            if not unit.alive:
                continue
            if unit.side == "blue":
                grid[1, unit.y, unit.x] = self._unit_encoding(unit)
            elif self.blue_visible[unit.y, unit.x]:
                grid[2, unit.y, unit.x] = self._unit_encoding(unit)
        grid[3] = self.blue_visible.astype(np.float32)
        for hvt in self.hvts:
            if not hvt.alive:
                continue
            if hvt.side == "blue":
                grid[4, hvt.y, hvt.x] = min(1.0, hvt.value / 12.0)
            elif self.known_enemy_hvts[hvt.y, hvt.x]:
                grid[5, hvt.y, hvt.x] = min(1.0, hvt.value / 12.0)
        weights = np.array([self.w_enemy, self.w_own, self.w_info, self.w_time], dtype=np.float32)
        return np.concatenate([grid.ravel(), weights]).astype(np.float32)

    def _info(self) -> dict[str, Any]:
        return {
            "step": self.t,
            "weights": [self.w_enemy, self.w_own, self.w_info, self.w_time],
            "blue_value": float(self._side_value("blue")),
            "red_value": float(self._side_value("red")),
            "blue_units_alive": sum(1 for u in self.units if u.side == "blue" and u.alive),
            "red_units_alive": sum(1 for u in self.units if u.side == "red" and u.alive),
            "blue_sam_emit": self.blue_sam_emit,
        }

    def _is_terminal(self) -> bool:
        blue_units_alive = any(u.side == "blue" and u.alive for u in self.units)
        red_assets_alive = any(u.side == "red" and u.alive for u in self.units) or any(
            h.side == "red" and h.alive for h in self.hvts
        )
        blue_hvts_alive = any(h.side == "blue" and h.alive for h in self.hvts)
        return (not blue_units_alive) or (not red_assets_alive) or (not blue_hvts_alive)

    def _side_value(self, side: str) -> float:
        unit_value = sum(u.value * (u.hp / u.max_hp) for u in self.units if u.side == side and u.alive)
        hvt_value = sum(h.value for h in self.hvts if h.side == side and h.alive)
        return float(unit_value + hvt_value)

    def _can_enter(self, unit: RLUnit, x: int, y: int, occupied: set[tuple[int, int]]) -> bool:
        if not self._in_bounds(x, y):
            return False
        if (x, y) in occupied:
            return False
        if unit.domain == "ground" and self.terrain[y, x] == TERRAIN_BLOCKED:
            return False
        return True

    def _nearest_target(self, unit: RLUnit, targets: list[RLUnit | HVT]) -> RLUnit | HVT | None:
        if not targets:
            return None
        return min(targets, key=lambda target: self._manhattan(unit.x, unit.y, target.x, target.y))

    def _direction_toward(self, x: int, y: int, tx: int, ty: int) -> int:
        dx = tx - x
        dy = ty - y
        if abs(dx) >= abs(dy) and dx != 0:
            return 4 if dx > 0 else 3
        if dy != 0:
            return 2 if dy > 0 else 1
        return 0

    def _retreat_delta(self, x: int, y: int, tx: int, ty: int) -> tuple[int, int]:
        dx = x - tx
        dy = y - ty
        if abs(dx) >= abs(dy) and dx != 0:
            return (1 if dx > 0 else -1, 0)
        if dy != 0:
            return (0, 1 if dy > 0 else -1)
        return (0, 0)

    def _unit_encoding(self, unit: RLUnit) -> float:
        base = {"recon": 0.35, "strike": 0.7, "sam": 1.0}[unit.kind]
        return base * (unit.hp / unit.max_hp)

    def _unit_symbol(self, unit: RLUnit) -> str:
        return {"recon": "r", "strike": "d", "sam": "s"}[unit.kind]

    def _terrain_symbol(self, x: int, y: int) -> str:
        terrain = self.terrain[y, x]
        if terrain == TERRAIN_BLOCKED:
            return "#"
        if terrain == TERRAIN_ROUGH:
            return "^"
        return "."

    def _unit_at(self, x: int, y: int, side: str | None = None) -> RLUnit | None:
        for unit in self.units:
            if unit.alive and unit.x == x and unit.y == y and (side is None or unit.side == side):
                return unit
        return None

    def _hvt_at(self, x: int, y: int, side: str | None = None) -> HVT | None:
        for hvt in self.hvts:
            if hvt.alive and hvt.x == x and hvt.y == y and (side is None or hvt.side == side):
                return hvt
        return None

    def _unit_by_id(self, unit_id: str) -> RLUnit | None:
        for unit in self.units:
            if unit.id == unit_id:
                return unit
        return None

    def _hvt_by_id(self, hvt_id: str) -> HVT | None:
        for hvt in self.hvts:
            if hvt.id == hvt_id:
                return hvt
        return None

    def _target_hp(self, target: RLUnit | HVT) -> int:
        return target.hp if isinstance(target, RLUnit) else 1

    def _in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def _manhattan(self, ax: int, ay: int, bx: int, by: int) -> int:
        return abs(ax - bx) + abs(ay - by)
