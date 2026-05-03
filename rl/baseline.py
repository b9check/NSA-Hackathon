"""Reward-conditioned scripted baseline for the standalone RL environment.

This is not meant to be clever. It is a no-training comparator that proves the
dummy environment can express different commander intents before spending more
PPO compute.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    from rl.rl_env import HVT, RLUnit, OverwatchEnv
except ModuleNotFoundError:  # Allows `python rl/eval.py` from repo root.
    from rl_env import HVT, RLUnit, OverwatchEnv


MOVE_HOLD = 0
MOVE_NORTH = 1
MOVE_SOUTH = 2
MOVE_WEST = 3
MOVE_EAST = 4

POLICY_STRIKE = 0
POLICY_SURVEIL = 1
POLICY_RETREAT = 2

UNIT_SLOTS = {"recon": 0, "strike": 1, "sam": 2}


@dataclass(frozen=True)
class Intent:
    name: str
    enemy: float
    own: float
    info: float
    time: float


class ScriptedIntentPolicy:
    """Hand-written policy driven by the four reward weights."""

    def predict(self, env: OverwatchEnv) -> np.ndarray:
        intent = self._intent(env)
        if intent.name == "defensive":
            return self._defensive(env)
        if intent.name == "recon-heavy":
            return self._recon_heavy(env)
        return self._offensive(env)

    def _intent(self, env: OverwatchEnv) -> Intent:
        weights = Intent(
            name="",
            enemy=float(env.w_enemy),
            own=float(env.w_own),
            info=float(env.w_info),
            time=float(env.w_time),
        )
        if weights.info >= 0.75 or weights.info > weights.enemy:
            return Intent("recon-heavy", weights.enemy, weights.own, weights.info, weights.time)
        if abs(weights.own) >= 0.75 and abs(weights.own) >= weights.enemy:
            return Intent("defensive", weights.enemy, weights.own, weights.info, weights.time)
        return Intent("offensive", weights.enemy, weights.own, weights.info, weights.time)

    def _offensive(self, env: OverwatchEnv) -> np.ndarray:
        action = self._default_action(radar_emit=True)
        visible = self._visible_enemies(env, include_hvts=True)
        known_hvts = self._known_enemy_hvts(env)
        red_hvt_anchor = self._enemy_hvt_anchor(env)

        recon = self._blue_unit(env, "recon")
        if recon is not None:
            target = self._nearest(recon, known_hvts or visible) or red_hvt_anchor
            move = self._toward(recon, target) if target is not None else MOVE_EAST
            self._set(action, "recon", move, POLICY_SURVEIL)

        strike = self._blue_unit(env, "strike")
        if strike is not None:
            target = self._highest_value(strike, visible or known_hvts) or red_hvt_anchor
            in_range = target is not None and self._dist(strike, target) <= strike.strike_range
            move = MOVE_HOLD if in_range else self._toward(strike, target) if target is not None else MOVE_EAST
            self._set(action, "strike", move, POLICY_STRIKE if target is not None else POLICY_SURVEIL)

        sam = self._blue_unit(env, "sam")
        if sam is not None:
            air_target = self._nearest(sam, self._visible_air_threats(env))
            target = air_target or red_hvt_anchor or (env.width - 2, 2)
            move = MOVE_HOLD if air_target is not None and self._dist(sam, air_target) <= sam.strike_range else self._toward(sam, target)
            self._set(action, "sam", move, POLICY_STRIKE if air_target is not None else POLICY_SURVEIL)
            action[6] = 1
        return action

    def _defensive(self, env: OverwatchEnv) -> np.ndarray:
        action = self._default_action(radar_emit=False)
        anchor = self._own_hvt_anchor(env)
        air_threats = self._visible_air_threats(env)
        red_strike = self._visible_enemy_kind(env, "strike")
        pressure = red_strike or self._nearest_point(anchor, air_threats)

        recon = self._blue_unit(env, "recon")
        if recon is not None:
            threat = self._nearest(recon, air_threats)
            if threat is not None and self._dist(recon, threat) <= 4:
                self._set(action, "recon", self._away(recon, threat), POLICY_RETREAT)
            else:
                self._set(action, "recon", self._screen_move(recon, anchor), POLICY_SURVEIL)

        strike = self._blue_unit(env, "strike")
        if strike is not None:
            threat = red_strike or self._nearest(strike, air_threats)
            if threat is not None:
                in_range = self._dist(strike, threat) <= strike.strike_range
                move = MOVE_HOLD if in_range else self._toward(strike, threat)
                self._set(action, "strike", move, POLICY_STRIKE)
            else:
                self._set(action, "strike", self._guard_move(strike, anchor, radius=2), POLICY_SURVEIL)

        sam = self._blue_unit(env, "sam")
        if sam is not None:
            threat = red_strike or self._nearest(sam, air_threats)
            if threat is not None and self._dist(sam, threat) <= sam.strike_range + 1:
                self._set(action, "sam", MOVE_HOLD, POLICY_STRIKE)
                action[6] = 1
            elif pressure is not None and abs(pressure.x - anchor[0]) + abs(pressure.y - anchor[1]) <= 5:
                self._set(action, "sam", self._guard_move(sam, anchor, radius=1), POLICY_SURVEIL)
                action[6] = 1
            else:
                self._set(action, "sam", self._guard_move(sam, anchor, radius=1), POLICY_SURVEIL)
                action[6] = 0
        return action

    def _recon_heavy(self, env: OverwatchEnv) -> np.ndarray:
        action = self._default_action(radar_emit=(env.t % 3 == 0))
        visible_air = self._visible_air_threats(env)
        sensor_hunters = self._visible_air_hunters(env)

        recon = self._blue_unit(env, "recon")
        if recon is not None:
            threat = self._nearest(recon, sensor_hunters)
            if threat is not None and self._dist(recon, threat) <= threat.strike_range + 1:
                self._set(action, "recon", self._away(recon, threat), POLICY_RETREAT)
            else:
                self._set(action, "recon", self._safe_frontier_move(env, recon, lane="north"), POLICY_SURVEIL)

        strike = self._blue_unit(env, "strike")
        if strike is not None:
            visible = self._visible_enemies(env, include_hvts=True)
            target = self._nearest(strike, visible)
            threat = self._nearest(strike, sensor_hunters)
            hvt_target = isinstance(target, HVT)
            if threat is not None and not hvt_target and self._dist(strike, threat) <= threat.strike_range:
                self._set(action, "strike", self._away(strike, threat), POLICY_RETREAT)
            elif target is not None and self._dist(strike, target) <= strike.strike_range:
                self._set(action, "strike", MOVE_HOLD, POLICY_STRIKE)
            else:
                self._set(action, "strike", self._safe_frontier_move(env, strike, lane="south"), POLICY_SURVEIL)

        sam = self._blue_unit(env, "sam")
        if sam is not None:
            air_target = self._nearest(sam, visible_air)
            red_strike = self._enemy_unit(env, "strike")
            anchor = self._own_hvt_anchor(env)
            if air_target is not None and self._dist(sam, air_target) <= sam.strike_range:
                self._set(action, "sam", MOVE_HOLD, POLICY_STRIKE)
                action[6] = 1
            elif (
                red_strike is not None
                and abs(red_strike.x - anchor[0]) + abs(red_strike.y - anchor[1]) <= 5
            ):
                move = MOVE_HOLD if self._dist(sam, red_strike) <= sam.strike_range else self._toward(sam, red_strike)
                self._set(action, "sam", move, POLICY_STRIKE)
                action[6] = 1
            else:
                self._set(action, "sam", self._guard_move(sam, anchor, radius=2), POLICY_SURVEIL)
        return action

    def _default_action(self, radar_emit: bool) -> np.ndarray:
        return np.array(
            [
                MOVE_HOLD, POLICY_SURVEIL,
                MOVE_HOLD, POLICY_SURVEIL,
                MOVE_HOLD, POLICY_SURVEIL,
                1 if radar_emit else 0,
            ],
            dtype=np.int64,
        )

    def _set(self, action: np.ndarray, kind: str, move: int, policy: int) -> None:
        slot = UNIT_SLOTS[kind] * 2
        action[slot] = move
        action[slot + 1] = policy

    def _blue_unit(self, env: OverwatchEnv, kind: str) -> RLUnit | None:
        for unit in env.units:
            if unit.side == "blue" and unit.kind == kind and unit.alive:
                return unit
        return None

    def _visible_enemies(self, env: OverwatchEnv, include_hvts: bool) -> list[RLUnit | HVT]:
        return env._visible_targets_for("blue", include_hvts=include_hvts)

    def _visible_air_threats(self, env: OverwatchEnv) -> list[RLUnit]:
        return [
            target for target in self._visible_enemies(env, include_hvts=False)
            if isinstance(target, RLUnit) and target.domain == "air"
        ]

    def _visible_air_hunters(self, env: OverwatchEnv) -> list[RLUnit]:
        return [
            target for target in self._visible_enemies(env, include_hvts=False)
            if isinstance(target, RLUnit) and target.can_hit_air and target.strike_range > 0
        ]

    def _visible_enemy_kind(self, env: OverwatchEnv, kind: str) -> RLUnit | None:
        targets = [
            target for target in self._visible_enemies(env, include_hvts=False)
            if isinstance(target, RLUnit) and target.kind == kind
        ]
        blue_anchor = self._own_hvt_anchor(env)
        return min(
            targets,
            key=lambda target: abs(target.x - blue_anchor[0]) + abs(target.y - blue_anchor[1]),
            default=None,
        )

    def _enemy_unit(self, env: OverwatchEnv, kind: str) -> RLUnit | None:
        return next(
            (
                unit for unit in env.units
                if unit.side == "red" and unit.kind == kind and unit.alive
            ),
            None,
        )

    def _known_enemy_hvts(self, env: OverwatchEnv) -> list[HVT]:
        return [
            hvt for hvt in env.hvts
            if hvt.side == "red"
            and hvt.alive
            and bool(env.known_enemy_hvts[hvt.y, hvt.x])
        ]

    def _own_hvt_anchor(self, env: OverwatchEnv) -> tuple[int, int]:
        hvts = [hvt for hvt in env.hvts if hvt.side == "blue" and hvt.alive]
        if not hvts:
            return 1, env.height - 2
        x = round(sum(hvt.x for hvt in hvts) / len(hvts))
        y = round(sum(hvt.y for hvt in hvts) / len(hvts))
        return int(x), int(y)

    def _enemy_hvt_anchor(self, env: OverwatchEnv) -> tuple[int, int] | None:
        hvts = [hvt for hvt in env.hvts if hvt.side == "red" and hvt.alive]
        if not hvts:
            return None
        x = round(sum(hvt.x for hvt in hvts) / len(hvts))
        y = round(sum(hvt.y for hvt in hvts) / len(hvts))
        return int(x), int(y)

    def _nearest(self, unit: RLUnit, targets: list[RLUnit | HVT]) -> RLUnit | HVT | None:
        if not targets:
            return None
        return min(targets, key=lambda target: self._dist(unit, target))

    def _nearest_point(self, point: tuple[int, int], targets: list[RLUnit | HVT]) -> RLUnit | HVT | None:
        if not targets:
            return None
        return min(targets, key=lambda target: abs(point[0] - target.x) + abs(point[1] - target.y))

    def _highest_value(self, unit: RLUnit, targets: list[RLUnit | HVT]) -> RLUnit | HVT | None:
        if not targets:
            return None
        return max(targets, key=lambda target: (float(target.value), -self._dist(unit, target)))

    def _dist(self, unit: RLUnit, target: RLUnit | HVT | tuple[int, int]) -> int:
        tx, ty = self._target_xy(target)
        return abs(unit.x - tx) + abs(unit.y - ty)

    def _toward(self, unit: RLUnit, target: RLUnit | HVT | tuple[int, int] | None) -> int:
        if target is None:
            return MOVE_HOLD
        tx, ty = self._target_xy(target)
        dx = tx - unit.x
        dy = ty - unit.y
        if abs(dx) >= abs(dy) and dx != 0:
            return MOVE_EAST if dx > 0 else MOVE_WEST
        if dy != 0:
            return MOVE_SOUTH if dy > 0 else MOVE_NORTH
        return MOVE_HOLD

    def _away(self, unit: RLUnit, target: RLUnit | HVT | tuple[int, int]) -> int:
        tx, ty = self._target_xy(target)
        dx = unit.x - tx
        dy = unit.y - ty
        if abs(dx) >= abs(dy) and dx != 0:
            return MOVE_EAST if dx > 0 else MOVE_WEST
        if dy != 0:
            return MOVE_SOUTH if dy > 0 else MOVE_NORTH
        return MOVE_HOLD

    def _target_xy(self, target: RLUnit | HVT | tuple[int, int]) -> tuple[int, int]:
        if isinstance(target, tuple):
            return target
        return target.x, target.y

    def _guard_move(self, unit: RLUnit, anchor: tuple[int, int], radius: int) -> int:
        dist = abs(unit.x - anchor[0]) + abs(unit.y - anchor[1])
        if dist <= radius:
            return MOVE_HOLD
        return self._toward(unit, anchor)

    def _patrol_move(self, unit: RLUnit, anchor: tuple[int, int]) -> int:
        if unit.x <= anchor[0]:
            return MOVE_EAST
        if unit.y >= anchor[1]:
            return MOVE_NORTH
        return self._guard_move(unit, anchor, radius=3)

    def _screen_move(self, unit: RLUnit, anchor: tuple[int, int]) -> int:
        screen = (min(anchor[0] + 2, 4), max(anchor[1] - 2, 4))
        if abs(unit.x - screen[0]) + abs(unit.y - screen[1]) > 1:
            return self._toward(unit, screen)
        return MOVE_WEST if (unit.x + unit.y) % 2 == 0 else MOVE_EAST

    def _frontier_move(self, env: OverwatchEnv, unit: RLUnit) -> int:
        candidates: list[tuple[float, tuple[int, int]]] = []
        for y in range(env.height):
            for x in range(env.width):
                if env.blue_seen_ever[y, x]:
                    continue
                dist = abs(unit.x - x) + abs(unit.y - y)
                forward_bias = x * 1.5 - y * 0.15
                candidates.append((forward_bias - dist * 0.4, (x, y)))
        if not candidates:
            return MOVE_EAST if unit.x < env.width - 2 else MOVE_NORTH
        _score, target = max(candidates, key=lambda item: item[0])
        return self._toward(unit, target)

    def _safe_frontier_move(self, env: OverwatchEnv, unit: RLUnit, lane: str) -> int:
        threats = self._visible_air_hunters(env)
        moves = [MOVE_HOLD, MOVE_NORTH, MOVE_SOUTH, MOVE_WEST, MOVE_EAST]
        scored: list[tuple[float, int]] = []
        for move in moves:
            x, y = self._after_move(env, unit, move)
            if (x, y) == (unit.x, unit.y) and move != MOVE_HOLD:
                continue
            newly_seen = self._new_cells_from(env, x, y, unit.sensor_range + 2)
            forward = x * 0.7
            lane_bias = -abs(y - 3) * 0.45 if lane == "north" else -abs(y - 7) * 0.45
            threat_penalty = 0.0
            for threat in threats:
                dist = abs(x - threat.x) + abs(y - threat.y)
                danger_range = threat.strike_range + 2
                if dist <= danger_range:
                    threat_penalty += (danger_range + 1 - dist) * 10.0
            scored.append((newly_seen * 1.5 + forward + lane_bias - threat_penalty, move))
        return max(scored, key=lambda item: item[0])[1] if scored else MOVE_HOLD

    def _after_move(self, env: OverwatchEnv, unit: RLUnit, move: int) -> tuple[int, int]:
        dx, dy = {
            MOVE_HOLD: (0, 0),
            MOVE_NORTH: (0, -1),
            MOVE_SOUTH: (0, 1),
            MOVE_WEST: (-1, 0),
            MOVE_EAST: (1, 0),
        }[move]
        x, y = unit.x, unit.y
        occupied = {(other.x, other.y) for other in env.units if other.alive and other is not unit}
        for _ in range(unit.move):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < env.width and 0 <= ny < env.height):
                break
            if (nx, ny) in occupied:
                break
            if unit.domain == "ground" and int(env.terrain[ny, nx]) == 2:
                break
            x, y = nx, ny
        return x, y

    def _new_cells_from(self, env: OverwatchEnv, x: int, y: int, sensor_range: int) -> int:
        count = 0
        for cy in range(env.height):
            for cx in range(env.width):
                if env.blue_seen_ever[cy, cx]:
                    continue
                if abs(x - cx) + abs(y - cy) <= sensor_range:
                    count += 1
        return count


def predict_baseline_action(env: OverwatchEnv) -> np.ndarray:
    return ScriptedIntentPolicy().predict(env)
