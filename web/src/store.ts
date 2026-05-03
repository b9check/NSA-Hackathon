import { create } from 'zustand'
import type {
  GameState,
  Order,
  OrderKind,
  TurnInfo,
  UnitInstance,
  ViewMode,
} from './types'

export interface RegionMeta {
  key: string
  name: string
  lat: number
  lng: number
  zoom: number
}

/** A click on MOVE/STRIKE/SCOUT/CAPTURE puts the UI into targeting mode
 *  for that unit; the next valid map click commits the order. */
export interface TargetingMode {
  unitId: string
  kind: 'MOVE' | 'STRIKE' | 'SCOUT' | 'CAPTURE'
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
  viewMode: ViewMode
  // Turn flow
  turnInfo: TurnInfo | null
  pendingOrders: Record<string, Order> // keyed by unit_id
  targeting: TargetingMode | null
  resolving: boolean
  /** Set immediately after /api/resolve returns. MapStage subscribes,
   *  animates each event in order, then clears + refetches state.json. */
  pendingEvents: any[] | null
  /** True while MapStage is mid-replay; redraw() short-circuits so
   *  syncUnits doesn't snap positions and clobber move tweens. */
  replaying: boolean

  gameOver: GameOverInfo | null

  // Battle narrative — full event history, prefixed with the turn each
  // event was emitted on. Trimmed to last 200.
  eventLog: AnnotatedEvent[]

  setGame: (g: GameState) => void
  selectUnit: (id: string | null) => void
  setHover: (h: { col: number; row: number } | null) => void
  selectedUnit: () => UnitInstance | null
  setViewMode: (m: ViewMode) => void

  loadRegions: () => Promise<void>
  swapRegion: (key: string) => Promise<void>
  reroll: () => Promise<void>
  refetchState: () => Promise<void>

  // ---- Turn flow -------------------------------------------------
  /** Pull the latest turn meta (scores, locks, counts) from /api/turn. */
  refetchTurn: () => Promise<void>
  /** Set or replace the order for a unit. Local-only until lockSide(). */
  setOrder: (order: Order) => void
  /** Drop the queued order for a unit. */
  clearOrder: (unitId: string) => void
  /** Enter / exit targeting mode for one of MOVE/STRIKE/SCOUT/CAPTURE. */
  startTargeting: (unitId: string, kind: TargetingMode['kind']) => void
  cancelTargeting: () => void
  /** Submit + lock this side's queued orders to the server. */
  lockSide: (side: 'blue' | 'red') => Promise<void>
  /** Run the resolver if both sides are locked; ignored otherwise. */
  resolveTurn: () => Promise<void>

  setPendingEvents: (events: any[] | null) => void
  setReplaying: (b: boolean) => void

  resetMatch: () => void
  endMatch: (info: GameOverInfo | null) => void
}


export interface GameOverInfo {
  winner: 'blue' | 'red' | 'draw'
  reason: 'hp_collapse' | 'turn_cap' | 'annihilation'
  blue_hp_pct: number
  red_hp_pct: number
  turn: number
}


export type AnnotatedEvent = any & { _turn: number; _seq: number }

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
  viewMode: 'omniscient',
  turnInfo: null,
  pendingOrders: {},
  targeting: null,
  resolving: false,
  pendingEvents: null,
  replaying: false,
  gameOver: null,
  eventLog: [],

  setGame: (g) => set({ game: g }),
  selectUnit: (id) => set({ selectedUnitId: id }),
  setHover: (h) => set({ hoverHex: h }),
  selectedUnit: () => {
    const { game, selectedUnitId } = get()
    if (!game || !selectedUnitId) return null
    return game.units.find((u) => u.id === selectedUnitId) ?? null
  },
  setViewMode: (m) => set({ viewMode: m, selectedUnitId: null }),

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

  reroll: async () => {
    // No tile fetch -> tiny spinner; reuse swapping flag for the overlay.
    set({ swapping: true, swapError: null })
    try {
      await fetchJson('/api/reroll', { method: 'POST' })
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
    // Pull turn meta in parallel; don't block on failure.
    get().refetchTurn().catch(() => {})
  },

  refetchTurn: async () => {
    try {
      const t = await fetchJson<TurnInfo>('/api/turn')
      set({ turnInfo: t })
    } catch {
      set({ turnInfo: null })
    }
  },

  setOrder: (order) =>
    set((s) => ({
      pendingOrders: { ...s.pendingOrders, [order.unit_id]: order },
      targeting: null,
    })),

  clearOrder: (unitId) =>
    set((s) => {
      const next = { ...s.pendingOrders }
      delete next[unitId]
      return { pendingOrders: next }
    }),

  startTargeting: (unitId, kind) => set({ targeting: { unitId, kind } }),
  cancelTargeting: () => set({ targeting: null }),

  lockSide: async (side) => {
    const { pendingOrders, game } = get()
    if (!game) return
    // Only orders for this side
    const ownIds = new Set(
      game.units.filter((u) => u.side === side).map((u) => u.id),
    )
    const orders = Object.values(pendingOrders).filter((o) =>
      ownIds.has(o.unit_id),
    )
    try {
      await fetchJson('/api/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side, orders, lock: true }),
      })
      await get().refetchTurn()
    } catch (e) {
      console.error('lockSide failed', e)
    }
  },

  resolveTurn: async () => {
    const { turnInfo } = get()
    if (!turnInfo?.blue_locked || !turnInfo?.red_locked) return
    set({ resolving: true })
    try {
      const res: any = await fetchJson('/api/resolve', { method: 'POST' })
      const events = res?.events ?? []
      // Stamp every event with the turn it occurred in (server already
      // returns turn_started_at) and a monotonic sequence number for keys.
      const startTurn = res?.turn_started_at ?? get().turnInfo?.turn ?? 0
      const seqBase = get().eventLog.length
      const annotated: AnnotatedEvent[] = events.map(
        (e: any, i: number) => ({ ...e, _turn: startTurn, _seq: seqBase + i }),
      )
      // Stage events for the MapStage replay. It clears them when done
      // and triggers a refetchState() to snap to the resolver's truth.
      set({
        pendingOrders: {},
        targeting: null,
        pendingEvents: events,
        eventLog: [...get().eventLog, ...annotated].slice(-200),
      })
    } catch (e) {
      console.error('resolveTurn failed', e)
    } finally {
      set({ resolving: false })
    }
  },

  setPendingEvents: (events) => set({ pendingEvents: events }),
  setReplaying: (b) => set({ replaying: b }),

  resetMatch: () => set({ gameOver: null }),

  endMatch: (info) => set({ gameOver: info }),
}))
