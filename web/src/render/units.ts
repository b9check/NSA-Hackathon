// Per-unit / per-base Pixi node factories + reconcilers.
//
// Each unit and base now owns a Container that we transform (position, scale,
// alpha, rotation) for animation. The scene graph reconciles against state on
// each redraw: matching ids are updated in place; new ids spawn fresh nodes;
// stale ids are flagged for removal so the death-animation system can play
// them out before destroy().
import { Container, Graphics, Text, TextStyle } from 'pixi.js'
import type { BaseInstance, Side, UnitInstance } from '../types'
import { COLORS, SIDE_COLOR, SIDE_DIM } from '../theme'
import { HEX_SIZE, hexToPixel } from '../hex'

export interface UnitNode {
  container: Container
  shape: Graphics
  glyph: Text
  hpBar: Graphics
  // Last rendered values, so we don't redraw if nothing changed.
  lastHp: number
  lastMaxHp: number
}

export interface BaseNode {
  container: Container
  shape: Graphics
  glyph: Text
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
  const color = SIDE_COLOR[unit.side]
  const r = HEX_SIZE * 0.55
  if (unit.side === 'blue') {
    g.roundRect(-r, -r * 0.7, r * 2, r * 1.4, 5)
      .fill({ color: COLORS.bg, alpha: 0.85 })
      .stroke({ color, width: 2 })
  } else {
    g.poly([0, -r, r, 0, 0, r, -r, 0])
      .fill({ color: COLORS.bg, alpha: 0.85 })
      .stroke({ color, width: 2 })
  }
  if (unit.stealth) {
    g.circle(r * 0.85, -r * 0.6, 2.4).fill(COLORS.fg)
  }
}

function drawBaseShape(g: Graphics, base: BaseInstance) {
  g.clear()
  const color = SIDE_DIM[base.side]
  const r = HEX_SIZE * 0.62
  g.poly([
    -r,         r * 0.55,
    r,          r * 0.55,
    r,          -r * 0.15,
    0,          -r * 0.65,
    -r,         -r * 0.15,
  ])
    .fill({ color: COLORS.bg, alpha: 0.85 })
    .stroke({ color, width: 2 })
}

export function createUnitNode(unit: UnitInstance): UnitNode {
  const container = new Container()
  container.eventMode = 'none' // hit-test stays on the stage (brute force)
  container.label = `unit:${unit.id}`
  const shape = new Graphics()
  drawUnitShape(shape, unit)
  container.addChild(shape)
  const glyph = new Text({
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
  if (node.glyph.text !== unit.glyph) {
    node.glyph.text = unit.glyph
  }
}

export function createBaseNode(base: BaseInstance): BaseNode {
  const container = new Container()
  container.eventMode = 'none'
  container.label = `base:${base.id}`
  const shape = new Graphics()
  drawBaseShape(shape, base)
  container.addChild(shape)
  const glyph = new Text({
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
