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

        recon = self._blue_unit(env, "recon")
        if recon is not None:
            target = self._nearest(recon, known_hvts or visible)
            move = self._toward(recon, target) if target is not None else self._frontier_move(env, recon)
            self._set(action, "recon", move, POLICY_SURVEIL)

        strike = self._blue_unit(env, "strike")
        if strike is not None:
            target = self._highest_value(strike, visible or known_hvts)
            in_range = target is not None and self._dist(strike, target) <= strike.strike_range
            move = MOVE_HOLD if in_range else self._toward(strike, target) if target is not None else MOVE_EAST
            self._set(action, "strike", move, POLICY_STRIKE if target is not None else POLICY_SURVEIL)

        sam = self._blue_unit(env, "sam")
        if sam is not None:
            air_target = self._nearest(sam, self._visible_air_threats(env))
            move = MOVE_HOLD if air_target is not None else MOVE_NORTH
            self._set(action, "sam", move, POLICY_STRIKE if air_target is not None else POLICY_SURVEIL)
            action[6] = 1
        return action

    def _defensive(self, env: OverwatchEnv) -> np.ndarray:
        action = self._default_action(radar_emit=False)
        anchor = self._own_hvt_anchor(env)
        air_threats = self._visible_air_threats(env)

        recon = self._blue_unit(env, "recon")
        if recon is not None:
            threat = self._nearest(recon, air_threats)
            if threat is not None and self._dist(recon, threat) <= 3:
                self._set(action, "recon", self._away(recon, threat), POLICY_RETREAT)
            else:
                self._set(action, "recon", self._patrol_move(recon, anchor), POLICY_SURVEIL)

        strike = self._blue_unit(env, "strike")
        if strike is not None:
            threat = self._nearest(strike, air_threats)
            if threat is not None:
                in_range = self._dist(strike, threat) <= strike.strike_range
                move = MOVE_HOLD if in_range else self._toward(strike, threat)
                self._set(action, "strike", move, POLICY_STRIKE)
            else:
                self._set(action, "strike", self._guard_move(strike, anchor, radius=2), POLICY_SURVEIL)

        sam = self._blue_unit(env, "sam")
        if sam is not None:
            threat = self._nearest(sam, air_threats)
            if threat is not None and self._dist(sam, threat) <= sam.strike_range + 1:
                self._set(action, "sam", MOVE_HOLD, POLICY_STRIKE)
                action[6] = 1
            else:
                self._set(action, "sam", self._guard_move(sam, anchor, radius=1), POLICY_SURVEIL)
                action[6] = 0
        return action

    def _recon_heavy(self, env: OverwatchEnv) -> np.ndarray:
        action = self._default_action(radar_emit=True)

        recon = self._blue_unit(env, "recon")
        if recon is not None:
            self._set(action, "recon", self._frontier_move(env, recon), POLICY_SURVEIL)

        strike = self._blue_unit(env, "strike")
        if strike is not None:
            visible = self._visible_enemies(env, include_hvts=True)
            target = self._nearest(strike, visible)
            if target is not None and self._dist(strike, target) <= strike.strike_range:
                self._set(action, "strike", MOVE_HOLD, POLICY_STRIKE)
            else:
                self._set(action, "strike", self._frontier_move(env, strike), POLICY_SURVEIL)

        sam = self._blue_unit(env, "sam")
        if sam is not None:
            air_target = self._nearest(sam, self._visible_air_threats(env))
            if air_target is not None and self._dist(sam, air_target) <= sam.strike_range:
                self._set(action, "sam", MOVE_HOLD, POLICY_STRIKE)
            else:
                self._set(action, "sam", MOVE_HOLD, POLICY_SURVEIL)
            action[6] = 1
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

    def _nearest(self, unit: RLUnit, targets: list[RLUnit | HVT]) -> RLUnit | HVT | None:
        if not targets:
            return None
        return min(targets, key=lambda target: self._dist(unit, target))

    def _highest_value(self, unit: RLUnit, targets: list[RLUnit | HVT]) -> RLUnit | HVT | None:
        if not targets:
            return None
        return max(targets, key=lambda target: (float(target.value), -self._dist(unit, target)))

    def _dist(self, unit: RLUnit, target: RLUnit | HVT) -> int:
        return abs(unit.x - target.x) + abs(unit.y - target.y)

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


def predict_baseline_action(env: OverwatchEnv) -> np.ndarray:
    return ScriptedIntentPolicy().predict(env)
