// Per-unit / per-base Pixi node factories + reconcilers.
//
// Each unit and base now owns a Container that we transform (position, scale,
// alpha, rotation) for animation. The scene graph reconciles against state on
// each redraw: matching ids are updated in place; new ids spawn fresh nodes;
// stale ids are flagged for removal so the death-animation system can play
// them out before destroy().
import { Container, Graphics, Sprite, Text, TextStyle } from 'pixi.js'
import type { BaseInstance, Side, UnitInstance } from '../types'
import { COLORS, SIDE_COLOR, SIDE_DIM } from '../theme'
import { HEX_SIZE, hexToPixel } from '../hex'
import { getIcon } from './icons'
import { drawFrame, drawFunctionGlyph } from './symbols'


// Sprite size for unit icons (FlightRadar-ish small silhouettes).
const UNIT_ICON_PX = 30
const BASE_ICON_PX = 28


export interface UnitNode {
  container: Container
  // Either a Sprite (icon) or fallback Graphics (procedural shape).
  shape: Sprite | Graphics
  glyph: Text | null
  hpBar: Graphics
  // Last rendered values, so we don't redraw if nothing changed.
  lastHp: number
  lastMaxHp: number
}

export interface BaseNode {
  container: Container
  shape: Sprite | Graphics
  glyph: Text | null
  hpBar: Graphics
  lastHp: number
  lastMaxHp: number
}

const HP_BAR_W = HEX_SIZE * 0.55 * 1.6
const HP_BAR_H = 4
const HP_BAR_OFFSET_Y = HEX_SIZE * 0.55 * 0.85 + 4

function hpColor(ratio: number): number {
  if (ratio >= 0.6) return COLORS.green
  if (ratio >= 0.3) return COLORS.amber
  return COLORS.red
}

/** Repaint a unit/base's HP bar — exported so the animation pipeline can
 *  refresh it mid-event (between syncUnits passes). */
export function refreshHpBar(
  node: { hpBar: Graphics; lastHp: number; lastMaxHp: number },
  hp: number, maxHp: number, side: Side,
) {
  drawHpBar(node.hpBar, hp, maxHp, side)
  node.lastHp = hp
  node.lastMaxHp = maxHp
}


function drawHpBar(g: Graphics, hp: number, maxHp: number, side: Side) {
  g.clear()
  const ratio = Math.max(0, Math.min(1, hp / Math.max(maxHp, 1)))
  // backing
  g.rect(-HP_BAR_W / 2, HP_BAR_OFFSET_Y, HP_BAR_W, HP_BAR_H)
    .fill({ color: COLORS.bg, alpha: 0.85 })
    .stroke({ color: COLORS.line, width: 1, alpha: 0.9 })
  // foreground
  if (ratio > 0) {
    g.rect(-HP_BAR_W / 2, HP_BAR_OFFSET_Y, HP_BAR_W * ratio, HP_BAR_H)
      .fill({ color: hpColor(ratio) })
  }
  // sliver tick at side color so you can read the side at a glance
  g.rect(-HP_BAR_W / 2, HP_BAR_OFFSET_Y - 1.5, 2, HP_BAR_H + 1.5)
    .fill({ color: SIDE_COLOR[side] })
}

function drawUnitShape(g: Graphics, unit: UnitInstance) {
  g.clear()
  const r = HEX_SIZE * 0.55
  // MIL-STD-2525-style: affiliation frame + function-modifier glyph.
  drawFrame(g, unit.side, r)
  drawFunctionGlyph(g, unit.type, unit.side, r)
  if (unit.stealth) {
    g.circle(r * 0.85, -r * 0.6, 2.4).fill(COLORS.fg)
  }
}

function drawBaseShape(g: Graphics, base: BaseInstance) {
  g.clear()
  const color = SIDE_DIM[base.side]
  const r = HEX_SIZE * 0.62
  // Installation symbol: affiliation frame with a small flag-staff bar
  // through the top — distinguishes a base from a mobile unit.
  drawFrame(g, base.side, r * 0.85)
  // Flag staff (thin vertical bar rising from the frame).
  g.moveTo(0, -r * 0.6).lineTo(0, -r * 1.05).stroke({ color, width: 1.5 })
  g.rect(-r * 0.35, -r * 1.05, r * 0.7, r * 0.18)
    .fill({ color, alpha: 0.85 })
    .stroke({ color, width: 1 })
}

export function createUnitNode(unit: UnitInstance): UnitNode {
  const container = new Container()
  container.eventMode = 'none' // hit-test stays on the stage (brute force)
  container.label = `unit:${unit.id}`
  // Try to use the white silhouette icon and tint it; fall back to the
  // procedural Graphics frame if the icon failed to load.
  const tex = getIcon(unit.type)
  let shape: Sprite | Graphics
  let glyph: Text | null = null
  if (tex) {
    const sprite = new Sprite(tex)
    sprite.anchor.set(0.5)
    sprite.width = UNIT_ICON_PX
    sprite.height = UNIT_ICON_PX
    sprite.tint = SIDE_COLOR[unit.side]
    container.addChild(sprite)
    shape = sprite
  } else {
    const g = new Graphics()
    drawUnitShape(g, unit)
    container.addChild(g)
    glyph = new Text({
      text: unit.glyph,
      style: new TextStyle({
        fontFamily: 'IBM Plex Mono',
        fontSize: 13,
        fontWeight: '600',
        fill: SIDE_COLOR[unit.side],
        align: 'center',
      }),
    })
    glyph.anchor.set(0.5)
    glyph.y = 0.5
    container.addChild(glyph)
    shape = g
  }
  if (unit.stealth) {
    // Small white dot to mark stealth platforms regardless of icon vs graphics.
    const dot = new Graphics()
      .circle(UNIT_ICON_PX * 0.4, -UNIT_ICON_PX * 0.4, 2.4)
      .fill(COLORS.fg)
    container.addChild(dot)
  }
  const hpBar = new Graphics()
  drawHpBar(hpBar, unit.hp, unit.max_hp, unit.side)
  container.addChild(hpBar)
  const { x, y } = hexToPixel(unit.col, unit.row)
  container.x = x
  container.y = y
  return {
    container, shape, glyph, hpBar,
    lastHp: unit.hp, lastMaxHp: unit.max_hp,
  }
}

export function updateUnitNode(node: UnitNode, unit: UnitInstance) {
  const { x, y } = hexToPixel(unit.col, unit.row)
  // Snap position; animations will tween position separately.
  node.container.x = x
  node.container.y = y
  if (unit.hp !== node.lastHp || unit.max_hp !== node.lastMaxHp) {
    drawHpBar(node.hpBar, unit.hp, unit.max_hp, unit.side)
    node.lastHp = unit.hp
    node.lastMaxHp = unit.max_hp
  }
  if (node.glyph && node.glyph.text !== unit.glyph) {
    node.glyph.text = unit.glyph
  }
}

export function createBaseNode(base: BaseInstance): BaseNode {
  const container = new Container()
  container.eventMode = 'none'
  container.label = `base:${base.id}`
  const tex = getIcon(base.type)
  let shape: Sprite | Graphics
  let glyph: Text | null = null
  if (tex) {
    const sprite = new Sprite(tex)
    sprite.anchor.set(0.5)
    sprite.width = BASE_ICON_PX
    sprite.height = BASE_ICON_PX
    sprite.tint = SIDE_DIM[base.side]
    sprite.alpha = 0.92
    container.addChild(sprite)
    shape = sprite
  } else {
    const g = new Graphics()
    drawBaseShape(g, base)
    container.addChild(g)
    glyph = new Text({
      text: 'B',
      style: new TextStyle({
        fontFamily: 'IBM Plex Mono',
        fontSize: 11,
        fontWeight: '700',
        fill: SIDE_DIM[base.side],
      }),
    })
    glyph.anchor.set(0.5)
    glyph.y = 1
    container.addChild(glyph)
    shape = g
  }
  const hpBar = new Graphics()
  drawHpBar(hpBar, base.hp, base.max_hp, base.side)
  container.addChild(hpBar)
  const { x, y } = hexToPixel(base.col, base.row)
  container.x = x
  container.y = y
  return {
    container, shape, glyph, hpBar,
    lastHp: base.hp, lastMaxHp: base.max_hp,
  }
}

export function updateBaseNode(node: BaseNode, base: BaseInstance) {
  const { x, y } = hexToPixel(base.col, base.row)
  node.container.x = x
  node.container.y = y
  if (base.hp !== node.lastHp || base.max_hp !== node.lastMaxHp) {
    drawHpBar(node.hpBar, base.hp, base.max_hp, base.side)
    node.lastHp = base.hp
    node.lastMaxHp = base.max_hp
  }
}

// ---- Reconcilers ----

export function syncUnits(
  layer: Container,
  state: import('../types').GameState,
  filter: (u: UnitInstance) => boolean,
  nodes: Map<string, UnitNode>,
) {
  const seen = new Set<string>()
  for (const u of state.units) {
    if (!filter(u)) continue
    seen.add(u.id)
    let node = nodes.get(u.id)
    if (!node) {
      node = createUnitNode(u)
      layer.addChild(node.container)
      nodes.set(u.id, node)
    } else {
      updateUnitNode(node, u)
      // Re-show if it had been hidden by fog last redraw.
      node.container.visible = true
    }
  }
  // Hide units the filter excluded; remove units that no longer exist.
  for (const [id, node] of nodes) {
    if (seen.has(id)) continue
    if (state.units.some((u) => u.id === id)) {
      node.container.visible = false
    } else {
      node.container.destroy({ children: true })
      nodes.delete(id)
    }
  }
}

export function syncBases(
  layer: Container,
  state: import('../types').GameState,
  filter: (b: BaseInstance) => boolean,
  nodes: Map<string, BaseNode>,
) {
  const seen = new Set<string>()
  for (const b of state.bases ?? []) {
    if (!filter(b)) continue
    seen.add(b.id)
    let node = nodes.get(b.id)
    if (!node) {
      node = createBaseNode(b)
      layer.addChild(node.container)
      nodes.set(b.id, node)
    } else {
      updateBaseNode(node, b)
      node.container.visible = true
    }
  }
  for (const [id, node] of nodes) {
    if (seen.has(id)) continue
    if ((state.bases ?? []).some((b) => b.id === id)) {
      node.container.visible = false
    } else {
      node.container.destroy({ children: true })
      nodes.delete(id)
    }
  }
}
