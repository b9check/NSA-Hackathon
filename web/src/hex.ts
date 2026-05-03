// Pointy-top, odd-r offset hex math (mirrors engine/hex.py).

export const HEX_SIZE = 30 // center-to-vertex radius in px

export function hexWidth(size: number = HEX_SIZE) {
  return Math.sqrt(3) * size
}
export function hexHeight(size: number = HEX_SIZE) {
  return 2 * size
}

export function hexToPixel(
  col: number,
  row: number,
  size: number = HEX_SIZE,
): { x: number; y: number } {
  const w = hexWidth(size)
  const x = w * (col + 0.5 * (row & 1))
  const y = size * 1.5 * row
  return { x, y }
}

export function hexCorners(
  cx: number,
  cy: number,
  size: number = HEX_SIZE,
): number[] {
  // pointy-top corners, starting at top vertex, clockwise
  const pts: number[] = []
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30)
    pts.push(cx + size * Math.cos(angle), cy + size * Math.sin(angle))
  }
  return pts
}

export function offsetToCube(col: number, row: number) {
  const x = col - (row - (row & 1)) / 2
  const z = row
  const y = -x - z
  return { x, y, z }
}

export function hexDistance(
  ac: number, ar: number,
  bc: number, br: number,
) {
  const a = offsetToCube(ac, ar)
  const b = offsetToCube(bc, br)
  return (Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.z - b.z)) / 2
}

const EVEN_NB: Array<[number, number]> = [
  [+1, 0], [-1, 0], [0, -1], [-1, -1], [0, +1], [-1, +1],
]
const ODD_NB: Array<[number, number]> = [
  [+1, 0], [-1, 0], [+1, -1], [0, -1], [+1, +1], [0, +1],
]

export function neighbors(col: number, row: number): Array<[number, number]> {
  const deltas = row & 1 ? ODD_NB : EVEN_NB
  return deltas.map(([dc, dr]) => [col + dc, row + dr] as [number, number])
}
