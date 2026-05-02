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

export interface UnitInstance {
  id: string
  type: string
  side: Side
  col: number
  row: number
  hp: number
  display: string
  domain: Domain
  glyph: string
  speed: number
  sensor: number
  weapon: number
  cost: number
  stealth: boolean
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
  victory: VictoryConfig
}
