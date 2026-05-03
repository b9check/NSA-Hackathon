"""Post-game reflection: extract a small number of generalisable lessons
from a finished match's trace, save them to the memory store.

Reflection runs once when the game ends (winner != None) or when the user
clicks the manual FORFEIT button. We use Haiku 4.5 to keep cost low — the
task is summarisation, not strategy.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import anthropic

from ai.memory import Lesson, append_lessons


log = logging.getLogger(__name__)

# Cheap model — the task is summarisation. Sonnet was overkill.
REFLECT_MODEL = "claude-haiku-4-5"


REFLECT_SYSTEM = """You are reviewing a completed turn-based wargame to \
extract 2-4 SHORT, GENERALISABLE LESSONS that the AI commander can apply \
to future games.

A good lesson:
- one concise assertion (≤25 words)
- captures a CAUSAL pattern: "doing X led to Y because Z"
- generalises beyond this exact map / seed
- references the game phase (open / mid / end) when relevant

A bad lesson:
- says "AI played well" with no claim
- restates the score
- is map-specific ("the hex at (8,7) is good")

Examples of good lessons:
- "Activating radar before scouts are forward exposes SAMs to fighter strikes."
- "Bombers committed without fighter cover are picked off by enemy AAW."
- "Concentrating two strikes on the same enemy unit per turn breaks trades."

You MUST respond by calling the `record_lessons` tool exactly once.
"""


TOOL_SCHEMA: Dict[str, Any] = {
    "name": "record_lessons",
    "description": "Record 2-4 lessons distilled from this game's trace.",
    "input_schema": {
        "type": "object",
        "required": ["lessons"],
        "properties": {
            "lessons": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "required": ["claim"],
                    "properties": {
                        "claim": {
                            "type": "string",
                            "description": "≤25 words. One causal claim that generalises.",
                        },
                        "phase": {
                            "type": "string",
                            "enum": ["open", "mid", "end", "any"],
                        },
                        "side_perspective": {
                            "type": "string",
                            "enum": ["blue", "red", "either"],
                            "description": "Which side the lesson is principally for.",
                        },
                    },
                },
            },
        },
    },
}


def _render_trace(game_log: List[Dict[str, Any]], final_state: Dict[str, Any]) -> str:
    """Compact, LLM-friendly summary of what happened.

    `game_log` rows look like {turn, blue_summary?, red_summary?, blue_score, red_score, events}
    `final_state` is the full GameState dict (we extract winner / win_reason / hp totals).
    """
    lines: List[str] = []
    lines.append(f"Outcome: winner={final_state.get('winner')!r} reason={final_state.get('win_reason')!r}")

    # Final HP totals
    blue_hp = sum(u["hp"] for u in final_state.get("units", []) if u["side"] == "blue")
    blue_hp += sum(b["hp"] for b in (final_state.get("bases") or []) if b["side"] == "blue")
    red_hp = sum(u["hp"] for u in final_state.get("units", []) if u["side"] == "red")
    red_hp += sum(b["hp"] for b in (final_state.get("bases") or []) if b["side"] == "red")
    starting = final_state.get("starting_hp", {})
    lines.append(
        f"Final HP — blue {blue_hp}/{starting.get('blue', '?')} | "
        f"red {red_hp}/{starting.get('red', '?')}"
    )

    # Surviving units
    blue_units = [u["type"] for u in final_state.get("units", []) if u["side"] == "blue"]
    red_units = [u["type"] for u in final_state.get("units", []) if u["side"] == "red"]
    lines.append(f"Survivors — blue: {sorted(blue_units)} | red: {sorted(red_units)}")
    lines.append("")
    lines.append(f"Turns played: {len(game_log)}")
    lines.append("")
    lines.append("Per-turn highlights:")
    for row in game_log:
        t = row.get("turn", "?")
        blue_summary = (row.get("blue_summary") or "").strip()
        red_summary = (row.get("red_summary") or "").strip()
        scores = row.get("scores") or {}
        events = row.get("events") or []
        # Compact event count per type
        ev_counts: Dict[str, int] = {}
        for e in events:
            k = str(e.get("type", "?"))
            ev_counts[k] = ev_counts.get(k, 0) + 1
        ev_str = ", ".join(f"{k}×{v}" for k, v in ev_counts.items())
        lines.append(f"  T{int(t) + 1}: scores blue={scores.get('blue')} red={scores.get('red')} | events: {ev_str or 'none'}")
        if blue_summary:
            lines.append(f"     BLUE plan: {blue_summary[:200]}")
        if red_summary:
            lines.append(f"     RED  plan: {red_summary[:200]}")
    return "\n".join(lines)


async def reflect_on_game(
    game_log: List[Dict[str, Any]],
    final_state: Dict[str, Any],
    *,
    game_id: Optional[str] = None,
    model: str = REFLECT_MODEL,
) -> List[Lesson]:
    """Run the reflect call. Returns parsed Lessons (NOT yet persisted —
    caller decides whether to append). game_id auto-generated if not given.
    """
    gid = game_id or ("game_" + uuid.uuid4().hex[:6])
    trace = _render_trace(game_log, final_state)
    user_msg = f"## Game trace\n\n{trace}\n\n## Task\n\nDistill 2-4 lessons via the record_lessons tool."

    from ai.llm_agent import _get_client
    client = _get_client()
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=600,
            system=REFLECT_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "record_lessons"},
        )
    except anthropic.APIError as e:
        log.exception("reflect API call failed")
        return []

    payload = _extract_tool_input(resp)
    if not payload:
        log.warning("reflect returned no tool_use")
        return []

    out: List[Lesson] = []
    winner = final_state.get("winner") or ""
    last_turn = max((row.get("turn", 0) for row in game_log), default=0)
    for entry in payload.get("lessons", []) or []:
        claim = (entry.get("claim") or "").strip()
        if not claim:
            continue
        side_persp = entry.get("side_perspective") or "either"
        outcome = ""
        if winner == "blue":
            outcome = "blue_wins"
        elif winner == "red":
            outcome = "red_wins"
        elif winner == "draw":
            outcome = "draw"
        out.append(Lesson.new(
            claim=claim,
            tags={
                "phase": entry.get("phase") or "any",
                "side_perspective": side_persp,
            },
            source_game_id=gid,
            source_turn=int(last_turn),
            side=side_persp if side_persp != "either" else "",
            outcome=outcome,
        ))
    return out


async def reflect_and_persist(
    game_log: List[Dict[str, Any]],
    final_state: Dict[str, Any],
    *,
    game_id: Optional[str] = None,
) -> List[Lesson]:
    """Convenience: reflect, then append to memory.jsonl."""
    lessons = await reflect_on_game(game_log, final_state, game_id=game_id)
    append_lessons(lessons)
    return lessons


def _extract_tool_input(resp: anthropic.types.Message) -> Optional[Dict[str, Any]]:
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_lessons":
            data = block.input
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return None
            return data if isinstance(data, dict) else None
    return None
