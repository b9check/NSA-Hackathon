import { useEffect, useRef } from 'react'
import {
  Application,
  Assets,
  Container,
  Graphics,
  Sprite,
  Text,
  TextStyle,
} from 'pixi.js'
import type { FederatedPointerEvent } from 'pixi.js'
import { GlowFilter } from 'pixi-filters'
import { useStore } from '../store'
import {
  hexCorners,
  hexToPixel,
  hexDistance,
  HEX_SIZE,
  hexWidth,
  hexHeight,
  neighbors,
} from '../hex'
import type { GameState, HexCell, UnitInstance } from '../types'
import { COLORS, SIDE_COLOR } from '../theme'

const PAD_X = 24
const PAD_Y = 24

interface PixiHandles {
  app: Application
  hexHitTest: (mx: number, my: number) => { col: number; row: number } | null
  redraw: (state: GameState, selectedUnitId: string | null) => void
  destroy: () => void
}

async function buildPixi(
  parent: HTMLDivElement,
  state: GameState,
  onPointerDown: (
    hex: { col: number; row: number } | null,
    unit: UnitInstance | null,
  ) => void,
  onHover: (hex: { col: number; row: number } | null) => void,
): Promise<PixiHandles> {
  const w = hexWidth()
  const h = hexHeight()
  const stageW = Math.ceil(w * (state.map.cols + 0.5)) + PAD_X * 2
  const stageH = Math.ceil(h * 0.75 * state.map.rows + h * 0.25) + PAD_Y * 2

  const app = new Application()
  await app.init({
    width: stageW,
    height: stageH,
    background: COLORS.bg,
    antialias: true,
    autoDensity: true,
    resolution: window.devicePixelRatio || 1,
  })
  parent.appendChild(app.canvas)
  app.canvas.style.display = 'block'

  // ---- Background: pre-rendered satellite-style terrain ----
  // The PNG is sized to the full canvas (including PAD_X/PAD_Y), so it lives
  // on app.stage directly, not under the PAD-offset `root`.
  try {
    const tex = await Assets.load('/terrain.png')
    const bg = new Sprite(tex)
    bg.x = 0
    bg.y = 0
    app.stage.addChildAt(bg, 0)
  } catch (e) {
    console.warn('terrain.png missing, falling back to flat panel bg', e)
  }

  // ---- Layers (everything below sits in PAD-offset root) ----
  const root = new Container()
  root.x = PAD_X
  root.y = PAD_Y
  app.stage.addChild(root)

  const gridLayer = new Container()
  const overlayLayer = new Container() // objective ring, reachability, ranges
  const hoverLayer = new Container()
  const unitLayer = new Container()
  root.addChild(gridLayer, overlayLayer, hoverLayer, unitLayer)

  // Pre-compute hex centers
  const cellCenters = new Map<string, { x: number; y: number; cell: HexCell }>()
  for (const cell of state.map.cells) {
    const { x, y } = hexToPixel(cell.col, cell.row)
    cellCenters.set(`${cell.col},${cell.row}`, { x, y, cell })
  }

  // ---- Tactical hex grid overlay (subtle) ----
  drawHexGrid(gridLayer, state, cellCenters)
  drawObjectives(overlayLayer, state, cellCenters)

  // ---- Hover layer (interactive map background) ----
  const hoverGraphics = new Graphics()
  hoverLayer.addChild(hoverGraphics)

  function findHexAtPixel(mx: number, my: number) {
    let best: { col: number; row: number; d: number } | null = null
    const px = mx - PAD_X
    const py = my - PAD_Y
    for (const [key, v] of cellCenters) {
      const dx = px - v.x
      const dy = py - v.y
      const d2 = dx * dx + dy * dy
      if (d2 < HEX_SIZE * HEX_SIZE && (best === null || d2 < best.d)) {
        const [col, row] = key.split(',').map(Number)
        best = { col, row, d: d2 }
      }
    }
    return best ? { col: best.col, row: best.row } : null
  }

  app.stage.eventMode = 'static'
  app.stage.hitArea = app.screen
  app.stage.on('pointermove', (e: FederatedPointerEvent) => {
    const hex = findHexAtPixel(e.global.x, e.global.y)
    onHover(hex)
    hoverGraphics.clear()
    if (hex) {
      const c = cellCenters.get(`${hex.col},${hex.row}`)!
      hoverGraphics
        .poly(hexCorners(c.x, c.y))
        .stroke({ color: COLORS.fg, width: 1.5, alpha: 0.55 })
    }
  })
  app.stage.on('pointerdown', (e: FederatedPointerEvent) => {
    const hex = findHexAtPixel(e.global.x, e.global.y)
    if (!hex) {
      onPointerDown(null, null)
      return
    }
    const u = state.units.find((u) => u.col === hex.col && u.row === hex.row)
    onPointerDown(hex, u ?? null)
  })

  // ---- Unit + selection draw ----
  const unitGfx = new Graphics()
  const unitTexts: Text[] = []
  unitLayer.addChild(unitGfx)
  const overlay = new Graphics()
  overlayLayer.addChild(overlay)
  const selectionGfx = new Graphics()
  selectionGfx.filters = [
    new GlowFilter({ distance: 12, outerStrength: 2, innerStrength: 0.4, color: COLORS.amber }),
  ]
  unitLayer.addChild(selectionGfx)

  function redraw(s: GameState, selectedUnitId: string | null) {
    overlay.clear()
    unitGfx.clear()
    selectionGfx.clear()
    for (const t of unitTexts) t.destroy()
    unitTexts.length = 0

    const selected = selectedUnitId ? s.units.find((u) => u.id === selectedUnitId) : null

    // ---- Reachability + sensor + weapon overlays for selected friendly ----
    if (selected) {
      drawReachability(overlay, s, selected, cellCenters)
      drawSensorRing(overlay, selected, cellCenters)
      drawWeaponRing(overlay, selected, cellCenters)
    }

    // ---- Units ----
    for (const u of s.units) {
      const c = cellCenters.get(`${u.col},${u.row}`)
      if (!c) continue
      drawUnit(unitGfx, unitTexts, unitLayer, u, c.x, c.y)
      if (selected && selected.id === u.id) {
        drawSelectionRing(selectionGfx, c.x, c.y)
      }
    }
  }

  redraw(state, null)

  return {
    app,
    hexHitTest: findHexAtPixel,
    redraw,
    destroy: () => {
      app.destroy(true, { children: true, texture: true })
    },
  }
}

// ---------------- Drawing helpers ----------------

function drawHexGrid(
  layer: Container,
  state: GameState,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
) {
  // Subtle tactical overlay — every hex outlined at low alpha so commanders
  // can read positions without occluding the satellite-style backdrop.
  const outline = new Graphics()
  layer.addChild(outline)
  for (const cell of state.map.cells) {
    const c = centers.get(`${cell.col},${cell.row}`)!
    outline
      .poly(hexCorners(c.x, c.y, HEX_SIZE * 0.985))
      .stroke({ color: COLORS.fg, width: 0.6, alpha: 0.13 })
  }
}

function drawObjectives(
  layer: Container,
  state: GameState,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
) {
  const g = new Graphics()
  g.filters = [new GlowFilter({ distance: 10, outerStrength: 1.6, innerStrength: 0, color: COLORS.amber })]
  for (const o of state.map.objective_hexes) {
    const c = centers.get(`${o.col},${o.row}`)
    if (!c) continue
    g.poly(hexCorners(c.x, c.y, HEX_SIZE * 0.92))
      .stroke({ color: COLORS.amber, width: 2, alpha: 0.95 })
  }
  layer.addChild(g)
}

function drawReachability(
  g: Graphics,
  state: GameState,
  unit: UnitInstance,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
) {
  // Quick BFS approximation: hexes within `speed` step distance whose terrain is traversable.
  // Mirrors engine/movement.py at low fidelity (no per-step costs); good enough for visual hint.
  const cost = (dom: string, t: string) => {
    if (dom === 'air') return 1
    if (dom === 'sea') return t === 'water' ? 1 : Infinity
    if (dom === 'amphib') {
      if (t === 'mountain') return Infinity
      return 1
    }
    // land
    if (t === 'water') return Infinity
    if (t === 'mountain') return 3
    if (t === 'urban' || t === 'forest') return 2
    return 1
  }
  const terrainAt = new Map<string, string>()
  for (const cell of state.map.cells) terrainAt.set(`${cell.col},${cell.row}`, cell.terrain)
  const best = new Map<string, number>()
  best.set(`${unit.col},${unit.row}`, 0)
  const stack: Array<[number, number, number]> = [[unit.col, unit.row, 0]]
  while (stack.length) {
    const [c, r, used] = stack.shift()!
    for (const [nc, nr] of neighbors(c, r)) {
      const key = `${nc},${nr}`
      const t = terrainAt.get(key)
      if (!t) continue
      const step = cost(unit.domain, t)
      if (!Number.isFinite(step)) continue
      const nu = used + step
      if (nu > unit.speed) continue
      if (nu < (best.get(key) ?? Infinity)) {
        best.set(key, nu)
        stack.push([nc, nr, nu])
      }
    }
  }
  for (const [key, used] of best) {
    if (used === 0) continue
    const [col, row] = key.split(',').map(Number)
    const c = centers.get(`${col},${row}`)
    if (!c) continue
    g.poly(hexCorners(c.x, c.y, HEX_SIZE * 0.9))
      .fill({ color: SIDE_COLOR[unit.side], alpha: 0.16 })
      .stroke({ color: SIDE_COLOR[unit.side], width: 1, alpha: 0.55 })
  }
}

function drawSensorRing(
  g: Graphics,
  unit: UnitInstance,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
) {
  const c = centers.get(`${unit.col},${unit.row}`)
  if (!c) return
  for (const [key, v] of centers) {
    const [col, row] = key.split(',').map(Number)
    if (hexDistance(unit.col, unit.row, col, row) === unit.sensor) {
      g.poly(hexCorners(v.x, v.y, HEX_SIZE * 0.92))
        .stroke({ color: COLORS.green, width: 1, alpha: 0.5 })
    }
  }
}

function drawWeaponRing(
  g: Graphics,
  unit: UnitInstance,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
) {
  if (unit.weapon === 0) return
  for (const [key, v] of centers) {
    const [col, row] = key.split(',').map(Number)
    if (hexDistance(unit.col, unit.row, col, row) === unit.weapon) {
      g.poly(hexCorners(v.x, v.y, HEX_SIZE * 0.92))
        .stroke({ color: COLORS.amber, width: 1, alpha: 0.45 })
    }
  }
}

function drawUnit(
  g: Graphics,
  texts: Text[],
  layer: Container,
  u: UnitInstance,
  cx: number,
  cy: number,
) {
  const color = SIDE_COLOR[u.side]
  const r = HEX_SIZE * 0.55

  if (u.side === 'blue') {
    // rounded rect (friendly)
    g.roundRect(cx - r, cy - r * 0.7, r * 2, r * 1.4, 5)
      .fill({ color: COLORS.bg, alpha: 0.85 })
      .stroke({ color, width: 2 })
  } else {
    // diamond (hostile)
    g.poly([cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy])
      .fill({ color: COLORS.bg, alpha: 0.85 })
      .stroke({ color, width: 2 })
  }

  // domain glyph letter
  const style = new TextStyle({
    fontFamily: 'IBM Plex Mono',
    fontSize: 13,
    fontWeight: '600',
    fill: color,
    align: 'center',
  })
  const t = new Text({ text: u.glyph, style })
  t.anchor.set(0.5)
  t.x = cx
  t.y = cy + 0.5
  layer.addChild(t)
  texts.push(t)

  // stealth marker (small dot)
  if (u.stealth) {
    g.circle(cx + r * 0.85, cy - r * 0.6, 2.2).fill(COLORS.fg)
  }

  // mini HP bar
  const barW = r * 1.6
  const ratio = Math.max(0, Math.min(1, u.hp / Math.max(u.hp, hpFromGlyph(u))))
  g.rect(cx - barW / 2, cy + r * 0.85, barW, 2.5).fill({ color: COLORS.line, alpha: 0.9 })
  g.rect(cx - barW / 2, cy + r * 0.85, barW * ratio, 2.5).fill(color)
}

// Heuristic: at game start hp == max_hp, so this is exact for turn 0.
// Keeping a default-by-glyph as a safety net for later turns until state.json adds max_hp.
function hpFromGlyph(u: UnitInstance) {
  // domain-domain default ceilings
  const map: Record<string, number> = { A: 3, U: 1, N: 8, S: 3, T: 6, I: 4 }
  return map[u.glyph] ?? u.hp
}

function drawSelectionRing(g: Graphics, cx: number, cy: number) {
  g.poly(hexCorners(cx, cy, HEX_SIZE * 1.0))
    .stroke({ color: COLORS.amber, width: 2, alpha: 0.95 })
}

// ---------------- React wrapper ----------------

export function MapStage() {
  const ref = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<PixiHandles | null>(null)
  const game = useStore((s) => s.game)
  const selectedUnitId = useStore((s) => s.selectedUnitId)
  const selectUnit = useStore((s) => s.selectUnit)
  const setHover = useStore((s) => s.setHover)

  // mount once when game first loads
  useEffect(() => {
    if (!game || !ref.current || handlesRef.current) return
    let cancelled = false
    ;(async () => {
      const h = await buildPixi(
        ref.current!,
        game,
        (_hex, unit) => {
          if (unit && unit.side === 'blue') selectUnit(unit.id)
          else selectUnit(null)
        },
        (hex) => setHover(hex),
      )
      if (cancelled) {
        h.destroy()
        return
      }
      handlesRef.current = h
    })()
    return () => {
      cancelled = true
      handlesRef.current?.destroy()
      handlesRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [game])

  // redraw on selection / state change
  useEffect(() => {
    if (!handlesRef.current || !game) return
    handlesRef.current.redraw(game, selectedUnitId)
  }, [game, selectedUnitId])

  return <div ref={ref} className="w-full h-full overflow-auto" />
}
