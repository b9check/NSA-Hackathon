// Mirrors the engine.state pydantic models (JSON wire shape).
export type Side = 'blue' | 'red'
export type ViewMode = 'omniscient' | 'blue' | 'red'
export type Domain = 'land' | 'air' | 'sea' | 'amphib'
export type Terrain = 'open' | 'urban' | 'forest' | 'mountain' | 'water'

export interface HexCell {
  col: number
  row: number
  terrain: Terrain
}

export interface MapInfo {
  cols: number
  rows: number
  cells: HexCell[]
}

export interface SensorRef {
  key: string
  display: string
  modality: 'radar' | 'eo' | 'ir' | 'sigint' | 'sonar'
  range: number
  los_required: boolean
  detects_stealth: boolean
  target_domains: string[]
  emits: boolean
  notes: string
  is_active: boolean
}

export interface WeaponRef {
  key: string
  display: string
  kind: 'gun' | 'missile' | 'sam' | 'kamikaze' | 'bomb'
  range: number
  damage: number
  self_destruct: boolean
  /** Which target domains this weapon can damage. Empty/missing => any. */
  target_domains: string[]
  ammo: number
  notes: string
}

export interface UnitInstance {
  id: string
  type: string
  side: Side
  col: number
  row: number
  hp: number
  max_hp: number
  display: string
  role: string
  domain: Domain
  glyph: string
  speed: number
  sensor: number
  weapon: number
  cost: number
  stealth: boolean
  sensors: SensorRef[]
  weapons: WeaponRef[]
  endurance_minutes?: number
  time_in_air_minutes?: number
  home_base_id?: string | null
}

export interface BaseInstance {
  id: string
  type: string
  side: Side
  col: number
  row: number
  hp: number
  max_hp: number
  display: string
  role: string
  domain: 'land' | 'sea'
  glyph: string
  capacity: number
  spawns: string[]
  sensor: number
  weapon: number
  sensors: SensorRef[]
  weapons: WeaponRef[]
}

export interface VictoryConfig {
  hp_loss_threshold: number
  turn_cap: number
}

export type WinReason = 'hp_collapse' | 'turn_cap' | 'annihilation'

// ---- OPLAN: briefing + phased objectives ----
export type ObjectiveKind = 'destroy' | 'hold' | 'transit' | 'deny' | 'recon'

export interface Objective {
  label: string
  kind: ObjectiveKind
  target_ids: string[]
  target_hex?: [number, number] | null
  completed: boolean
}

export interface OpPhase {
  name: string
  description: string
  objectives: Objective[]
  completed: boolean
}

export interface Briefing {
  title: string
  situation: string
  mission: string
  rules_of_engagement: string
}

export interface Mission {
  unit_id: string
  target_hex: [number, number]
  roe: 'engage' | 'surveil' | 'avoid'
  radar_state: 'on' | 'off' | 'auto'
  halt_on_contact: boolean
  halt_on_low_hp: boolean
  halt_on_no_ammo: boolean
  max_turns: number
  intent: string
}

export interface Contact {
  contact_id: string
  target_kind: 'unit' | 'base'
  believed_col: number
  believed_row: number
  position_uncertainty_hexes: number
  existence: number              // P(real), 0..1
  class_probs: Record<string, number>   // platform-key -> P(class)
  last_refined_turn: number
  currently_observed: boolean
  contributing_sensor_ids: string[]
  dominant_modality: string
}

export interface GameState {
  name: string
  seed: number
  turn: number
  turn_limit: number
  map: MapInfo
  units: UnitInstance[]
  bases: BaseInstance[]
  victory: VictoryConfig
  starting_total?: Record<string, number>
  starting_hp?: Record<string, number>
  winner?: 'blue' | 'red' | 'draw' | null
  win_reason?: WinReason | null
  contacts?: { blue: Contact[]; red: Contact[] }
  missions?: Record<string, Mission>
  sim_clock_minutes?: number
  minutes_per_turn?: number
  briefing?: Briefing | null
  phases?: OpPhase[]
  current_phase?: number
}


// Mirrors engine/orders.py at the wire level.
export type OrderKind =
  | 'MOVE'
  | 'STRIKE'
  | 'SCOUT'
  | 'OVERWATCH'
  | 'HOLD'

export type Order =
  | { kind: 'MOVE'; unit_id: string; target_hex: [number, number]; intent?: string }
  | { kind: 'STRIKE'; unit_id: string; target_hex: [number, number]; intent?: string }
  | { kind: 'SCOUT'; unit_id: string; intent?: string }
  | { kind: 'OVERWATCH'; unit_id: string; intent?: string }
  | { kind: 'HOLD'; unit_id: string; intent?: string }


export interface TurnInfo {
  turn: number
  blue_score: number
  red_score: number
  blue_locked: boolean
  red_locked: boolean
  blue_orders_count: number
  red_orders_count: number
  pending_units: { blue: string[]; red: string[] }
}
