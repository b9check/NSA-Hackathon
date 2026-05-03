// MIL-STD-2525-style symbology helpers.
//
// Frame shape encodes affiliation:
//   - friendly (blue)  -> rectangle (flat top/bottom)
//   - hostile  (red)   -> diamond  (rotated square)
// Inner glyph encodes function (air, ground, armor, SAM, sea, ...).
//
// We're "close enough" to MIL-STD-2525 — the goal is that a defense person
// nods, not strict spec compliance.
import { Graphics } from 'pixi.js'
import type { Side } from '../types'
import { COLORS, SIDE_COLOR } from '../theme'

/** Draw the affiliation frame. `size` is half-width of the bounding box. */
export function drawFrame(g: Graphics, side: Side, size: number) {
  const color = SIDE_COLOR[side]
  if (side === 'blue') {
    // Friendly rectangle (slightly wider than tall).
    g.rect(-size, -size * 0.7, size * 2, size * 1.4)
      .fill({ color: COLORS.bg, alpha: 0.85 })
      .stroke({ color, width: 2 })
  } else {
    // Hostile diamond.
    g.poly([0, -size, size, 0, 0, size, -size, 0])
      .fill({ color: COLORS.bg, alpha: 0.85 })
      .stroke({ color, width: 2 })
  }
}

/** Draw the function-modifier glyph centered in the frame.
 *  `size` is the frame's half-width; the glyph is sized to fit comfortably. */
export function drawFunctionGlyph(
  g: Graphics,
  unitType: string,
  side: Side,
  size: number,
) {
  const color = SIDE_COLOR[side]
  const fn = classifyFunction(unitType)
  const r = size * 0.55

  switch (fn) {
    case 'air': {
      // Upward chevron / wing — reads as "aircraft".
      g.poly([-r, r * 0.4, 0, -r * 0.55, r, r * 0.4])
        .stroke({ color, width: 2 })
      // Small fuselage tick.
      g.moveTo(0, -r * 0.55).lineTo(0, r * 0.55).stroke({ color, width: 1.5 })
      break
    }
    case 'armor': {
      // Horizontal pill (tank tread profile).
      g.roundRect(-r, -r * 0.35, r * 2, r * 0.7, r * 0.35)
        .stroke({ color, width: 2 })
      break
    }
    case 'infantry': {
      // Crossed lines (X) — classic infantry mark.
      g.moveTo(-r, -r * 0.6).lineTo(r, r * 0.6).stroke({ color, width: 2 })
      g.moveTo(r, -r * 0.6).lineTo(-r, r * 0.6).stroke({ color, width: 2 })
      break
    }
    case 'sam': {
      // Upward triangle / arrow — surface-to-air.
      g.poly([0, -r, r * 0.75, r * 0.5, -r * 0.75, r * 0.5])
        .stroke({ color, width: 2 })
      g.moveTo(0, -r).lineTo(0, r * 0.5).stroke({ color, width: 1.2 })
      break
    }
    case 'sea': {
      // Horizontal hull silhouette + small mast.
      g.poly([-r, 0, r, 0, r * 0.7, r * 0.45, -r * 0.7, r * 0.45])
        .stroke({ color, width: 2 })
      g.moveTo(0, 0).lineTo(0, -r * 0.7).stroke({ color, width: 1.5 })
      g.moveTo(-r * 0.25, -r * 0.5).lineTo(r * 0.25, -r * 0.5)
        .stroke({ color, width: 1.2 })
      break
    }
    default: {
      // Generic dot for unclassified.
      g.circle(0, 0, r * 0.35).stroke({ color, width: 1.5 })
    }
  }
}

type FnKind = 'air' | 'armor' | 'infantry' | 'sam' | 'sea' | 'unknown'

function classifyFunction(unitType: string): FnKind {
  const t = unitType.toLowerCase()
  // Air platforms.
  if (
    t.includes('fighter') || t.includes('bomber') ||
    t.includes('drone')   || t.includes('aircraft') ||
    t.includes('helo')    || t.includes('helicopter') ||
    t.includes('uav')     || t.includes('awacs')
  ) return 'air'
  // SAM / missile launchers.
  if (t.includes('sam') || t.includes('missile_launcher') || t.includes('launcher')) return 'sam'
  // Sea platforms.
  if (
    t.includes('destroyer') || t.includes('frigate') ||
    t.includes('carrier')   || t.includes('cruiser') ||
    t.includes('boat')      || t.includes('ship')    ||
    t.includes('submarine') || t.includes('sub')
  ) return 'sea'
  // Armor.
  if (t.includes('tank') || t.includes('armor') || t.includes('apc') || t.includes('ifv'))
    return 'armor'
  // Foot.
  if (t.includes('infantry') || t.includes('marine') || t.includes('squad') || t.includes('soldier'))
    return 'infantry'
  return 'unknown'
}
