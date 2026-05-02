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
import { bindApp, tween, easeOutCubic, delay } from '../anim/tween'
import {
  animateDeath,
  animateMovePath,
  animateStrike,
  flashAndShake,
  popupText,
} from '../anim/primitives'
import { moveMsPerHex } from '../anim/timing'

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
  /** Replay a server event log: tween moves, fire strikes, play deaths. */
  playEvents: (events: any[], baseState: GameState) => Promise<void>
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
  const orderLayer = new Container() // queued-order arrows / reticles
  const hoverLayer = new Container()
  const unitLayer = new Container()
  const fxLayer = new Container() // tracers, particles, popups
  root.addChild(gridLayer, fogLayer, overlayLayer, orderLayer, hoverLayer, unitLayer, fxLayer)

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

  // ---- Selection / overlays / fog / orders ----
  const overlay = new Graphics()
  overlayLayer.addChild(overlay)
  const fogGfx = new Graphics()
  fogLayer.addChild(fogGfx)
  const orderGfx = new Graphics()
  orderLayer.addChild(orderGfx)
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
    orderGfx.clear()
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

    // ---- Queued orders (above overlays, below units) ----
    drawQueuedOrders(orderGfx, s, cellCenters, viewMode)

    // Selection ring (above units)
    if (selected) {
      const c = cellCenters.get(`${selected.col},${selected.row}`)
      if (c) drawSelectionRing(selectionGfx, c.x, c.y)
    }
  }

  redraw(state, null, 'omniscient')

  async function playEvents(events: any[], baseState: GameState): Promise<void> {
    // Resolve a few helpers up front.
    const unitsById = new Map(baseState.units.map((u) => [u.id, u]))
    const basesById = new Map((baseState.bases ?? []).map((b) => [b.id, b]))
    const nodeFor = (id: string) => unitNodes.get(id) ?? baseNodes.get(id)

    for (const ev of events) {
      switch (ev.type) {
        case 'move': {
          const node = unitNodes.get(ev.unit)
          if (!node) break
          const u = unitsById.get(ev.unit)
          if (!u) break
          const path = ev.path.map((h: [number, number]) => {
            const c = cellCenters.get(`${h[0]},${h[1]}`)
            return c ? { x: c.x, y: c.y } : { x: node.container.x, y: node.container.y }
          })
          await animateMovePath(node.container, path, moveMsPerHex(u))
          break
        }
        case 'strike':
        case 'overwatch_fire': {
          const atkNode = nodeFor(ev.attacker)
          const tgtNode = nodeFor(ev.target)
          if (!atkNode || !tgtNode) break
          const tgt = unitsById.get(ev.target) ?? basesById.get(ev.target)
          await animateStrike(
            fxLayer,
            { x: atkNode.container.x, y: atkNode.container.y },
            { x: tgtNode.container.x, y: tgtNode.container.y },
            { hit: ev.hit, pkill: ev.pkill, damage: ev.damage },
          )
          if (ev.hit && tgt) {
            // Optimistically reflect the new HP on the bar (resolver's truth
            // syncs on the post-replay refetchState).
            tgt.hp = ev.remaining_hp
            // Repaint the HP bar via the existing reconciler helper.
            const node = nodeFor(ev.target)
            if (node) {
              // forcing the lastHp mismatch causes drawHpBar to re-run
              node.lastHp = -1
            }
            // shake the target
            await flashAndShake(tgtNode.container)
          }
          break
        }
        case 'destroyed': {
          const node = nodeFor(ev.entity_id)
          if (!node) break
          // popup death tag, then explode
          popupText(
            fxLayer,
            node.container.x,
            node.container.y - 12,
            'DESTROYED',
            COLORS.red,
            900,
          )
          await animateDeath(node.container, fxLayer)
          unitNodes.delete(ev.entity_id)
          baseNodes.delete(ev.entity_id)
          break
        }
        case 'capture': {
          const c = cellCenters.get(`${ev.hex[0]},${ev.hex[1]}`)
          if (c) {
            popupText(
              fxLayer,
              c.x, c.y - 6,
              ev.controller ? `${ev.side.toUpperCase()} CONTROLS` : `${ev.side.toUpperCase()} CAPTURING ${ev.counter}/${ev.threshold}`,
              ev.side === 'blue' ? COLORS.blue : COLORS.red,
              700,
            )
            await delay(120)
          }
          break
        }
        case 'scout_reveal': {
          // brief pulse on revealed hexes
          for (const [col, row] of ev.revealed_hexes) {
            const c = cellCenters.get(`${col},${row}`)
            if (!c) continue
            const pulse = new Graphics()
              .poly(hexCorners(c.x, c.y, HEX_SIZE * 0.95))
              .stroke({ color: COLORS.green, width: 1.5, alpha: 0.7 })
            fxLayer.addChild(pulse)
            tween(pulse, { alpha: 0 }, 350, easeOutCubic).then(() => pulse.destroy())
          }
          await delay(200)
          break
        }
        case 'turn_end':
          // animations done; nothing to play
          break
      }
    }
  }

  return {
    app,
    hexHitTest: findHexAtPixel,
    redraw,
    playEvents,
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


function drawQueuedOrders(
  g: Graphics,
  state: GameState,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
  viewMode: ViewMode,
) {
  const orders = useStore.getState().pendingOrders
  for (const order of Object.values(orders)) {
    const u = state.units.find((x) => x.id === order.unit_id)
    if (!u) continue
    // In side views, only show our own orders.
    if (viewMode !== 'omniscient' && u.side !== viewMode) continue
    const c = centers.get(`${u.col},${u.row}`)
    if (!c) continue
    const color = SIDE_COLOR[u.side]
    switch (order.kind) {
      case 'MOVE':
      case 'CAPTURE':
      case 'SCOUT': {
        const tgt = centers.get(`${order.target_hex[0]},${order.target_hex[1]}`)
        if (!tgt) break
        const lineColor =
          order.kind === 'CAPTURE' ? COLORS.amber :
          order.kind === 'SCOUT'   ? COLORS.green :
          color
        drawDashedLine(g, c.x, c.y, tgt.x, tgt.y, lineColor)
        drawArrowhead(g, c.x, c.y, tgt.x, tgt.y, lineColor)
        if (order.kind === 'CAPTURE') {
          drawFlag(g, tgt.x, tgt.y)
        } else if (order.kind === 'SCOUT') {
          drawScoutHalo(g, tgt.x, tgt.y)
        }
        break
      }
      case 'STRIKE': {
        const tgtUnit = state.units.find((x) => x.id === order.target_id)
        if (!tgtUnit) break
        const tgt = centers.get(`${tgtUnit.col},${tgtUnit.row}`)
        if (!tgt) break
        // Solid line + reticle for strikes.
        g.moveTo(c.x, c.y).lineTo(tgt.x, tgt.y)
          .stroke({ color: 0xFF6B4D, width: 2.5, alpha: 0.9 })
        drawReticle(g, tgt.x, tgt.y, 0xFF6B4D)
        break
      }
      case 'OVERWATCH': {
        // Small shield indicator above the unit.
        drawShield(g, c.x + HEX_SIZE * 0.55, c.y - HEX_SIZE * 0.55, color)
        break
      }
      case 'HOLD':
        // No visual; HOLD is the default.
        break
    }
  }
}


function drawDashedLine(
  g: Graphics, x0: number, y0: number, x1: number, y1: number, color: number,
) {
  const dx = x1 - x0
  const dy = y1 - y0
  const len = Math.hypot(dx, dy)
  if (len < 1) return
  const ux = dx / len, uy = dy / len
  const dash = 7
  const gap = 5
  let t = 0
  while (t < len) {
    const t2 = Math.min(t + dash, len)
    g.moveTo(x0 + ux * t, y0 + uy * t)
      .lineTo(x0 + ux * t2, y0 + uy * t2)
    t = t2 + gap
  }
  g.stroke({ color, width: 1.8, alpha: 0.85 })
}


function drawArrowhead(
  g: Graphics, x0: number, y0: number, x1: number, y1: number, color: number,
) {
  const dx = x1 - x0, dy = y1 - y0
  const len = Math.hypot(dx, dy)
  if (len < 1) return
  const ux = dx / len, uy = dy / len
  // backstep so the arrowhead sits inside the target hex
  const tipX = x1 - ux * 6
  const tipY = y1 - uy * 6
  // perpendicular
  const px = -uy, py = ux
  const baseX = tipX - ux * 9
  const baseY = tipY - uy * 9
  g.poly([
    tipX, tipY,
    baseX + px * 5, baseY + py * 5,
    baseX - px * 5, baseY - py * 5,
  ]).fill({ color, alpha: 0.95 })
}


function drawReticle(g: Graphics, cx: number, cy: number, color: number) {
  g.circle(cx, cy, 11).stroke({ color, width: 1.5, alpha: 0.9 })
  // crosshairs with center gap
  g.moveTo(cx - 14, cy).lineTo(cx - 4, cy)
    .moveTo(cx + 4, cy).lineTo(cx + 14, cy)
    .moveTo(cx, cy - 14).lineTo(cx, cy - 4)
    .moveTo(cx, cy + 4).lineTo(cx, cy + 14)
    .stroke({ color, width: 1.5, alpha: 0.9 })
}


function drawFlag(g: Graphics, cx: number, cy: number) {
  // small flag glyph
  g.moveTo(cx - 4, cy - 8).lineTo(cx - 4, cy + 8)
    .stroke({ color: COLORS.amber, width: 1.5 })
  g.poly([
    cx - 4, cy - 8,
    cx + 7, cy - 5,
    cx - 4, cy - 2,
  ]).fill({ color: COLORS.amber, alpha: 0.85 })
}


function drawScoutHalo(g: Graphics, cx: number, cy: number) {
  g.poly(hexCorners(cx, cy, HEX_SIZE * 1.5))
    .fill({ color: COLORS.green, alpha: 0.10 })
    .stroke({ color: COLORS.green, width: 1, alpha: 0.5 })
  // eye glyph
  g.ellipse(cx, cy, 6, 3).stroke({ color: COLORS.green, width: 1.2 })
  g.circle(cx, cy, 1.4).fill({ color: COLORS.green })
}


function drawShield(g: Graphics, cx: number, cy: number, color: number) {
  // small shield outline
  g.poly([
    cx - 5, cy - 4,
    cx + 5, cy - 4,
    cx + 5, cy + 1,
    cx, cy + 6,
    cx - 5, cy + 1,
  ])
    .fill({ color: COLORS.bg, alpha: 0.85 })
    .stroke({ color, width: 1.5 })
}


// Validate a click while in targeting mode. Returns true on commit.
function commitTargetingClick(
  t: import('../store').TargetingMode,
  unit: UnitInstance,
  hex: { col: number; row: number },
  clickedUnit: UnitInstance | null,
  game: GameState,
): boolean {
  const setOrder = useStore.getState().setOrder
  const cancelTargeting = useStore.getState().cancelTargeting

  // Click on the same unit -> cancel.
  if (clickedUnit && clickedUnit.id === unit.id) {
    cancelTargeting()
    return true
  }

  switch (t.kind) {
    case 'MOVE': {
      // Validate via lightweight reachability mirroring drawReachability.
      if (unit.speed <= 0) return false
      if (hex.col === unit.col && hex.row === unit.row) return false
      // For now, accept any hex within `speed` raw hex distance. The engine
      // will compute the actual path; we get a free server-side validation.
      if (hexDistance(unit.col, unit.row, hex.col, hex.row) > unit.speed)
        return false
      setOrder({
        kind: 'MOVE', unit_id: unit.id,
        target_hex: [hex.col, hex.row],
      })
      return true
    }
    case 'STRIKE': {
      if (!clickedUnit) return false
      if (clickedUnit.side === unit.side) return false
      if (unit.weapon <= 0) return false
      if (hexDistance(unit.col, unit.row, clickedUnit.col, clickedUnit.row) > unit.weapon)
        return false
      setOrder({
        kind: 'STRIKE', unit_id: unit.id, target_id: clickedUnit.id,
      })
      return true
    }
    case 'SCOUT': {
      if (unit.sensor <= 0) return false
      // Allow any in-bounds hex; SCOUT bonus is +50% sensor for the turn.
      const range = Math.max(1, unit.sensor + 1)
      if (hexDistance(unit.col, unit.row, hex.col, hex.row) > range + unit.sensor)
        return false
      setOrder({
        kind: 'SCOUT', unit_id: unit.id, target_hex: [hex.col, hex.row],
      })
      return true
    }
    case 'CAPTURE': {
      if (!(unit.domain === 'land' || unit.domain === 'amphib')) return false
      const isObjective = game.map.objective_hexes.some(
        (o) => o.col === hex.col && o.row === hex.row,
      )
      if (!isObjective) return false
      if (hexDistance(unit.col, unit.row, hex.col, hex.row) > 1) return false
      setOrder({
        kind: 'CAPTURE', unit_id: unit.id, target_hex: [hex.col, hex.row],
      })
      return true
    }
  }
  return false
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
  const setOrder = useStore((s) => s.setOrder)
  const cancelTargeting = useStore((s) => s.cancelTargeting)

  // mount once when game first loads
  useEffect(() => {
    if (!game || !ref.current || handlesRef.current) return
    let cancelled = false
    ;(async () => {
      const h = await buildPixi(
        ref.current!,
        game,
        (hex, unit) => {
          // If targeting mode is active, the next click commits the order.
          const st = useStore.getState()
          const t = st.targeting
          if (t && st.game) {
            const own = st.game.units.find((u) => u.id === t.unitId)
            if (!own) {
              cancelTargeting()
            } else if (hex && commitTargetingClick(t, own, hex, unit, st.game)) {
              // setOrder already happened inside commitTargetingClick
              return
            } else {
              // invalid click in targeting mode: keep mode active, no-op.
              return
            }
            return
          }
          // Default selection behavior.
          const playerSide: 'blue' | 'red' =
            st.viewMode === 'red' ? 'red' : 'blue'
          if (unit && unit.side === playerSide) selectUnit(unit.id)
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

  // redraw on selection / state / view-mode change OR pending-order change.
  const pendingOrdersHash = useStore((s) => JSON.stringify(s.pendingOrders))
  const replaying = useStore((s) => s.replaying)
  useEffect(() => {
    if (!handlesRef.current || !game) return
    if (replaying) return // suppress reconcile during animation playback
    handlesRef.current.redraw(game, selectedUnitId, viewMode)
  }, [game, selectedUnitId, viewMode, pendingOrdersHash, replaying])

  // Replay any events queued by /api/resolve.
  const pendingEvents = useStore((s) => s.pendingEvents)
  useEffect(() => {
    if (!pendingEvents || !handlesRef.current || !game) return
    let cancelled = false
    ;(async () => {
      useStore.getState().setReplaying(true)
      try {
        await handlesRef.current!.playEvents(pendingEvents, game)
      } finally {
        if (cancelled) return
        useStore.getState().setPendingEvents(null)
        useStore.getState().setReplaying(false)
        useStore.getState().refetchState().catch(() => {})
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingEvents])

  return <div ref={ref} className="w-full h-full overflow-auto" />
}
