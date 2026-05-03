// Per-platform animation timing. ms-per-hex for movement; fixed envelopes
// for strikes / scout reveals / death sequences.
import type { UnitInstance } from '../types'


// unit_type -> ms per hex traversed during MOVE animation
const MOVE_MS_PER_HEX: Record<string, number> = {
  // Air — fast movers
  fighter:      300,
  bomber:       450,   // bigger plane, slightly heavier feel
  scout_drone:  350,
  strike_drone: 250,   // kamikaze, snappy
  // Sea
  destroyer:    600,
  // Ground
  armor:        500,
  infantry:     600,
  missile_launcher: 0, // stationary
}


export const STRIKE_TRACER_MS = 180    // missile flight to target
export const STRIKE_IMPACT_MS = 250    // particles + popup tail
export const DEATH_MS = 700            // total death sequence
export const SCOUT_REVEAL_MS = 300     // reveal blip animation
export const DAMAGE_FLASH_MS = 220


/** Wallclock ms to traverse one hex. 0 = unit can't move (stationary). */
export function moveMsPerHex(unit: UnitInstance): number {
  const t = MOVE_MS_PER_HEX[unit.type]
  return t ?? (unit.domain === 'air' ? 350 : unit.domain === 'sea' ? 600 : 600)
}
