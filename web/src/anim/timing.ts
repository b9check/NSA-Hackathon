// Per-platform animation timing. ms-per-hex for movement; fixed envelopes
// for strikes / scout reveals / death sequences.
import type { UnitInstance } from '../types'


// unit_type -> ms per hex traversed during MOVE animation
const MOVE_MS_PER_HEX: Record<string, number> = {
  // Air — fast movers
  fighter:      180,
  bomber:       260,
  scout_drone:  200,
  strike_drone: 150,   // kamikaze, snappy
  // Sea
  destroyer:    340,
  // Ground
  armor:        300,
  infantry:     340,
  missile_launcher: 0, // stationary
}


export const STRIKE_TRACER_MS = 130    // missile flight to target
export const STRIKE_IMPACT_MS = 180    // particles + popup tail
export const DEATH_MS = 500            // total death sequence
export const SCOUT_REVEAL_MS = 220     // reveal blip animation
export const DAMAGE_FLASH_MS = 160


/** Wallclock ms to traverse one hex. 0 = unit can't move (stationary). */
export function moveMsPerHex(unit: UnitInstance): number {
  const t = MOVE_MS_PER_HEX[unit.type]
  return t ?? (unit.domain === 'air' ? 200 : unit.domain === 'sea' ? 340 : 340)
}
