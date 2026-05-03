// Operational bottom bar: just the GO button.
// The previous tactical lock/resolve dance (LOCK BLUE → LOCK RED → RESOLVE)
// has been collapsed in favor of mission-driven simulation. The player
// plans missions, hits GO, the engine runs both sides until any halt.
import { useState } from 'react'
import { useStore } from '../store'


export function TurnBar() {
  const game = useStore((s) => s.game)
  if (!game) return null

  return (
    <div className="h-12 bg-panel border-t border-line flex items-center justify-end px-4 gap-3 font-mono text-[11px]">
      <GoButton />
    </div>
  )
}


function GoButton() {
  const game = useStore((s) => s.game)
  const runUntilHalt = useStore((s) => s.runUntilHalt)
  // Block re-clicks while:
  //   - posting /api/run (local `posting`)
  //   - replaying the returned events in MapStage (store.replaying)
  //   - or there are pending events queued for replay (store.pendingEvents)
  // Without these gates, clicking GO mid-animation overwrites pendingEvents
  // and corrupts the in-flight replay → random game-over triggers.
  const replaying = useStore((s) => s.replaying)
  const pendingEvents = useStore((s) => s.pendingEvents)
  const gameOver = useStore((s) => s.gameOver)
  const [posting, setPosting] = useState(false)
  const [lastHalt, setLastHalt] = useState<string | null>(null)
  const missionsCount = game?.missions ? Object.keys(game.missions).length : 0
  const busy = posting || replaying || pendingEvents != null
  const enabled = missionsCount > 0 && !busy && !gameOver

  const onClick = async () => {
    if (!enabled) return
    setPosting(true)
    setLastHalt(null)
    try {
      const r = await runUntilHalt(8)
      if (r.halts.length === 0) {
        setLastHalt(`ran ${r.turnsRun} turn(s) — no halt`)
      } else {
        const summary = r.halts
          .slice(0, 3)
          .map((h) => `${h.unit_id} (${h.reason})`)
          .join(', ')
        setLastHalt(`halt: ${summary}${r.halts.length > 3 ? ' …' : ''}`)
      }
    } catch (e: any) {
      setLastHalt(`error: ${e?.message ?? e}`)
    } finally {
      setPosting(false)
    }
  }

  return (
    <div className="flex items-center gap-3">
      {lastHalt && (
        <span className="text-[10px] font-mono text-mute max-w-[320px] truncate" title={lastHalt}>
          {lastHalt}
        </span>
      )}
      <button
        onClick={onClick}
        disabled={!enabled}
        title={
          missionsCount === 0
            ? 'Set at least one mission to enable auto-resolve'
            : `Auto-resolve until any halt fires (up to 8 turns; ${missionsCount} mission${missionsCount === 1 ? '' : 's'} active)`
        }
        className={[
          'h-8 px-5 rounded-sm border tracking-widest text-[11px]',
          'transition-colors',
          enabled
            ? 'bg-blue/10 border-blue text-blue hover:bg-blue/20'
            : 'border-line text-mute opacity-50 cursor-not-allowed',
        ].join(' ')}
      >
        {posting ? 'STARTING…' : (busy ? 'RUNNING…' : `GO (${missionsCount})`)}
      </button>
    </div>
  )
}
