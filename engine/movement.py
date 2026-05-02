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
