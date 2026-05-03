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

export interface Objective {
  col: number
  row: number
}

export interface MapInfo {
  cols: number
  rows: number
  cells: HexCell[]
  objective_hexes: Objective[]
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
}

export interface WeaponRef {
  key: string
  display: string
  kind:
    | 'aam'
    | 'asm_air'
    | 'asm_ship'
    | 'sam'
    | 'gun_naval'
    | 'gun_armor'
    | 'manpads'
    | 'loitering'
  range: number
  pkill: Record<string, number>
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
  capture_hold_turns: number
  combat_power_threshold: number
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
  objective_points?: Record<string, number>
}


// Mirrors engine/orders.py at the wire level.
export type OrderKind =
  | 'MOVE'
  | 'STRIKE'
  | 'SCOUT'
  | 'OVERWATCH'
  | 'HOLD'
  | 'CAPTURE'

export type Order =
  | { kind: 'MOVE'; unit_id: string; target_hex: [number, number]; intent?: string }
  | { kind: 'STRIKE'; unit_id: string; target_id: string; intent?: string }
  | { kind: 'SCOUT'; unit_id: string; target_hex: [number, number]; intent?: string }
  | { kind: 'OVERWATCH'; unit_id: string; intent?: string }
  | { kind: 'HOLD'; unit_id: string; intent?: string }
  | { kind: 'CAPTURE'; unit_id: string; target_hex: [number, number]; intent?: string }


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
