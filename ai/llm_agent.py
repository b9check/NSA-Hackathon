"""LLM-driven controller: Claude Sonnet 4.6 picks actions per turn.

Single tool-use call per turn. Caches the system prompt for a price break on
turn 2 onward. Validates the plan + repairs invalid orders + reports
reasoning back to the runner via the shared `meta` dict.

Falls back to all-HOLD on API error or on a wholly invalid plan; the
controller-runner adds a fallback badge to the meta so the UI can show a
"thinking went wrong" state.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import anthropic

from ai.controller import Controller, ControllerResult, register_controller
from ai.memory import load_lessons, top_k
from ai.menu import build_menus
from ai.prompt import SYSTEM_PROMPT, TOOL_SCHEMA, render_user_message
from ai.validate import validate_plan
from engine.orders import HoldOrder, Order
from engine.state import GameState


log = logging.getLogger(__name__)


# Claude Sonnet 4.6 — the live-demo workhorse. Haiku for cheap fallback /
# overnight self-play (Phase D).
DEFAULT_MODEL = "claude-sonnet-4-6"
FALLBACK_MODEL = "claude-haiku-4-5"


# Module-level singleton: anthropic.AsyncAnthropic wraps an httpx.AsyncClient
# with a connection pool. Per-call instantiation leaks CLOSE_WAIT sockets
# and chokes the event loop after a few dozen calls (caught during a 20-game
# self-play run). Reuse one client for the whole process.
_SHARED_CLIENT: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None:
        _SHARED_CLIENT = anthropic.AsyncAnthropic()
    return _SHARED_CLIENT


async def reset_client() -> None:
    """Force a fresh client. Some long-lived runs accumulate stale
    httpx connections that the SDK doesn't reap; harnesses call this
    between games as a belt-and-suspenders safety net."""
    global _SHARED_CLIENT
    if _SHARED_CLIENT is not None:
        try:
            await _SHARED_CLIENT.close()
        except Exception:
            pass
        _SHARED_CLIENT = None


@register_controller("llm")
class LLMController(Controller):
    """Claude-driven controller. Uses tool-use to constrain output to our
    LLMTurnPlan schema."""

    # Subclass overrides to true to skip lesson retrieval entirely
    # (used by the A/B harness to compare with-memory vs no-memory).
    memory_off: bool = False

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1500,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        # Reuse the module-level client to avoid per-turn connection leaks.
        self._client = _get_client()

    async def decide(self, state: GameState, side: str) -> ControllerResult:
        own_unit_ids = [u.id for u in state.units if u.side == side]
        if not own_unit_ids:
            return [], {"summary": "no controllable units", "decisions": []}

        menus = build_menus(state, side)
        # Retrieve recent lessons unless this is the no-memory ablation.
        if self.memory_off:
            lessons = []
        else:
            lessons = top_k(load_lessons(), k=5)
        user_msg = render_user_message(state, side, menus, lessons=lessons)

        # Single tool-use call. Cache the system prompt so turn N+1 hits the
        # cache and pays only the diff (the per-turn block is ~1.5k tokens).
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": user_msg}],
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "submit_orders"},
            )
        except anthropic.APIError as e:
            log.exception("LLM API call failed for side %s", side)
            return _fallback_orders(own_unit_ids, f"LLM API error: {e!r}", state, side)

        plan = _extract_tool_input(response)
        if plan is None:
            log.warning("LLM returned no tool_use for side %s", side)
            return _fallback_orders(
                own_unit_ids, "LLM returned no tool_use block", state, side,
            )

        orders, sensor_toggles, drops = validate_plan(plan, menus, state, side)

        # Apply radar toggles immediately. They're "free actions" so they
        # don't compete with the unit's order.
        if sensor_toggles:
            _apply_sensor_toggles(state, sensor_toggles)

        meta: Dict[str, Any] = {
            "summary": str(plan.get("summary", ""))[:500],
            "decisions": _decisions_for_panel(plan, orders, sensor_toggles, drops),
            "fallback": False,
            "model": self.model,
            "drops": drops,
            "usage": _usage_dict(response),
        }
        return orders, meta


# ----------------------------------------------------------------------
# Helpers

def _extract_tool_input(response: anthropic.types.Message) -> Optional[Dict[str, Any]]:
    """Pick the first submit_orders tool_use block; return its `input` dict."""
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_orders":
            data = block.input
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return None
            return data if isinstance(data, dict) else None
    return None


def _fallback_orders(
    own_unit_ids: List[str],
    reason: str,
    state: GameState,
    side: str,
) -> ControllerResult:
    orders: List[Order] = [HoldOrder(unit_id=uid) for uid in own_unit_ids]
    return orders, {
        "summary": f"FALLBACK: {reason}",
        "decisions": [
            {
                "unit_id": uid,
                "kind": "HOLD",
                "target_hex": None,
                "intent": "fallback (LLM unavailable)",
            }
            for uid in own_unit_ids
        ],
        "fallback": True,
        "drops": [reason],
    }


def _apply_sensor_toggles(
    state: GameState,
    toggles: List[Tuple[str, str, Optional[Tuple[int, int]]]],
) -> None:
    """Flip is_active on the named sensors and recompute summary range.

    Same semantics as POST /api/sensor/toggle, applied directly to the live
    GameState (we're inside the server's session lock).
    """
    for unit_id, sensor_key, active in toggles:
        unit = state.unit_by_id(unit_id)
        if unit is None:
            continue
        for s in unit.sensors:
            if s.key == sensor_key:
                s.is_active = bool(active)
                break
        unit.sensor = max(
            (s.range for s in unit.sensors if s.is_active), default=0,
        )


def _decisions_for_panel(
    plan: Dict[str, Any],
    orders: List[Order],
    sensor_toggles: List[Tuple[str, str, Optional[Tuple[int, int]]]],
    drops: List[str],
) -> List[Dict[str, Any]]:
    """Re-render the LLM's plan in the shape the UI's ReasoningPanel expects.

    Each decision: {unit_id, kind, target_hex?, intent, action_id}
    """
    plan_decisions = plan.get("decisions") or []
    by_uid: Dict[str, Dict[str, Any]] = {}
    for d in plan_decisions:
        uid = d.get("unit_id")
        if not isinstance(uid, str):
            continue
        by_uid[uid] = d

    out: List[Dict[str, Any]] = []
    for o in orders:
        d = by_uid.get(o.unit_id, {})
        target = getattr(o, "target_hex", None)
        out.append({
            "unit_id": o.unit_id,
            "kind": getattr(o, "kind", "HOLD"),
            "action_id": d.get("action_id"),
            "target_hex": list(target) if target else None,
            "intent": str(d.get("intent", ""))[:120],
        })
    # Append radar toggles as separate "decision" rows (no Order)
    for unit_id, sensor_key, active in sensor_toggles:
        out.append({
            "unit_id": unit_id,
            "kind": "ACTIVATE_RADAR" if active else "DEACTIVATE_RADAR",
            "target_hex": None,
            "intent": f"sensor {sensor_key} {'ON' if active else 'OFF'}",
        })
    return out


def _usage_dict(response: anthropic.types.Message) -> Dict[str, Any]:
    u = response.usage
    return {
        "input": u.input_tokens,
        "output": u.output_tokens,
        "cache_read": getattr(u, "cache_read_input_tokens", 0),
        "cache_create": getattr(u, "cache_creation_input_tokens", 0),
    }


@register_controller("llm-no-memory")
class LLMNoMemoryController(LLMController):
    """A/B-test variant: identical to LLMController but skips lesson
    retrieval. Same model, same prompt scaffolding, just `lessons=[]`."""
    memory_off: bool = True
