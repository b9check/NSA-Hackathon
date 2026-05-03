// Pixi-side palette (numeric hex). Keep aligned with tailwind.config.js.
import type { Terrain } from './types'

export const COLORS = {
  bg:      0x0A0E14,
  panel:   0x141B26,
  line:    0x243044,
  fg:      0xE6EAF2,
  mute:    0x7A879A,
  blue:    0x4DA3FF,
  bluedim: 0x2C6FB3,
  red:     0xFF4D5E,
  reddim:  0xB33745,
  amber:   0xFFB84D,
  green:   0x4DD18F,
} as const

export const TERRAIN_FILL: Record<Terrain, number> = {
  water:    0x0E1A2E,
  open:     0x223028,
  urban:    0x2E3645,
  forest:   0x18301F,
  mountain: 0x3A342A,
}

export const TERRAIN_DETAIL: Record<Terrain, number> = {
  water:    0x1A2D4A,
  open:     0x2D4538,
  urban:    0x3F4A60,
  forest:   0x254A2C,
  mountain: 0x6A5C44,
}

export const SIDE_COLOR: Record<'blue' | 'red', number> = {
  blue: COLORS.blue,
  red:  COLORS.red,
}

export const SIDE_DIM: Record<'blue' | 'red', number> = {
  blue: COLORS.bluedim,
  red:  COLORS.reddim,
}
