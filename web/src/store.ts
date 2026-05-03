import { create } from 'zustand'
import type {
  GameState,
  Mission,
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
  kind: 'MOVE' | 'STRIKE' | 'SCOUT' | 'CAPTURE' | 'MISSION'
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
  // Continuous-time sim controls. `playing` is the user-set intent;
  // the loop checks this between ticks to decide whether to schedule
  // another. `simSpeed` scales animation timings (1× = real-time
  // animations; 60× = near-instant). `lastPauseReason` is shown in the
  // bottom bar when the loop auto-pauses.
  playing: boolean
  simSpeed: number          // 1 | 5 | 15 | 60
  lastPauseReason: string | null
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

  // Mission flow
  setMission: (m: Mission) => Promise<void>
  clearMission: (unitId: string) => Promise<void>
  /** Commit a mission target picked from the map. If a mission already
   *  exists for the unit, only the target_hex is updated (preserves ROE
   *  and halt flags). Otherwise creates a new mission with sensible defaults. */
  commitMissionTarget: (unitId: string, hex: [number, number]) => Promise<void>
  runUntilHalt: (maxTurns?: number) => Promise<{ halts: Array<{unit_id: string; reason: string}>; turnsRun: number }>

  // Continuous-time controls
  play: () => Promise<void>
  pause: (reason?: string) => void
  setSpeed: (s: number) => void

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
  playing: false,
  simSpeed: 30,            // start at 30× — feels like an op center, not real-time
  lastPauseReason: null,
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

  setMission: async (m) => {
    await fetchJson('/api/mission', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(m),
    })
    set({ assetVersion: get().assetVersion + 1 })
    await get().refetchState()
  },

  commitMissionTarget: async (unitId, hex) => {
    const existing = get().game?.missions?.[unitId]
    const m: Mission = existing
      ? { ...existing, target_hex: hex }
      : {
          unit_id: unitId,
          target_hex: hex,
          roe: 'engage',
          radar_state: 'auto',
          halt_on_contact: true,
          halt_on_low_hp: true,
          halt_on_no_ammo: true,
          // Engage missions interrupt frequently; surveil/avoid recon-style
          // missions are meant to play out — give them a long horizon.
          max_turns: 8,
          intent: '',
        }
    await get().setMission(m)
    set({ targeting: null })
  },

  clearMission: async (unitId) => {
    await fetchJson(`/api/mission/${encodeURIComponent(unitId)}`, { method: 'DELETE' })
    set({ assetVersion: get().assetVersion + 1 })
    await get().refetchState()
  },

  runUntilHalt: async (maxTurns = 8) => {
    // Legacy single-shot run. Kept for back-compat; the primary flow is
    // now play() which loops one tick at a time.
    const startTurn = get().game?.turn ?? 0
    const r: any = await fetchJson('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_turns: maxTurns }),
    })
    const events = (r?.events ?? []) as any[]
    const seqBase = get().eventLog.length
    const annotated: AnnotatedEvent[] = events.map(
      (e: any, i: number) => ({ ...e, _turn: startTurn, _seq: seqBase + i }),
    )
    set({
      pendingEvents: events,
      pendingOrders: {},
      targeting: null,
      eventLog: [...get().eventLog, ...annotated].slice(-200),
      selectedUnitId: null,
    })
    return { halts: r.halts ?? [], turnsRun: r.turns_run ?? 0 }
  },

  // ---- Continuous-time controls ----
  setSpeed: (s) => set({ simSpeed: Math.max(1, Math.min(60, s)) }),
  pause: (reason) => set({ playing: false, lastPauseReason: reason ?? null }),
  play: async () => {
    if (get().playing) return    // already in a play loop
    set({ playing: true, lastPauseReason: null, selectedUnitId: null, targeting: null })

    const SIGNIFICANT_AUTO_PAUSE = (events: any[], mySide: 'blue' | 'red'): string | null => {
      // Returns a non-null reason string if the events warrant a halt.
      for (const e of events) {
        if (!e || !e.type) continue
        if (e.type === 'destroyed') {
          if (e.side === mySide) return `friendly destroyed: ${e.entity_id ?? '?'}`
          // Don't auto-pause on enemy destruction — that's good news, keep going.
        }
        if (e.type === 'strike') {
          // Strikes against your forces: pause. But only if any of the targets
          // hit are friendly (server doesn't tell us directly; check by id prefix).
          for (const tid of e.targets_hit ?? []) {
            if (typeof tid === 'string' && tid.startsWith(`${mySide}-`)) {
              return `friendly under fire: ${tid}`
            }
          }
        }
      }
      return null
    }

    while (get().playing) {
      // Wait if a replay is still flushing (animation pipeline busy).
      if (get().replaying || get().pendingEvents != null) {
        await new Promise((r) => setTimeout(r, 50))
        continue
      }
      // No missions = nothing to do — pause politely.
      const game = get().game
      if (!game) {
        get().pause('no game state')
        return
      }
      if (!game.missions || Object.keys(game.missions).length === 0) {
        get().pause('no missions set')
        return
      }
      if (game.winner) {
        get().pause('game over')
        return
      }

      // Capture pre-tick contact ids per side so we can detect "first contact"
      // after the tick even if the engine missed a halt-on-contact.
      const preContacts = {
        blue: new Set((game.contacts?.blue ?? []).map((c) => c.contact_id)),
        red: new Set((game.contacts?.red ?? []).map((c) => c.contact_id)),
      }

      let r: any
      try {
        r = await fetchJson('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ max_turns: 1 }),
        })
      } catch (err: any) {
        get().pause(`error: ${err?.message ?? err}`)
        return
      }

      const events = (r?.events ?? []) as any[]
      const halts = r?.halts ?? []
      const startTurn = game.turn

      // Pipe events through the replay pipeline so animations play.
      const seqBase = get().eventLog.length
      const annotated: AnnotatedEvent[] = events.map(
        (e: any, i: number) => ({ ...e, _turn: startTurn, _seq: seqBase + i }),
      )
      set({
        pendingEvents: events,
        pendingOrders: {},
        eventLog: [...get().eventLog, ...annotated].slice(-200),
      })

      // Wait for the animation to flush. The replay subscriber in MapStage
      // calls onReplayComplete which clears pendingEvents and refetches state.
      // Poll until that happens.
      while (get().pendingEvents != null || get().replaying) {
        await new Promise((r) => setTimeout(r, 30))
        if (!get().playing) return
      }

      // Re-fetch was triggered by onReplayComplete; ensure we have fresh state.
      // Auto-pause checks:
      const myView = get().viewMode
      const mySide: 'blue' | 'red' = myView === 'red' ? 'red' : 'blue'

      // 1) Halt fired (engine-level halt)
      if (halts.length > 0) {
        const top = halts[0]
        get().pause(`halt: ${top.unit_id} (${top.reason})`)
        return
      }
      // 2) Significant event in this tick (friendly damage / destruction)
      const evReason = SIGNIFICANT_AUTO_PAUSE(events, mySide)
      if (evReason) {
        get().pause(evReason)
        return
      }
      // 3) New contact for player's side (intel surprise)
      const newGame = get().game
      if (newGame) {
        const postContacts = new Set(
          (newGame.contacts?.[mySide] ?? []).map((c: any) => c.contact_id),
        )
        for (const cid of postContacts) {
          if (!preContacts[mySide].has(cid)) {
            get().pause(`new contact: ${cid}`)
            return
          }
        }
      }
      // 4) Game over — stop on next iteration's check.
      // Otherwise: continue loop, take next tick. simSpeed controls cadence
      // by spacing the loop iterations. At 60×, no extra delay (animations
      // are the bottleneck). At 1×, one tick per ~30 sim-minutes wall-time.
      const speed = get().simSpeed
      // 30 sim-min/turn ÷ speed × ms/sec ≈ delay between starts of each tick.
      // Practical floor of ~50ms so 60× doesn't pegasus the server.
      const delayMs = Math.max(50, Math.round((30 * 60_000) / Math.max(1, speed)))
      await new Promise((r) => setTimeout(r, delayMs))
    }
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
