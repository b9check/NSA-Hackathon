import { useEffect, useRef } from 'react'
import {
  Application,
  Assets,
  Container,
  Graphics,
  Sprite,
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
import type { GameState, HexCell, UnitInstance, ViewMode } from '../types'
import { COLORS, SIDE_COLOR } from '../theme'
import {
  syncBases,
  syncUnits,
  type BaseNode,
  type UnitNode,
} from '../render/units'
import { loadIcons } from '../render/icons'
import { bindApp } from '../anim/tween'

const PAD_X = 24
const PAD_Y = 24

interface PixiHandles {
  app: Application
  hexHitTest: (mx: number, my: number) => { col: number; row: number } | null
  redraw: (
    state: GameState,
    selectedUnitId: string | null,
    viewMode: ViewMode,
  ) => void
  destroy: () => void
}


function computeVisibleHexes(state: GameState, mode: ViewMode): Set<string> {
  // Empty set = "no fog filter"; caller treats omniscient specially.
  const out = new Set<string>()
  if (mode === 'omniscient') return out
  // Friendly units AND bases contribute to the COP. Bases always cover at
  // least their own hex (sensor=0), and many carry organic radars.
  const sources: Array<{ col: number; row: number; sensor: number }> = []
  for (const u of state.units) {
    if (u.side === mode) sources.push({ col: u.col, row: u.row, sensor: u.sensor })
  }
  for (const b of state.bases ?? []) {
    if (b.side === mode) sources.push({ col: b.col, row: b.row, sensor: b.sensor })
  }
  for (const src of sources) {
    for (const cell of state.map.cells) {
      if (hexDistance(src.col, src.row, cell.col, cell.row) <= src.sensor) {
        out.add(`${cell.col},${cell.row}`)
      }
    }
  }
  return out
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
  // Bind the global tween module to this Pixi app so animation primitives
  // can drive off the same ticker (and pause when the app does).
  bindApp(app)
  // Preload the unit + base icon textures before any redraw so the very
  // first reconcile populates Sprites instead of fallback Graphics.
  await loadIcons()

  // ---- Background: pre-rendered satellite-style terrain ----
  // Cache-bust with a query param so a region swap force-reloads the image.
  const terrainUrl = '/terrain.png?v=' + Date.now()
  try {
    const tex = await Assets.load(terrainUrl)
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
  const fogLayer = new Container() // dims non-visible hexes in side views
  const overlayLayer = new Container() // objective ring, reachability, ranges
  const hoverLayer = new Container()
  const unitLayer = new Container()
  root.addChild(gridLayer, fogLayer, overlayLayer, hoverLayer, unitLayer)

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

  // ---- Selection / overlays / fog ----
  const overlay = new Graphics()
  overlayLayer.addChild(overlay)
  const fogGfx = new Graphics()
  fogLayer.addChild(fogGfx)
  const selectionGfx = new Graphics()
  selectionGfx.filters = [
    new GlowFilter({ distance: 12, outerStrength: 2, innerStrength: 0.4, color: COLORS.amber }),
  ]
  unitLayer.addChild(selectionGfx)

  // ---- Per-unit / per-base Containers (reconciled, not redrawn) ----
  // unitLayer ordering: bases below, then units above.
  const baseHost = new Container()
  const unitHost = new Container()
  unitLayer.addChild(baseHost, unitHost)
  // selectionGfx already added above; ensure it sits on top of units.
  unitLayer.setChildIndex(selectionGfx, unitLayer.children.length - 1)
  const unitNodes: Map<string, UnitNode> = new Map()
  const baseNodes: Map<string, BaseNode> = new Map()

  function redraw(
    s: GameState,
    selectedUnitId: string | null,
    viewMode: ViewMode,
  ) {
    overlay.clear()
    fogGfx.clear()
    selectionGfx.clear()

    const selected = selectedUnitId
      ? s.units.find((u) => u.id === selectedUnitId)
      : null

    const visibleHexes = computeVisibleHexes(s, viewMode)
    const sideView = viewMode !== 'omniscient'

    // ---- Fog ----
    if (sideView) {
      for (const cell of s.map.cells) {
        const key = `${cell.col},${cell.row}`
        if (visibleHexes.has(key)) continue
        const c = cellCenters.get(key)!
        fogGfx
          .poly(hexCorners(c.x, c.y, HEX_SIZE * 1.04))
          .fill({ color: 0x05080F, alpha: 0.78 })
      }
    }

    if (selected) {
      drawReachability(overlay, s, selected, cellCenters)
      drawSensorRing(overlay, selected, cellCenters)
      drawWeaponRing(overlay, selected, cellCenters)
    }

    // ---- Reconcile units + bases (per-id Containers) ----
    syncBases(baseHost, s, (b) => {
      if (!sideView) return true
      if (b.side === viewMode) return true
      return visibleHexes.has(`${b.col},${b.row}`)
    }, baseNodes)

    syncUnits(unitHost, s, (u) => {
      if (!sideView) return true
      if (u.side === viewMode) return true
      return visibleHexes.has(`${u.col},${u.row}`)
    }, unitNodes)

    // Selection ring (above units)
    if (selected) {
      const c = cellCenters.get(`${selected.col},${selected.row}`)
      if (c) drawSelectionRing(selectionGfx, c.x, c.y)
    }
  }

  redraw(state, null, 'omniscient')

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
  const viewMode = useStore((s) => s.viewMode)
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

  // redraw on selection / state / view-mode change
  useEffect(() => {
    if (!handlesRef.current || !game) return
    handlesRef.current.redraw(game, selectedUnitId, viewMode)
  }, [game, selectedUnitId, viewMode])

  return <div ref={ref} className="w-full h-full overflow-auto" />
}
