import { useEffect } from 'react'
import { useStore } from './store'
import { MapStage } from './components/MapStage'
import { TopBar, RightRail } from './components/HUD'
import { TurnBar } from './components/TurnBar'
import { EndGameOverlay } from './components/EndGameOverlay'
import { BattleLog } from './components/BattleLog'


/** Watch the engine-authoritative game.winner field and surface the
 *  EndGameOverlay once the resolver declares a victor. */
function useWinCheck() {
  const game = useStore((s) => s.game)
  const turnInfo = useStore((s) => s.turnInfo)
  const replaying = useStore((s) => s.replaying)
  const gameOver = useStore((s) => s.gameOver)
  const endMatch = useStore((s) => s.endMatch)
  useEffect(() => {
    if (!game || !game.winner || gameOver || replaying) return
    const startBlue = game.starting_hp?.blue ?? 0
    const startRed = game.starting_hp?.red ?? 0
    let blueHp = 0, redHp = 0
    for (const u of game.units) (u.side === 'blue' ? blueHp += u.hp : redHp += u.hp)
    for (const b of game.bases) (b.side === 'blue' ? blueHp += b.hp : redHp += b.hp)
    endMatch({
      winner: game.winner,
      reason: (game.win_reason ?? 'turn_cap') as any,
      blue_hp_pct: startBlue ? blueHp / startBlue : 0,
      red_hp_pct: startRed ? redHp / startRed : 0,
      turn: turnInfo?.turn ?? game.turn,
    })
  }, [game?.winner, game?.win_reason, replaying, gameOver])
}


function useGlobalShortcuts() {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Don't hijack typing in inputs
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) {
        return
      }
      const st = useStore.getState()

      // Cancel targeting / clear selection
      if (e.key === 'Escape') {
        if (st.targeting) st.cancelTargeting()
        else st.selectUnit(null)
        return
      }

      // Smart Space: lock current side, or resolve if both locked
      if (e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault()
        const ti = st.turnInfo
        if (!ti) return
        if (ti.blue_locked && ti.red_locked) {
          st.resolveTurn()
          return
        }
        const side: 'blue' | 'red' =
          st.viewMode === 'red' ? 'red' :
          ti.blue_locked ? 'red' : 'blue'
        st.lockSide(side)
        return
      }

      // Action shortcuts only fire when a controllable friendly unit is selected
      const u = st.selectedUnit()
      if (!u) return
      const playerSide: 'blue' | 'red' = st.viewMode === 'red' ? 'red' : 'blue'
      if (u.side !== playerSide) return

      // Gate each shortcut on the unit's capability so e.g. pressing S
      // on a weaponless scout drone doesn't enter STRIKE targeting (it
      // would just silently fail later when no hex is valid).
      const canMove   = u.speed > 0
      const canStrike = u.weapon > 0
      const canScout  = u.type === 'scout_drone'

      const k = e.key.toLowerCase()
      if (k === 'm' && canMove) st.startTargeting(u.id, 'MOVE')
      else if (k === 's' && canStrike) st.startTargeting(u.id, 'STRIKE')
      else if (k === 'v' && canScout) st.setOrder({ kind: 'SCOUT', unit_id: u.id })
      else if (k === 'o' && canStrike) st.setOrder({ kind: 'OVERWATCH', unit_id: u.id })
      else if (k === 'h') st.setOrder({ kind: 'HOLD', unit_id: u.id })
      else if (k === 'x') st.clearOrder(u.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}

export default function App() {
  const game = useStore((s) => s.game)
  const assetVersion = useStore((s) => s.assetVersion)
  const swapping = useStore((s) => s.swapping)
  const loadRegions = useStore((s) => s.loadRegions)
  const refetchState = useStore((s) => s.refetchState)

  useGlobalShortcuts()
  useWinCheck()

  // On mount: fetch the region list (for the picker) + the initial state.
  useEffect(() => {
    loadRegions()
    refetchState().catch((e) => console.error('initial state load failed', e))
  }, [loadRegions, refetchState])

  return (
    <div className="h-screen w-screen flex flex-col">
      <TopBar />
      <div className="flex-1 flex min-h-0 relative">
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex-1 min-h-0 overflow-auto bg-bg relative">
            {game ? (
              // Key by version so the Pixi stage fully unmounts on region swap
              // and re-loads /terrain.png with a fresh cache-busted URL.
              <MapStage key={assetVersion} />
            ) : (
              <div className="h-full flex items-center justify-center text-mute font-mono text-sm">
                loading state.json…
              </div>
            )}
            <HotseatPovBanner />
          </div>
          <TurnBar />
        </div>
        <RightRail />
        {swapping && <SwapOverlay />}
        <EndGameOverlay />
      </div>
      <BattleLog />
      <BottomBar />
    </div>
  )
}

function HotseatPovBanner() {
  const hr = useStore((s) => s.hotseatReplay)
  if (!hr || hr.phase === 'settle') return null
  const isBlue = hr.phase === 'blue'
  const cls = isBlue ? 'border-blue text-blue' : 'border-red text-red'
  return (
    <div
      className={[
        'absolute top-3 left-1/2 -translate-x-1/2 z-30',
        'h-8 px-4 inline-flex items-center gap-3 rounded-sm border',
        'bg-panel/90 backdrop-blur-sm font-mono text-[11px] tracking-widest',
        cls,
      ].join(' ')}
    >
      <span className="opacity-70">REPLAY</span>
      <span className="font-semibold">{hr.phase.toUpperCase()} POV</span>
      <span className="opacity-50">{isBlue ? '1 / 2' : '2 / 2'}</span>
    </div>
  )
}


function SwapOverlay() {
  return (
    <div className="absolute inset-0 bg-bg/70 backdrop-blur-sm flex items-center justify-center z-40">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-amber border-t-transparent rounded-full animate-spin" />
        <div className="text-[11px] font-mono tracking-[0.2em] text-amber">
          REGENERATING SCENARIO
        </div>
      </div>
    </div>
  )
}

function BottomBar() {
  const game = useStore((s) => s.game)
  const hover = useStore((s) => s.hoverHex)
  const selected = useStore((s) => s.selectedUnit())
  if (!game) return null
  const cell = hover
    ? game.map.cells.find((c) => c.col === hover.col && c.row === hover.row)
    : null
  return (
    <div className="h-8 bg-panel border-t border-line flex items-center px-5 text-[11px] font-mono text-mute gap-6">
      <span>SCENARIO {game.name}</span>
      <span>SEED {game.seed}</span>
      {hover && (
        <span>
          HEX ({hover.col},{hover.row}) — {cell ? cell.terrain : '—'}
        </span>
      )}
      {selected && (
        <span className="text-amber">SELECTED {selected.display}</span>
      )}
      <span className="ml-auto opacity-50">alex_game_engine · 0→1 build</span>
    </div>
  )
}
