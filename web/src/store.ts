import { create } from 'zustand'
import type { GameState, UnitInstance } from './types'

interface AppState {
  game: GameState | null
  selectedUnitId: string | null
  hoverHex: { col: number; row: number } | null
  setGame: (g: GameState) => void
  selectUnit: (id: string | null) => void
  setHover: (h: { col: number; row: number } | null) => void
  selectedUnit: () => UnitInstance | null
}

export const useStore = create<AppState>((set, get) => ({
  game: null,
  selectedUnitId: null,
  hoverHex: null,
  setGame: (g) => set({ game: g }),
  selectUnit: (id) => set({ selectedUnitId: id }),
  setHover: (h) => set({ hoverHex: h }),
  selectedUnit: () => {
    const { game, selectedUnitId } = get()
    if (!game || !selectedUnitId) return null
    return game.units.find((u) => u.id === selectedUnitId) ?? null
  },
}))
