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
  /** When true, OMNI view is disabled and the game auto-snaps the
   *  camera back to the *queueing* side after RESOLVE TURN, so two
   *  humans can hot-seat without seeing each other's intel. */
  realGame: boolean
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
  /** Hot-seat two-POV replay. After resolve in real-game mode the
   *  animation plays once per side. The store snapshots the pre-resolve
   *  state, then orchestrates pass 1 (BLUE), pass 2 (RED), and finally
   *  refetches the resolver's truth and settles back to BLUE. */
  hotseatReplay: null | {
    phase: 'blue' | 'red' | 'settle'
    events: any[]
    /** Pre-resolve state, used to start each pass from the same point. */
    snapshot: GameState
  }

  gameOver: GameOverInfo | null

  // Battle narrative — full event history, prefixed with the turn each
  // event was emitted on. Trimmed to last 200.
  eventLog: AnnotatedEvent[]

  setGame: (g: GameState) => void
  selectUnit: (id: string | null) => void
  setHover: (h: { col: number; row: number } | null) => void
  selectedUnit: () => UnitInstance | null
  setViewMode: (m: ViewMode) => void
  toggleRealGame: () => void

  loadRegions: () => Promise<void>
  swapRegion: (key: string) => Promise<void>
  reroll: () => Promise<void>
  refetchState: () => Promise<void>
  toggleSensor: (unitId: string, sensorKey: string, active: boolean) => Promise<void>

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
  /** Called by MapStage when an animation pass completes. In hot-seat
   *  mode, advances the two-POV replay; in normal mode, refetches
   *  state.json and settles. */
  onReplayComplete: () => void

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

// Persist a single bit (real-game mode) across reloads. Stored as
// 'wargame.realGame' = '1' | '0'. Anything else = false.
const REAL_GAME_KEY = 'wargame.realGame'
const initialRealGame = (() => {
  try { return localStorage.getItem(REAL_GAME_KEY) === '1' } catch { return false }
})()

export const useStore = create<AppState>((set, get) => ({
  game: null,
  selectedUnitId: null,
  hoverHex: null,
  regions: [],
  assetVersion: 1,
  swapping: false,
  swapError: null,
  // If real-game was on last session, default the camera to BLUE rather
  // than OMNI (which is hidden in real-game).
  viewMode: initialRealGame ? 'blue' : 'omniscient',
  realGame: initialRealGame,
  turnInfo: null,
  pendingOrders: {},
  targeting: null,
  resolving: false,
  pendingEvents: null,
  replaying: false,
  hotseatReplay: null,
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
  setViewMode: (m) => set((s) => {
    // While real-game is on, OMNI is forbidden — coerce to 'blue'.
    const safe = s.realGame && m === 'omniscient' ? 'blue' : m
    return { viewMode: safe, selectedUnitId: null }
  }),
  toggleRealGame: () => set((s) => {
    const realGame = !s.realGame
    try { localStorage.setItem(REAL_GAME_KEY, realGame ? '1' : '0') } catch {}
    // When switching INTO real-game, kick the player off OMNI.
    const viewMode = realGame && s.viewMode === 'omniscient' ? 'blue' : s.viewMode
    return { realGame, viewMode, selectedUnitId: null }
  }),

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
      // Also clear per-match state (event log + queued orders + game-over)
      // so the footer doesn't show events from a different scenario.
      set({
        assetVersion: get().assetVersion + 1,
        selectedUnitId: null,
        eventLog: [],
        pendingOrders: {},
        targeting: null,
        gameOver: null,
        hotseatReplay: null,
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
      // Same per-match reset as swapRegion — fresh seed = new scenario,
      // old battle log no longer applies.
      set({
        assetVersion: get().assetVersion + 1,
        selectedUnitId: null,
        eventLog: [],
        pendingOrders: {},
        targeting: null,
        gameOver: null,
        hotseatReplay: null,
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

  toggleSensor: async (unitId, sensorKey, active) => {
    await fetchJson('/api/sensor/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unit_id: unitId, sensor_key: sensorKey, active }),
    })
    // Bump asset version so the next refetch dodges the static file cache.
    set({ assetVersion: get().assetVersion + 1 })
    await get().refetchState()
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
      const baseState = get().game
      const realGame = get().realGame
      if (realGame && baseState) {
        // Hot-seat: animate twice — once from BLUE POV, once from RED.
        // Snapshot the pre-resolve state so each pass starts from the
        // same baseline. MapStage's onReplayComplete advances the
        // phases (blue -> red -> settle).
        //
        // CRITICAL: keep the pristine snapshot SEPARATE from the game
        // state pass 1 animates against. playEvents mutates HP/positions
        // on the game it's handed; if we shared the same reference,
        // pass 2 would clone the post-pass-1 (mutated) state and have
        // nothing to animate.
        const json = JSON.stringify(baseState)
        const pass1Game = JSON.parse(json) as GameState
        const pristine = JSON.parse(json) as GameState
        set({
          pendingOrders: {},
          targeting: null,
          pendingEvents: [...events],
          eventLog: [...get().eventLog, ...annotated].slice(-200),
          selectedUnitId: null,
          viewMode: 'blue',
          game: pass1Game,
          hotseatReplay: {
            phase: 'blue',
            events: [...events],
            snapshot: pristine,
          },
        })
      } else {
        // Normal flow — single pass; MapStage refetches state on
        // completion and settles to OMNI.
        set({
          pendingOrders: {},
          targeting: null,
          pendingEvents: events,
          eventLog: [...get().eventLog, ...annotated].slice(-200),
          selectedUnitId: null,
          viewMode: 'omniscient',
        })
      }
    } catch (e) {
      console.error('resolveTurn failed', e)
    } finally {
      set({ resolving: false })
    }
  },

  setPendingEvents: (events) => set({ pendingEvents: events }),
  setReplaying: (b) => set({ replaying: b }),

  onReplayComplete: () => {
    const hr = get().hotseatReplay
    if (!hr) {
      // Normal flow: clear and refetch.
      set({ pendingEvents: null, replaying: false })
      get().refetchState().catch(() => {})
      return
    }
    if (hr.phase === 'blue') {
      // Pass 1 done — start RED pass from the same snapshot.
      const fresh = JSON.parse(JSON.stringify(hr.snapshot))
      set({
        replaying: false,
        viewMode: 'red',
        game: fresh,
        pendingEvents: [...hr.events],
        hotseatReplay: { ...hr, phase: 'red' },
      })
    } else if (hr.phase === 'red') {
      // Pass 2 done — refetch resolver truth and settle on BLUE.
      set({
        replaying: false,
        pendingEvents: null,
        hotseatReplay: { ...hr, phase: 'settle' },
        viewMode: 'blue',
      })
      get().refetchState().finally(() => {
        set({ hotseatReplay: null })
      })
    } else {
      // Already settling — no-op safeguard.
      set({ pendingEvents: null, replaying: false, hotseatReplay: null })
    }
  },

  resetMatch: () => set({
    gameOver: null,
    eventLog: [],
    pendingOrders: {},
    targeting: null,
    selectedUnitId: null,
    hotseatReplay: null,
  }),

  endMatch: (info) => set({ gameOver: info }),
}))
