"""Prompt assembly for the LLM agent.

Splits the prompt into:
  - SYSTEM block (cached, terrain-agnostic): rules, schema reminder.
  - USER block (per-turn): current state + per-unit menu.

Caching: the system block is identical across turns of a given match, so we
mark it `cache_control: ephemeral` to hit Anthropic's prompt cache.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ai.memory import Lesson, render_for_prompt
from ai.menu import UnitMenu, _seen_by_side
from engine.state import GameState


# --------------------------------------------------------------------
# System prompt — cached. Terse, schema-forward.

SYSTEM_PROMPT = """You are the AI commander for one side of a turn-based hex \
wargame. Each turn you receive the live game state and a per-unit menu of \
legal actions. You return a single JSON object naming an `action_id` for \
every controllable unit.

# Game rules

- 20×15 odd-r offset hex map. Terrain: open, forest, urban, mountain, water.
- Both sides queue all orders, then the resolver runs:
  1) SCOUT — drone reveals; 2) MOVE (collision tie-break); 3) STRIKE (hex-targeted, \
deterministic damage, hits every enemy on target hex); 4) UPDATE (cleanup, win check).
- Win conditions (priority): annihilation; HP collapse (≤25% of starting HP); \
turn cap = higher HP%.
- BASES are always visible on the map — they don't move and have heavy HP. \
The enemy base is your primary objective once enemy units are mostly cleared. \
Don't camp in your half after winning attrition; advance and strike the base.

# Unit catalog (all 9 types)

infantry         — land, hp 3, speed 2, rifle/MANPADS r1 d1 ∞ (vs land/air)
armor            — land, hp 4, speed 4, gun r1 d2 ∞ (vs land)
missile_launcher — land, hp 3, speed 1, SAM r4 d2 ammo 4 (vs air/sea); radar r4 toggle
scout_drone      — air,  hp 1, speed 5, no weapon; SCOUT reveals r4 around its hex
strike_drone     — air,  hp 1, speed 4, kamikaze r1 d3 ammo 1 (self-destructs on fire)
fighter          — air,  hp 3, speed 5, AAM/ASM r3 d2 ammo 4; stealth; radar r3 toggle
bomber           — air,  hp 4, speed 3, heavy-bombs r2 d4 ammo 5 (vs land/sea only)
destroyer        — sea,  hp 5, speed 3, ship-battery r4 d2 ammo 6; radar r3 toggle
base             — fixed, hp 12, point-defense r2 d1 ∞

# Action kinds

HOLD          — skip turn, take full damage
OVERWATCH     — auto-fire on first enemy entering weapon range during MOVE phase
SCOUT         — drone-only; stays put, reveals radius around own hex
MOVE          — pathfind to target_hex within speed budget
STRIKE        — deterministic damage to every enemy on target_hex (whiff still burns ammo)
ACTIVATE_RADAR / DEACTIVATE_RADAR — free action; flips a radar on/off
CUSTOM        — escape hatch when no menu option fits; coords snap to nearest legal

# Output format (strict)

You MUST respond by calling the `submit_orders` tool exactly once. The tool input is:

{
  "summary": "1-2 sentence commander's intent for this turn",
  "decisions": [
    {"unit_id": "<id from menu>", "action_id": <int from that unit's menu>, "intent": "<≤80 chars>"}
  ]
}

Rules:
- Every controllable unit MUST appear in `decisions` exactly once.
- `action_id` MUST be one of the IDs listed in that unit's menu.
- `intent` is a one-line natural-language description of WHY (for the human-facing reasoning panel).
- If you must use CUSTOM (action_id=99), include `target_hex: [col, row]` in the decision dict.

Plan jointly across units. Coordinate scouts feeding strikes; bombers need fighter cover; \
missile_launchers and bases are stationary; strike_drones die on fire.
"""


# --------------------------------------------------------------------
# Per-turn user block

def render_state(state: GameState, side: str) -> str:
    """Compact state render: ASCII map + own units + visible enemies +
    bases + objectives + recent events. ~1500-2000 tokens."""
    map_block = _render_ascii_map(state, side)
    own_block = _render_own_units(state, side)
    enemy_block = _render_visible_enemies(state, side)
    bases_block = _render_bases(state, side)
    objectives_block = _render_objectives(state, side)
    return f"""## TURN {state.turn + 1} of {state.victory.turn_cap} — {side.upper()} TO MOVE

starting HP: blue {state.starting_hp.get('blue', 0)} / red {state.starting_hp.get('red', 0)}
hp_loss_threshold: {state.victory.hp_loss_threshold}

## Map (your view)
{map_block}

## Your units (you control these)
{own_block}

## Visible enemies (hexes within your sensor coverage)
{enemy_block}

## Bases (always visible — terrain landmarks)
{bases_block}

## Objectives this turn
{objectives_block}
"""


def render_menu_block(menus: Dict[str, UnitMenu]) -> str:
    """Render every controllable unit's menu in compact form."""
    lines: List[str] = ["## Per-unit legal actions (pick one action_id per unit)"]
    for uid, m in menus.items():
        lines.append(f"\n### {uid} ({m.unit_type})")
        for it in m.items:
            lines.append(f"  {it.action_id:>2}. {it.label}")
    return "\n".join(lines)


def render_memory_block(lessons: List[Lesson]) -> str:
    """Render top-K retrieved lessons for the LLM. Empty stub if none."""
    return f"## Lessons from prior games\n\n{render_for_prompt(lessons)}"


def render_user_message(
    state: GameState,
    side: str,
    menus: Dict[str, UnitMenu],
    lessons: List[Lesson] | None = None,
) -> str:
    parts = [render_state(state, side)]
    if lessons:
        parts.append(render_memory_block(lessons))
    parts.append(render_menu_block(menus))
    return "\n".join(parts)


# --------------------------------------------------------------------
# Tool schema for Anthropic tool use

TOOL_SCHEMA: Dict[str, Any] = {
    "name": "submit_orders",
    "description": (
        "Submit a complete order set for this turn. Every controllable unit "
        "must appear exactly once, with an action_id drawn from that unit's menu."
    ),
    "input_schema": {
        "type": "object",
        "required": ["summary", "decisions"],
        "properties": {
            "summary": {
                "type": "string",
                "description": "1-2 sentence commander's intent for this turn.",
            },
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["unit_id", "action_id", "intent"],
                    "properties": {
                        "unit_id": {"type": "string"},
                        "action_id": {"type": "integer"},
                        "intent": {"type": "string"},
                        "target_hex": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2, "maxItems": 2,
                            "description": (
                                "Required only when action_id=99 (CUSTOM). "
                                "Format: [col, row]."
                            ),
                        },
                    },
                },
            },
        },
    },
}


# --------------------------------------------------------------------
# ASCII map + unit table renderers

_TERRAIN_GLYPH = {
    "open": ".",
    "urban": "#",
    "forest": "f",
    "mountain": "^",
    "water": "~",
}


def _render_ascii_map(state: GameState, side: str) -> str:
    """One-char-per-cell map with units overlayed.

    Uppercase = your units, lowercase = visible enemies, ? = ghost (last
    seen), terrain otherwise. Two-letter unit IDs (B1..B9, R1..R9) shown in
    a separate legend so the map stays narrow.
    """
    cols, rows = state.map.cols, state.map.rows
    terrain_grid: Dict[tuple[int, int], str] = {
        (c.col, c.row): _TERRAIN_GLYPH.get(getattr(c.terrain, "value", str(c.terrain)), "?")
        for c in state.map.cells
    }

    # Build glyph map. Bases get 2-letter codes (Bb / Rb) on their hex.
    overlay: Dict[tuple[int, int], str] = {}
    legend_lines: List[str] = []

    # Own units — short codes
    for i, u in enumerate(sorted(
        [x for x in state.units if x.side == side], key=lambda x: x.id,
    )):
        code = f"{side[0].upper()}{i+1}"
        overlay[(u.col, u.row)] = code
        legend_lines.append(f"  {code} = {u.id} ({u.type}, hp {u.hp}/{u.max_hp})")
    # Enemy units, only if visible
    enemy_side = "red" if side == "blue" else "blue"
    enemies_visible = [
        e for e in state.units
        if e.side == enemy_side and _seen_by_side(state, side, e)
    ]
    for i, e in enumerate(sorted(enemies_visible, key=lambda x: x.id)):
        code = f"{enemy_side[0].lower()}{i+1}"
        overlay[(e.col, e.row)] = code
        legend_lines.append(f"  {code} = {e.id} ({e.type}, position seen)")
    # Bases — ALWAYS visible per UNITS_AND_RULES.md ("map terrain and
    # bases always visible"). Without this the AI never sees the enemy
    # base and wanders off after clearing units.
    for b in state.bases or []:
        code = f"{b.side[0].upper()}b" if b.side == side else f"{b.side[0].lower()}b"
        overlay[(b.col, b.row)] = code
        # HP is only revealed for own bases; enemy base HP is generic.
        if b.side == side:
            legend_lines.append(
                f"  {code} = {b.id} (YOUR base, hp {b.hp}/{b.max_hp})"
            )
        else:
            legend_lines.append(
                f"  {code} = {b.id} (ENEMY base, hp {b.hp}/{b.max_hp}) — primary objective"
            )

    # Render rows. Odd-r offset → indent odd rows by 1 cell width.
    lines: List[str] = []
    header = "    " + " ".join(f"{c:>2}" for c in range(cols))
    lines.append(header)
    for r in range(rows):
        offset = " " if r % 2 == 1 else ""
        cells: List[str] = []
        for c in range(cols):
            ov = overlay.get((c, r))
            if ov is not None:
                # 2-char code; pad if 1-char for column alignment
                cells.append(f"{ov:>2}")
            else:
                glyph = terrain_grid.get((c, r), " ")
                cells.append(f" {glyph}")
        lines.append(f"{r:>2}  {offset}{' '.join(cells)}")
    lines.append("")
    lines.append("Legend:")
    lines.append("  Terrain — . open  f forest  ^ mountain  ~ water  # urban")
    lines.extend(legend_lines)
    return "\n".join(lines)


def _render_own_units(state: GameState, side: str) -> str:
    rows = []
    rows.append(f"{'id':<28} {'type':<17} pos       hp     speed   sensor   weapon")
    for u in sorted([x for x in state.units if x.side == side], key=lambda x: x.id):
        weapon = u.weapons[0] if u.weapons else None
        wstr = "—"
        if weapon is not None:
            ammo = "∞" if weapon.ammo == -1 else str(weapon.ammo)
            wstr = f"{weapon.kind} r{weapon.range} d{weapon.damage} ammo={ammo}"
        sens_str = f"{u.sensor}"
        active_radars = [s for s in u.sensors if s.modality == "radar" and s.is_active]
        inactive_radars = [s for s in u.sensors if s.modality == "radar" and not s.is_active]
        if active_radars:
            sens_str += " (radar ON)"
        elif inactive_radars:
            sens_str += " (radar OFF)"
        rows.append(
            f"{u.id:<28} {u.type:<17} ({u.col:>2},{u.row:>2})  {u.hp}/{u.max_hp:<3} {u.speed:<7} {sens_str:<8} {wstr}"
        )
    return "\n".join(rows)


def _render_visible_enemies(state: GameState, side: str) -> str:
    enemy_side = "red" if side == "blue" else "blue"
    visible = [
        e for e in state.units
        if e.side == enemy_side and _seen_by_side(state, side, e)
    ]
    if not visible:
        return "  (no enemy units currently sensed)"
    rows = [f"{'id':<28} {'type':<17} pos       hp     domain"]
    for e in sorted(visible, key=lambda x: x.id):
        rows.append(
            f"{e.id:<28} {e.type:<17} ({e.col:>2},{e.row:>2})  {e.hp}/{e.max_hp:<3} {e.domain}"
        )
    return "\n".join(rows)


def _render_bases(state: GameState, side: str) -> str:
    """List ALL bases (own + enemy). Bases are always visible per the
    rules — they're map landmarks, not units to hide. The enemy base is
    the AI's primary objective once enemy units are mostly cleared."""
    if not state.bases:
        return "  (no bases on this map)"
    rows = [f"{'id':<22} {'side':<8} pos       hp     role"]
    for b in state.bases:
        owner = "YOURS" if b.side == side else "ENEMY"
        role = "primary target" if b.side != side else "defend"
        rows.append(
            f"{b.id:<22} {owner:<8} ({b.col:>2},{b.row:>2})  {b.hp}/{b.max_hp:<3} {role}"
        )
    return "\n".join(rows)


def _render_objectives(state: GameState, side: str) -> str:
    """Plain-English summary of paths to victory + remaining work, so the
    LLM doesn't lose the plot after clearing units."""
    enemy_side = "red" if side == "blue" else "blue"
    enemy_units_alive = [u for u in state.units if u.side == enemy_side]
    enemy_bases_alive = [b for b in (state.bases or []) if b.side == enemy_side]
    own_hp_total = (
        sum(u.hp for u in state.units if u.side == side)
        + sum(b.hp for b in (state.bases or []) if b.side == side)
    )
    enemy_hp_total = (
        sum(u.hp for u in state.units if u.side == enemy_side)
        + sum(b.hp for b in (state.bases or []) if b.side == enemy_side)
    )
    own_start = state.starting_hp.get(side, 1) or 1
    enemy_start = state.starting_hp.get(enemy_side, 1) or 1
    own_pct = 100 * own_hp_total / own_start
    enemy_pct = 100 * enemy_hp_total / enemy_start
    threshold_pct = int(state.victory.hp_loss_threshold * 100)
    turns_left = max(0, state.victory.turn_cap - state.turn)

    parts: List[str] = []
    parts.append(
        f"You are {side.upper()} and win when ANY of:\n"
        f"  - all enemy units AND bases destroyed (annihilation)\n"
        f"  - enemy total HP drops to ≤{threshold_pct}% of starting (HP collapse)\n"
        f"  - higher HP%% than enemy at turn {state.victory.turn_cap}"
    )
    parts.append("")
    parts.append(
        f"Score now: YOU {own_pct:.0f}% HP / ENEMY {enemy_pct:.0f}% HP "
        f"(threshold {threshold_pct}%) — {turns_left} turn(s) remaining"
    )
    parts.append(
        f"Remaining enemy: {len(enemy_units_alive)} unit(s) + "
        f"{len(enemy_bases_alive)} base(s)"
    )
    if not enemy_units_alive and enemy_bases_alive:
        parts.append(
            f"⚠ Enemy units cleared but base(s) still standing. PUSH TO THE "
            f"ENEMY BASE — strike it to win by annihilation."
        )
    elif len(enemy_units_alive) <= 2 and enemy_bases_alive:
        parts.append(
            f"⚠ Enemy nearly cleared. After mopping up, advance on enemy base."
        )
    return "\n".join(parts)
