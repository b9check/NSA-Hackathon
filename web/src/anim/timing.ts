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


// Combat feedback — generous linger so the player can register what happened.
// Previously these were too short to read; bumped for demo clarity.
export const STRIKE_TRACER_MS = 220    // missile flight to target
export const STRIKE_IMPACT_MS = 350    // particles + popup tail
export const DEATH_MS = 800            // total death sequence
export const SCOUT_REVEAL_MS = 360     // reveal blip animation
export const DAMAGE_FLASH_MS = 280


/** Wallclock ms to traverse one hex. 0 = unit can't move (stationary). */
export function moveMsPerHex(unit: UnitInstance): number {
  const t = MOVE_MS_PER_HEX[unit.type]
  return t ?? (unit.domain === 'air' ? 200 : unit.domain === 'sea' ? 340 : 340)
}
