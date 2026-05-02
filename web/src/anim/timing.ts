// Per-platform animation timing. ms-per-hex for movement; fixed envelopes
// for strikes / scout reveals / death sequences.
import type { UnitInstance } from '../types'


// platform_key -> ms per hex traversed during MOVE animation
const MOVE_MS_PER_HEX: Record<string, number> = {
  // air, fast
  f35a:      600,
  j20:       600,
  // air, ISR / drones
  mq9:       700,
  recon_uav: 700,
  shahed:    350,   // loitering munition; treat as fast missile-like
  // surface, sea
  cg47:      1100,
  type055:   1100,
  // ground
  patriot:   0,     // stationary
  hq9:       0,     // stationary
  m1a2:      1100,
  mech_b:    1500,
  mech_r:    1500,
}


export const STRIKE_TRACER_MS = 250    // missile flight to target
export const STRIKE_IMPACT_MS = 350    // particles + popup tail
export const DEATH_MS = 1000           // total death sequence
export const SCOUT_REVEAL_MS = 400     // reveal blip animation
export const DAMAGE_FLASH_MS = 300


/** Wallclock ms to traverse one hex. 0 = unit can't move (stationary). */
export function moveMsPerHex(unit: UnitInstance): number {
  const t = MOVE_MS_PER_HEX[unit.type]
  return t ?? (unit.domain === 'air' ? 700 : unit.domain === 'sea' ? 1200 : 1300)
}
