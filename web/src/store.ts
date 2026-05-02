import { create } from 'zustand'
import type { GameState, UnitInstance } from './types'

export interface RegionMeta {
  key: string
  name: string
  lat: number
  lng: number
  zoom: number
}

interface AppState {
  game: GameState | null
  selectedUnitId: string | null
  hoverHex: { col: number; row: number } | null
  regions: RegionMeta[]
  /** Bumps every time we successfully swap region; used as a cache-buster +
   *  React key so the Pixi stage fully remounts and reloads /terrain.png. */
  assetVersion: number
  swapping: boolean
  swapError: string | null

  setGame: (g: GameState) => void
  selectUnit: (id: string | null) => void
  setHover: (h: { col: number; row: number } | null) => void
  selectedUnit: () => UnitInstance | null

  loadRegions: () => Promise<void>
  swapRegion: (key: string) => Promise<void>
  refetchState: () => Promise<void>
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`)
  return (await r.json()) as T
}

export const useStore = create<AppState>((set, get) => ({
  game: null,
  selectedUnitId: null,
  hoverHex: null,
  regions: [],
  assetVersion: 1,
  swapping: false,
  swapError: null,

  setGame: (g) => set({ game: g }),
  selectUnit: (id) => set({ selectedUnitId: id }),
  setHover: (h) => set({ hoverHex: h }),
  selectedUnit: () => {
    const { game, selectedUnitId } = get()
    if (!game || !selectedUnitId) return null
    return game.units.find((u) => u.id === selectedUnitId) ?? null
  },

  loadRegions: async () => {
    try {
      const regions = await fetchJson<RegionMeta[]>('/api/regions')
      set({ regions })
    } catch (e) {
      console.warn('failed to load region list (server down?)', e)
      set({ regions: [] })
    }
  },

  swapRegion: async (key: string) => {
    set({ swapping: true, swapError: null })
    try {
      await fetchJson('/api/region/' + key, { method: 'POST' })
      // Bumping the version triggers App + MapStage to refetch / remount.
      set({
        assetVersion: get().assetVersion + 1,
        selectedUnitId: null,
      })
      await get().refetchState()
    } catch (e: any) {
      set({ swapError: String(e?.message ?? e) })
    } finally {
      set({ swapping: false })
    }
  },

  refetchState: async () => {
    const v = get().assetVersion
    const game = await fetchJson<GameState>('/state.json?v=' + v)
    set({ game })
  },
}))
