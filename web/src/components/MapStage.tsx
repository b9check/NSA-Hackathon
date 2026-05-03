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
  refreshHpBar,
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

  // ---- Last-known-position memory (per observer side) ----
  // Each side remembers where it last *saw* enemy units / bases. Entries
  // older than LKP_DECAY turns are purged. Only consulted in side views.
  const LKP_DECAY = 3
  type Lkp = { col: number; row: number; turn: number; isBase: boolean; type: string }
  const lastSeen: Record<'blue' | 'red', Map<string, Lkp>> = {
    blue: new Map(),
    red: new Map(),
  }

  // ---- Tactical hex grid overlay (subtle) ----
  drawHexGrid(gridLayer, state, cellCenters)

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
    // Read the LIVE game state, not the captured initialGame. Otherwise
    // clicks always hit unit positions from the moment Pixi was built —
    // so after a unit moves or dies, clicks on the new position miss it
    // and clicks on the old position still find a stale unit.
    const liveGame = useStore.getState().game
    const units = liveGame ? liveGame.units : state.units
    const u = units.find((u) => u.col === hex.col && u.row === hex.row)
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

    // ---- LKP memory bookkeeping (only meaningful in side views) ----
    if (sideView) {
      const enemy = viewMode === 'blue' ? 'red' : 'blue'
      const myMap = lastSeen[viewMode]
      // Update from currently-visible enemies (units + bases).
      for (const u of s.units) {
        if (u.side !== enemy) continue
        if (visibleHexes.has(`${u.col},${u.row}`)) {
          myMap.set(u.id, {
            col: u.col, row: u.row, turn: s.turn,
            isBase: false, type: u.type,
          })
        }
      }
      for (const b of s.bases ?? []) {
        if (b.side !== enemy) continue
        if (visibleHexes.has(`${b.col},${b.row}`)) {
          myMap.set(b.id, {
            col: b.col, row: b.row, turn: s.turn,
            isBase: true, type: b.type,
          })
        }
      }
      // Purge stale or no-longer-extant entries.
      for (const [id, lkp] of myMap) {
        const stillExists =
          lkp.isBase
            ? (s.bases ?? []).some((x) => x.id === id)
            : s.units.some((x) => x.id === id)
        if (!stillExists || s.turn - lkp.turn > LKP_DECAY) myMap.delete(id)
      }
      // Draw ghosts for entries that aren't currently visible.
      drawLastKnownGhosts(overlay, s, viewMode, visibleHexes, myMap, cellCenters)
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

  // Initial draw must use the LIVE viewMode from the store, not a
  // hardcoded 'omniscient' — otherwise after a regen / scene-swap the
  // remounted MapStage flashes OMNI even when real-game mode forbids it.
  redraw(state, null, useStore.getState().viewMode)

  async function playEvents(events: any[], baseState: GameState): Promise<void> {
    // Resolve a few helpers up front.
    const unitsById = new Map(baseState.units.map((u) => [u.id, u]))
    const basesById = new Map((baseState.bases ?? []).map((b) => [b.id, b]))
    const nodeFor = (id: string) => unitNodes.get(id) ?? baseNodes.get(id)

    // Visibility filter for replay: in side-views, hide enemy actions
    // whose origin / path / target the observer can't sense. Coverage
    // is DYNAMIC — it grows as the observer's own units advance, so
    // enemies caught by an advancing sensor get revealed on the spot.
    const replayViewMode = useStore.getState().viewMode
    const observerSide: 'blue' | 'red' | null =
      replayViewMode === 'blue' || replayViewMode === 'red' ? replayViewMode : null

    // Live position of every unit, updated as moves animate. Starts at
    // baseState (pre-resolve).
    const livePos = new Map<string, { col: number; row: number }>()
    for (const u of baseState.units) livePos.set(u.id, { col: u.col, row: u.row })

    let liveCoverage: Set<string> = new Set<string>()
    const rebuildCoverage = () => {
      liveCoverage = new Set<string>()
      if (!observerSide) return
      const sources: Array<{ col: number; row: number; sensor: number }> = []
      for (const u of baseState.units) {
        if (u.side !== observerSide) continue
        const p = livePos.get(u.id)!
        sources.push({ col: p.col, row: p.row, sensor: u.sensor })
      }
      for (const b of (baseState.bases ?? [])) {
        if (b.side === observerSide) {
          sources.push({ col: b.col, row: b.row, sensor: b.sensor })
        }
      }
      for (const src of sources) {
        for (const cell of baseState.map.cells) {
          if (hexDistance(src.col, src.row, cell.col, cell.row) <= src.sensor) {
            liveCoverage.add(`${cell.col},${cell.row}`)
          }
        }
      }
    }
    const isVisible = (col: number, row: number) =>
      !observerSide || liveCoverage.has(`${col},${row}`)

    // Track which enemy entities have been revealed (made visible to
    // the observer) so we don't re-flash them on each coverage update.
    const revealed = new Set<string>()
    const revealEnemiesInCoverage = async () => {
      if (!observerSide) return
      const newly: Array<{ id: string; col: number; row: number }> = []
      for (const u of baseState.units) {
        if (u.side === observerSide) continue
        const p = livePos.get(u.id)!
        if (!liveCoverage.has(`${p.col},${p.row}`)) continue
        if (revealed.has(u.id)) continue
        revealed.add(u.id)
        const node = unitNodes.get(u.id)
        if (node) {
          const c = cellCenters.get(`${p.col},${p.row}`)
          if (c) { node.container.x = c.x; node.container.y = c.y }
          node.container.visible = true
          node.container.alpha = 0
          newly.push({ id: u.id, col: p.col, row: p.row })
        }
      }
      for (const b of (baseState.bases ?? [])) {
        if (b.side === observerSide) continue
        if (!liveCoverage.has(`${b.col},${b.row}`)) continue
        if (revealed.has(b.id)) continue
        revealed.add(b.id)
        const node = baseNodes.get(b.id)
        if (node) {
          node.container.visible = true
          node.container.alpha = 0
          newly.push({ id: b.id, col: b.col, row: b.row })
        }
      }
      // Light fade-in so the player notices a discovery without it
      // feeling abrupt.
      if (newly.length > 0) {
        const targets = newly
          .map((n) => unitNodes.get(n.id)?.container ?? baseNodes.get(n.id)?.container)
          .filter((c): c is Container => !!c)
        await Promise.all(targets.map((c) => tween(c, { alpha: 1 }, 250, easeOutCubic)))
      }
    }

    rebuildCoverage()
    // Don't await initial reveal — the initial fog already handled what
    // was visible pre-replay; this catches anything we missed.
    revealEnemiesInCoverage().catch(() => {})

    for (const ev of events) {
      switch (ev.type) {
        case 'move': {
          const node = unitNodes.get(ev.unit)
          if (!node) break
          const u = unitsById.get(ev.unit)
          if (!u) break
          const isEnemy = observerSide !== null && u.side !== observerSide
          const last = ev.path[ev.path.length - 1]
          // Fog: if the mover is enemy from observer's POV and NO step
          // is sensed, snap silently to the destination (no arrow, no
          // animation) — observer never saw it move.
          const stepVisible = ev.path.map((h: [number, number]) => isVisible(h[0], h[1]))
          if (isEnemy && !stepVisible.some((v: boolean) => v)) {
            const c = cellCenters.get(`${last[0]},${last[1]}`)
            if (c) { node.container.x = c.x; node.container.y = c.y }
            livePos.set(ev.unit, { col: last[0], row: last[1] })
            break
          }
          const path = ev.path.map((h: [number, number]) => {
            const c = cellCenters.get(`${h[0]},${h[1]}`)
            return c ? { x: c.x, y: c.y } : { x: node.container.x, y: node.container.y }
          })
          // Friendly mover: animate step-by-step so the dynamic coverage
          // gets to expand mid-move and surface enemies as we approach.
          if (!isEnemy && ev.path.length >= 2) {
            for (let i = 1; i < ev.path.length; i++) {
              const seg = [path[i - 1], path[i]]
              await animateMovePath(
                node.container, seg, moveMsPerHex(u), false,
                fxLayer, SIDE_COLOR[u.side],
              )
              livePos.set(ev.unit, { col: ev.path[i][0], row: ev.path[i][1] })
              rebuildCoverage()
              await revealEnemiesInCoverage()
            }
          } else {
            await animateMovePath(
              node.container, path, moveMsPerHex(u), false,
              isEnemy ? undefined : fxLayer,
              isEnemy ? undefined : SIDE_COLOR[u.side],
            )
            livePos.set(ev.unit, { col: last[0], row: last[1] })
            // Enemy moves can also walk into our coverage and become visible.
            if (isEnemy) {
              await revealEnemiesInCoverage()
            }
          }
          break
        }
        case 'strike': {
          // Hex-targeted strike. `targets_hit` is every entity_id damaged.
          // Whiff = empty target hex; we still draw the line.
          const atkNode = nodeFor(ev.attacker)
          if (!atkNode) break
          const tgtCenter = cellCenters.get(`${ev.target_hex[0]},${ev.target_hex[1]}`)
          if (!tgtCenter) break
          // Fog: skip the tracer line if neither attacker hex nor target
          // hex is sensed. We still apply HP changes (engine truth) — but
          // the observer doesn't see the engagement happen.
          const atkUnit = unitsById.get(ev.attacker)
          const atkVisible = atkUnit ? isVisible(atkUnit.col, atkUnit.row) : true
          const tgtVisible = isVisible(ev.target_hex[0], ev.target_hex[1])
          const ownAttacker = !!atkUnit && (!observerSide || atkUnit.side === observerSide)
          const showTracer = ownAttacker || atkVisible || tgtVisible
          if (showTracer) {
            await animateStrike(
              fxLayer,
              { x: atkNode.container.x, y: atkNode.container.y },
              { x: tgtCenter.x, y: tgtCenter.y },
              { hit: !ev.whiffed, pkill: 1.0, damage: ev.damage },
            )
          }
          if (!ev.whiffed) {
            for (const tid of ev.targets_hit) {
              const node = nodeFor(tid)
              const tgt = unitsById.get(tid) ?? basesById.get(tid)
              if (node && tgt) {
                tgt.hp = Math.max(0, tgt.hp - ev.damage)
                refreshHpBar(node, tgt.hp, tgt.max_hp, tgt.side)
                if (showTracer) await flashAndShake(node.container)
              }
            }
          }
          // Counter-damage to attacker (melee)
          if (ev.counter_damage > 0) {
            const atk = unitsById.get(ev.attacker)
            if (atk) {
              atk.hp = Math.max(0, atk.hp - ev.counter_damage)
              refreshHpBar(atkNode, atk.hp, atk.max_hp, atk.side)
              if (showTracer) await flashAndShake(atkNode.container)
            }
          }
          break
        }
        case 'overwatch_fire': {
          const atkNode = nodeFor(ev.attacker)
          const tgtNode = nodeFor(ev.target)
          if (!atkNode || !tgtNode) break
          const tgt = unitsById.get(ev.target) ?? basesById.get(ev.target)
          await animateStrike(
            fxLayer,
            { x: atkNode.container.x, y: atkNode.container.y },
            { x: tgtNode.container.x, y: tgtNode.container.y },
            { hit: true, pkill: 1.0, damage: ev.damage },
          )
          if (tgt) {
            tgt.hp = Math.max(0, tgt.hp - ev.damage)
            refreshHpBar(tgtNode, tgt.hp, tgt.max_hp, tgt.side)
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


function drawReachability(
  g: Graphics,
  state: GameState,
  unit: UnitInstance,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
) {
  // Quick BFS approximation: hexes within `speed` step distance whose terrain is traversable.
  // Mirrors engine/movement.py: mountain is passable for ground but eats
  // the full move budget (one mountain step ends the turn).
  const cost = (dom: string, t: string) => {
    if (dom === 'air') return 1
    if (dom === 'sea') return t === 'water' ? 1 : Infinity
    if (dom === 'amphib') {
      if (t === 'mountain') return Infinity
      return 1
    }
    // land
    if (t === 'water') return Infinity
    if (t === 'mountain') return Math.max(1, unit.speed) // one step then stop
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


/** Show a transient toast / map popup explaining why a click was rejected.
 *  Returns false (so commitTargetingClick callers can do `return rejectClick(...)`). */
function rejectClick(
  reason: string,
  _hex: { col: number; row: number },
  _game: GameState,
): boolean {
  // Lightweight UX: a top-of-page banner via the global error overlay div
  // (already in main.tsx). It auto-fades after 1.6s.
  showRejectionBanner(reason)
  return false
}


let _bannerTimer: number | null = null
function showRejectionBanner(text: string) {
  let div = document.getElementById('targeting-banner') as HTMLDivElement | null
  if (!div) {
    div = document.createElement('div')
    div.id = 'targeting-banner'
    div.style.cssText = [
      'position:fixed', 'top:60px', 'left:50%', 'transform:translateX(-50%)',
      'background:rgba(255, 184, 77, 0.18)', 'color:#FFB84D',
      'border:1px solid rgba(255, 184, 77, 0.7)',
      'padding:6px 14px', 'border-radius:4px',
      'font:11px ui-monospace,monospace', 'letter-spacing:0.08em',
      'z-index:60', 'pointer-events:none',
      'transition:opacity 200ms', 'opacity:1',
    ].join(';')
    document.body.appendChild(div)
  }
  div.textContent = text.toUpperCase()
  div.style.opacity = '1'
  if (_bannerTimer != null) window.clearTimeout(_bannerTimer)
  _bannerTimer = window.setTimeout(() => {
    if (div) div.style.opacity = '0'
  }, 1600)
}


function drawLastKnownGhosts(
  g: Graphics,
  state: GameState,
  observer: 'blue' | 'red',
  visibleHexes: Set<string>,
  myMap: Map<string, { col: number; row: number; turn: number; isBase: boolean; type: string }>,
  centers: Map<string, { x: number; y: number; cell: HexCell }>,
) {
  const enemyColor = observer === 'blue' ? 0xFF4D5E : 0x4DA3FF
  for (const [_id, lkp] of myMap) {
    if (visibleHexes.has(`${lkp.col},${lkp.row}`)) continue
    const c = centers.get(`${lkp.col},${lkp.row}`)
    if (!c) continue
    const age = state.turn - lkp.turn
    // Alpha decays with age: 0 turn ago -> 0.6, 1 -> 0.4, 2 -> 0.25, 3 -> 0.15
    const alpha = Math.max(0.15, 0.6 - age * 0.15)
    // Dashed-ish hex outline (we approximate with a slightly inset polygon
    // and faded fill).
    g.poly(hexCorners(c.x, c.y, HEX_SIZE * 0.85))
      .fill({ color: enemyColor, alpha: alpha * 0.18 })
      .stroke({ color: enemyColor, width: 1, alpha: alpha * 0.9 })
    // Question-mark glyph in the center.
    drawGhostGlyph(g, c.x, c.y, enemyColor, alpha)
    // Age indicator (T-1, T-2, T-3) below the hex.
    drawAgeTick(g, c.x, c.y + HEX_SIZE * 0.6, age, enemyColor, alpha)
  }
}


function drawGhostGlyph(g: Graphics, cx: number, cy: number, color: number, alpha: number) {
  // Crude '?' formed from arcs/lines (Pixi v8 Graphics doesn't render Text
  // here; we keep this on the overlay Graphics, which is shared and cheap).
  g.circle(cx, cy + 5, 1.6).fill({ color, alpha: alpha * 0.95 })
  // upper hook
  g.moveTo(cx - 4, cy - 5)
    .quadraticCurveTo(cx, cy - 9, cx + 4, cy - 5)
    .quadraticCurveTo(cx + 6, cy - 1, cx, cy + 1)
    .stroke({ color, width: 1.5, alpha: alpha * 0.95 })
}


function drawAgeTick(
  g: Graphics, cx: number, cy: number,
  age: number, color: number, alpha: number,
) {
  // Draw 1..3 small dashes representing how old the contact is.
  const n = Math.min(3, age + 1)
  for (let i = 0; i < n; i++) {
    g.rect(cx - 4 + i * 4, cy, 2.5, 1.5)
      .fill({ color, alpha: alpha * 0.7 })
  }
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
      case 'MOVE': {
        const tgt = centers.get(`${order.target_hex[0]},${order.target_hex[1]}`)
        if (!tgt) break
        drawDashedLine(g, c.x, c.y, tgt.x, tgt.y, color)
        drawArrowhead(g, c.x, c.y, tgt.x, tgt.y, color)
        break
      }
      case 'STRIKE': {
        const tgt = centers.get(`${order.target_hex[0]},${order.target_hex[1]}`)
        if (!tgt) break
        // Solid line + reticle for strikes (now hex-targeted).
        g.moveTo(c.x, c.y).lineTo(tgt.x, tgt.y)
          .stroke({ color: 0xFF6B4D, width: 2.5, alpha: 0.9 })
        drawReticle(g, tgt.x, tgt.y, 0xFF6B4D)
        break
      }
      case 'SCOUT': {
        // SCOUT is local — drone stays put, reveals around itself.
        drawScoutHalo(g, c.x, c.y)
        break
      }
      case 'OVERWATCH': {
        drawShield(g, c.x + HEX_SIZE * 0.55, c.y - HEX_SIZE * 0.55, color)
        break
      }
      case 'HOLD':
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
      if (unit.speed <= 0) return rejectClick('stationary platform', hex, game)
      if (hex.col === unit.col && hex.row === unit.row) return false
      if (hexDistance(unit.col, unit.row, hex.col, hex.row) > unit.speed)
        return rejectClick('out of move range', hex, game)
      // Terrain check: ground can't enter water; sea can't enter land.
      // Mountain is passable for ground but eats the whole move budget
      // (one mountain step ends the turn) — so it's only a valid target
      // if it's adjacent to the unit.
      const cell = game.map.cells.find((c) => c.col === hex.col && c.row === hex.row)
      if (cell) {
        if (unit.domain === 'land' && cell.terrain === 'water')
          return rejectClick('ground unit can\'t enter water', hex, game)
        if (unit.domain === 'sea' && cell.terrain !== 'water')
          return rejectClick('ship can only travel on water', hex, game)
        if (unit.domain === 'land' && cell.terrain === 'mountain' &&
            hexDistance(unit.col, unit.row, hex.col, hex.row) > 1)
          return rejectClick('mountain step ends the turn — must be adjacent', hex, game)
      }
      // Refuse to step ONTO an enemy hex.
      const enemyOnHex = game.units.some(
        (u) => u.side !== unit.side && u.col === hex.col && u.row === hex.row,
      ) || (game.bases ?? []).some(
        (b) => b.side !== unit.side && b.col === hex.col && b.row === hex.row,
      )
      if (enemyOnHex)
        return rejectClick('enemy occupies that hex', hex, game)
      setOrder({
        kind: 'MOVE', unit_id: unit.id,
        target_hex: [hex.col, hex.row],
      })
      return true
    }
    case 'STRIKE': {
      if (unit.weapon <= 0) return rejectClick('no weapons', hex, game)
      if (hexDistance(unit.col, unit.row, hex.col, hex.row) > unit.weapon)
        return rejectClick('out of weapon range', hex, game)
      // Domain check: if there ARE enemies on the hex but none match the
      // weapon's target_domains, reject up front instead of letting the
      // engine silently whiff. (e.g. SAM strikes ground infantry.)
      const w = unit.weapons[0]
      if (w && w.target_domains && w.target_domains.length > 0) {
        const enemiesOnHex = game.units.filter(
          (u) => u.side !== unit.side && u.col === hex.col && u.row === hex.row,
        )
        const basesOnHex = (game.bases ?? []).filter(
          (b) => b.side !== unit.side && b.col === hex.col && b.row === hex.row,
        )
        if (enemiesOnHex.length > 0 || basesOnHex.length > 0) {
          const anyHittable =
            enemiesOnHex.some((u) => w.target_domains.includes(u.domain)) ||
            basesOnHex.some(() => w.target_domains.includes('land'))
          if (!anyHittable) {
            return rejectClick(
              `${w.display} can only hit ${w.target_domains.join('/')}`,
              hex, game,
            )
          }
        }
      }
      // STRIKE is hex-targeted: damage applies to every enemy on the hex
      // after MOVE. Whiff (empty hex / target moved) still consumes ammo.
      setOrder({
        kind: 'STRIKE', unit_id: unit.id, target_hex: [hex.col, hex.row],
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

  // Mount-only teardown. Runs once per MapStage instance — destroys
  // Pixi when the component truly unmounts (e.g. region swap remounts
  // via key=assetVersion). This is split from the build effect so
  // game-state mutations don't tear Pixi down.
  useEffect(() => {
    return () => {
      handlesRef.current?.destroy()
      handlesRef.current = null
    }
  }, [])

  // Build Pixi the first time `game` is available. Bails on subsequent
  // game changes (handlesRef.current is already set) — those are
  // reconciled by the redraw effect, not by rebuilding the app.
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
              return
            } else {
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
    // The cleanup here only cancels an in-flight async build (if `game`
    // changes mid-build, we don't want a stale Pixi to land in
    // handlesRef). It does NOT destroy a built Pixi — the mount-only
    // effect above owns that.
    return () => { cancelled = true }
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
        // The store decides what comes next: in hot-seat mode it
        // orchestrates a 2nd POV pass; otherwise it refetches and
        // settles. This keeps replay-completion logic in one place.
        useStore.getState().onReplayComplete()
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingEvents])

  return <div ref={ref} className="w-full h-full overflow-auto" />
}
