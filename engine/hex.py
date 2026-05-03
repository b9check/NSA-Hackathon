"""Pointy-top hex grid using odd-r offset coordinates.

Storage / serialization uses (col, row) offset coords for ergonomics.
Math (distance, neighbors, line) uses axial / cube under the hood.

Reference: https://www.redblobgames.com/grids/hexagons/
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hex:
    col: int
    row: int

    def __iter__(self):
        yield self.col
        yield self.row


# Odd-r offset neighbor deltas (col_delta, row_delta) per row parity
_EVEN_ROW_NEIGHBORS = [(+1, 0), (-1, 0), (0, -1), (-1, -1), (0, +1), (-1, +1)]
_ODD_ROW_NEIGHBORS = [(+1, 0), (-1, 0), (+1, -1), (0, -1), (+1, +1), (0, +1)]


def neighbors(h: Hex) -> list[Hex]:
    deltas = _ODD_ROW_NEIGHBORS if (h.row & 1) else _EVEN_ROW_NEIGHBORS
    return [Hex(h.col + dc, h.row + dr) for dc, dr in deltas]


def offset_to_cube(h: Hex) -> tuple[int, int, int]:
    x = h.col - (h.row - (h.row & 1)) // 2
    z = h.row
    y = -x - z
    return x, y, z


def distance(a: Hex, b: Hex) -> int:
    ax, ay, az = offset_to_cube(a)
    bx, by, bz = offset_to_cube(b)
    return (abs(ax - bx) + abs(ay - by) + abs(az - bz)) // 2


def in_bounds(h: Hex, cols: int, rows: int) -> bool:
    return 0 <= h.col < cols and 0 <= h.row < rows


def hex_range(center: Hex, radius: int, cols: int, rows: int) -> list[Hex]:
    """All hexes within `radius` of `center`, clipped to map bounds."""
    if radius <= 0:
        return [center] if in_bounds(center, cols, rows) else []
    cx, cy, cz = offset_to_cube(center)
    result: list[Hex] = []
    for dx in range(-radius, radius + 1):
        for dy in range(max(-radius, -dx - radius), min(radius, -dx + radius) + 1):
            dz = -dx - dy
            x, y, z = cx + dx, cy + dy, cz + dz
            # cube to offset (odd-r)
            col = x + (z - (z & 1)) // 2
            row = z
            h = Hex(col, row)
            if in_bounds(h, cols, rows):
                result.append(h)
    return result
