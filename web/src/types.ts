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
}
