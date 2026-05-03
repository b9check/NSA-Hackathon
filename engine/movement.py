"""Per-unit reachability with terrain costs (Dijkstra on the hex grid)."""
from __future__ import annotations

import heapq

from engine.hex import Hex, in_bounds, neighbors
from engine.state import GameState, UnitInstance
from engine.terrain import INF, Terrain, can_traverse, move_cost


def _terrain_grid(state: GameState) -> dict[tuple[int, int], Terrain]:
    return {(c.col, c.row): c.terrain for c in state.map.cells}


def reachable(state: GameState, unit: UnitInstance) -> dict[tuple[int, int], int]:
    """Map of (col,row) -> min move-cost spent to reach, within unit.speed."""
    grid = _terrain_grid(state)
    cols, rows = state.map.cols, state.map.rows
    start = Hex(unit.col, unit.row)
    best: dict[tuple[int, int], int] = {(start.col, start.row): 0}
    pq: list[tuple[int, int, int]] = [(0, start.col, start.row)]
    while pq:
        cost, c, r = heapq.heappop(pq)
        if cost > best.get((c, r), INF):
            continue
        for nb in neighbors(Hex(c, r)):
            if not in_bounds(nb, cols, rows):
                continue
            terr = grid[(nb.col, nb.row)]
            if not can_traverse(unit.domain, terr):
                continue
            step = move_cost(unit.domain, terr)
            new_cost = cost + step
            if new_cost > unit.speed:
                continue
            if new_cost < best.get((nb.col, nb.row), INF):
                best[(nb.col, nb.row)] = new_cost
                heapq.heappush(pq, (new_cost, nb.col, nb.row))
    return best


def path_to(
    state: GameState,
    unit: UnitInstance,
    dest_col: int, dest_row: int,
) -> list[tuple[int, int]] | None:
    """Shortest hex path from unit's current pos to (dest_col, dest_row),
    respecting the unit's domain terrain costs and the unit's speed budget.

    Returns None if dest is unreachable this turn. Otherwise a list of
    (col, row) waypoints starting with the unit's current hex and ending
    with dest. Single-element list = "no movement".
    """
    if (unit.col, unit.row) == (dest_col, dest_row):
        return [(unit.col, unit.row)]
    grid = _terrain_grid(state)
    cols, rows = state.map.cols, state.map.rows
    start = (unit.col, unit.row)
    target = (dest_col, dest_row)
    best: dict[tuple[int, int], int] = {start: 0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    pq: list[tuple[int, int, int]] = [(0, start[0], start[1])]
    while pq:
        cost, c, r = heapq.heappop(pq)
        if (c, r) == target:
            break
        if cost > best.get((c, r), INF):
            continue
        for nb in neighbors(Hex(c, r)):
            if not in_bounds(nb, cols, rows):
                continue
            terr = grid[(nb.col, nb.row)]
            if not can_traverse(unit.domain, terr):
                continue
            step = move_cost(unit.domain, terr)
            new_cost = cost + step
            if new_cost > unit.speed:
                continue
            if new_cost < best.get((nb.col, nb.row), INF):
                best[(nb.col, nb.row)] = new_cost
                came_from[(nb.col, nb.row)] = (c, r)
                heapq.heappush(pq, (new_cost, nb.col, nb.row))
    if target not in came_from:
        return None
    # Reconstruct.
    path: list[tuple[int, int]] = [target]
    cur = target
    while cur != start:
        cur = came_from[cur]
        path.append(cur)
    path.reverse()
    return path
