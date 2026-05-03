import { create } from 'zustand'
import type {
  GameState,
  Order,
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

  /** Per-side AI controller. 'manual' = humans drive the UI; any other
   *  value names a registered server-side controller (random / heuristic /
   *  llm). The server owns the truth; we mirror it locally for fast UI. */
  controllers: { blue: ControllerKind; red: ControllerKind }
  /** Last completed AI turn's reasoning per side, fetched after /api/ai/play
   *  returns. Drives the ReasoningPanel. */
  aiReasoning: { blue: AIReasoning | null; red: AIReasoning | null }
  /** Set while an AI side is actively producing orders, so UI can show a
   *  "thinking…" badge and the click-handler can bail. */
  aiThinking: { blue: boolean; red: boolean }

  // Battle narrative — full event history, prefixed with the turn each
  // event was emitted on. Capped at 5000 so a 30-turn match keeps every
  // event from turn 1 onward (player can scroll back to game start).
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
  /** Free action — flip a unit's radar ON/OFF, then refetch state.
   *  Doesn't consume a turn order; persists immediately on the server. */
  toggleSensor: (unitId: string, sensorKey: string, active: boolean) => Promise<void>

  /** Set the controller for a side ('manual' / 'random' / 'heuristic' / 'llm'). */
  setController: (side: 'blue' | 'red', kind: ControllerKind) => Promise<void>
  /** Trigger the server to run the AI for the given side, lock its orders. */
  playSideWithAI: (side: 'blue' | 'red') => Promise<void>
  /** End the current game (force-forfeit if no winner yet) + run the
   *  reflect pass to extract lessons into the memory store. */
  endGameAndReflect: (opts?: { force?: boolean }) => Promise<void>
  /** Lessons popped after the most recent endGame call. UI renders these
   *  in a one-time drawer. */
  lastLessons: Array<{ id: string; claim: string; tags?: any; side?: string; outcome?: string }>
  reflecting: boolean
  showLessonsDrawer: boolean
  dismissLessonsDrawer: () => void

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


/** Kinds of side-controller. 'manual' = human submits via UI. */
export type ControllerKind = 'manual' | 'random' | 'heuristic' | 'llm'


/** Reasoning trace returned by /api/ai/play and cached server-side. */
export interface AIReasoning {
  controller: string
  turn: number | null
  summary: string
  decisions: Array<{
    unit_id: string
    kind?: string
    action_id?: number
    target_hex?: [number, number] | null
    intent?: string
    rationale?: string
  }>
  fallback: boolean
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

// Persist the battle log so a page reload mid-match doesn't wipe history.
// Stored as { name, seed, log } JSON; only restored when the loaded
// scenario's (name, seed) match — otherwise discarded (regen / swap).
const LOG_KEY = 'wargame.eventLog'
function persistLog(game: GameState | null, log: AnnotatedEvent[]) {
  try {
    if (!game) { localStorage.removeItem(LOG_KEY); return }
    localStorage.setItem(LOG_KEY, JSON.stringify({
      name: game.name, seed: game.seed, log,
    }))
  } catch {}
}
function loadPersistedLog(game: GameState | null): AnnotatedEvent[] {
  if (!game) return []
  try {
    const raw = localStorage.getItem(LOG_KEY)
    if (!raw) return []
    const p = JSON.parse(raw)
    if (p && p.name === game.name && p.seed === game.seed && Array.isArray(p.log)) {
      return p.log
    }
    return []
  } catch { return [] }
}

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
  controllers: { blue: 'manual', red: 'manual' },
  aiReasoning: { blue: null, red: null },
  aiThinking: { blue: false, red: false },
  lastLessons: [],
  reflecting: false,
  showLessonsDrawer: false,
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
      try { localStorage.removeItem(LOG_KEY) } catch {}
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
      try { localStorage.removeItem(LOG_KEY) } catch {}
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

  toggleSensor: async (unitId, sensorKey, active) => {
    try {
      await fetchJson('/api/sensor/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unit_id: unitId, sensor_key: sensorKey, active }),
      })
      await get().refetchState()
    } catch (e) {
      console.error('toggleSensor failed', e)
    }
  },

  setController: async (side, kind) => {
    try {
      const r = await fetchJson<{ ok: boolean; controllers: Record<'blue'|'red', ControllerKind> }>(
        '/api/ai/controller', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ side, kind }),
        },
      )
      // Mirror server truth.
      set({ controllers: { ...get().controllers, ...r.controllers } })
    } catch (e) {
      console.error('setController failed', e)
    }
  },

  endGameAndReflect: async (opts) => {
    set({ reflecting: true })
    try {
      const r = await fetchJson<{
        ok: boolean
        winner: string | null
        win_reason: string | null
        lessons: Array<{ id: string; claim: string; tags?: any; side?: string; outcome?: string }>
        game_id: string
      }>('/api/game/end', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: !!(opts?.force) }),
      })
      set({
        lastLessons: r.lessons || [],
        showLessonsDrawer: true,
      })
      // Sync the in-memory state.json so the EndGameOverlay reflects the
      // forced winner immediately.
      await get().refetchState()
    } catch (e) {
      console.error('endGameAndReflect failed', e)
    } finally {
      set({ reflecting: false })
    }
  },

  dismissLessonsDrawer: () => set({ showLessonsDrawer: false }),

  playSideWithAI: async (side) => {
    set((s) => ({ aiThinking: { ...s.aiThinking, [side]: true } }))
    try {
      const r = await fetchJson<{
        ok: boolean
        side: string
        controller: string
        orders_count: number
        summary: string
        decisions: AIReasoning['decisions']
        fallback: boolean
      }>('/api/ai/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side }),
      })
      set((s) => ({
        aiReasoning: {
          ...s.aiReasoning,
          [side]: {
            controller: r.controller,
            turn: get().turnInfo?.turn ?? null,
            summary: r.summary,
            decisions: r.decisions,
            fallback: r.fallback,
          },
        },
      }))
      // Server already locked the side; refresh turn meta so the UI sees it.
      await get().refetchTurn()
    } catch (e) {
      console.error('playSideWithAI failed', e)
    } finally {
      set((s) => ({ aiThinking: { ...s.aiThinking, [side]: false } }))
    }
  },

  refetchState: async () => {
    const v = get().assetVersion
    const game = await fetchJson<GameState>('/state.json?v=' + v)
    // First load (no events in memory): hydrate the battle log from
    // localStorage if it matches this scenario. Subsequent refetches
    // (after a resolve) leave the in-memory log alone — we already
    // appended the new events in resolveTurn.
    const cur = get()
    const next: Partial<AppState> = { game }
    if (cur.eventLog.length === 0) {
      const persisted = loadPersistedLog(game)
      if (persisted.length > 0) next.eventLog = persisted
    }
    set(next as any)
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
      // If the OTHER side is non-manual, auto-fire its AI now. The server
      // will run the controller, submit, and lock — we just refresh.
      const other: 'blue' | 'red' = side === 'blue' ? 'red' : 'blue'
      const otherKind = get().controllers[other]
      const otherTurn = get().turnInfo
      const otherAlreadyLocked =
        other === 'blue' ? otherTurn?.blue_locked : otherTurn?.red_locked
      if (otherKind !== 'manual' && !otherAlreadyLocked) {
        await get().playSideWithAI(other)
      }
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
          eventLog: [...get().eventLog, ...annotated].slice(-5000),
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
          eventLog: [...get().eventLog, ...annotated].slice(-5000),
          selectedUnitId: null,
          viewMode: 'omniscient',
        })
      }
      // Persist log after each resolve so a reload mid-match keeps
      // history. Keyed by current scenario (name + seed); regen / swap
      // produce a different key and discard the old log on hydrate.
      persistLog(get().game, get().eventLog)
    } catch (e) {
      console.error('resolveTurn failed', e)
    } finally {
      set({ resolving: false })
    }
  },

  setPendingEvents: (events) => set({ pendingEvents: events }),
  setReplaying: (b) => set({ replaying: b }),

  onReplayComplete: async () => {
    const hr = get().hotseatReplay
    if (!hr) {
      // Normal flow: refetch resolver truth FIRST so the next render has
      // post-resolve unit positions, THEN unblock interaction. Otherwise
      // a click during the brief stale window finds the wrong unit and
      // the move-range check fires with absurd distances.
      set({ pendingEvents: null })
      await get().refetchState().catch(() => {})
      set({ replaying: false })
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
      // Pass 2 done — fetch the resolver's truth THEN clear replaying,
      // so we never expose the stale pre-resolve clone to clicks.
      set({
        pendingEvents: null,
        hotseatReplay: { ...hr, phase: 'settle' },
        viewMode: 'blue',
      })
      await get().refetchState().catch(() => {})
      set({ replaying: false, hotseatReplay: null })
    } else {
      // Already settling — no-op safeguard.
      set({ pendingEvents: null, replaying: false, hotseatReplay: null })
    }
  },

  resetMatch: () => {
    try { localStorage.removeItem(LOG_KEY) } catch {}
    set({
      gameOver: null,
      eventLog: [],
      pendingOrders: {},
      targeting: null,
      selectedUnitId: null,
      hotseatReplay: null,
    })
  },

  endMatch: (info) => set({ gameOver: info }),
}))
