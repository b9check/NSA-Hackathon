// The bar at the bottom of the map: match timer, turn counter, both
// sides' live scores, lock-orders / resolve-turn controls. State
// machine:
//
//   QUEUEING_BLUE  -> [LOCK BLUE]
//   BLUE_LOCKED    -> [LOCK RED]     auto-switches viewmode to RED
//   RED_LOCKED     -> [RESOLVE TURN] pulse animation
//
// RESOLVING -> spinner; cleared when /api/resolve returns + state refetches.
import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { HpStatus } from './Timer'


/** Smoothly interpolate to a numeric target. */
function useTweenedNumber(target: number, ms = 600): number {
  const [shown, setShown] = useState(target)
  const fromRef = useRef(target)
  useEffect(() => {
    const from = fromRef.current
    const to = target
    if (from === to) return
    const start = performance.now()
    let raf = 0
    const tick = () => {
      const t = Math.min(1, (performance.now() - start) / ms)
      const e = 1 - Math.pow(1 - t, 3) // easeOutCubic
      setShown(from + (to - from) * e)
      if (t < 1) raf = requestAnimationFrame(tick)
      else {
        fromRef.current = to
        setShown(to)
      }
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return shown
}

export function TurnBar() {
  // ALL hooks must run unconditionally on every render — keep them above
  // any early-returns. (Rules of hooks.)
  const game = useStore((s) => s.game)
  const turnInfo = useStore((s) => s.turnInfo)
  const viewMode = useStore((s) => s.viewMode)
  const setViewMode = useStore((s) => s.setViewMode)
  const lockSide = useStore((s) => s.lockSide)
  const resolveTurn = useStore((s) => s.resolveTurn)
  const resolving = useStore((s) => s.resolving)
  const pendingOrders = useStore((s) => s.pendingOrders)
  const controllers = useStore((s) => s.controllers)
  const aiThinking = useStore((s) => s.aiThinking)
  const runAITurn = useStore((s) => s.runAITurn)

  if (!game || !turnInfo) {
    return (
      <div className="h-12 bg-panel border-t border-line flex items-center px-5 text-[11px] font-mono text-mute">
        loading turn state…
      </div>
    )
  }

  const blueUnits = game.units.filter((u) => u.side === 'blue')
  const redUnits = game.units.filter((u) => u.side === 'red')
  // Local pending counts (haven't necessarily POSTed yet)
  const localBlue = blueUnits.filter((u) => pendingOrders[u.id]).length
  const localRed = redUnits.filter((u) => pendingOrders[u.id]).length

  const blueLocked = turnInfo.blue_locked
  const redLocked = turnInfo.red_locked
  const canResolve = blueLocked && redLocked && !resolving

  const lockBlue = async () => {
    await lockSide('blue')
    if (viewMode !== 'red') setViewMode('red')
  }
  const lockRed = async () => {
    await lockSide('red')
  }

  return (
    <div className="h-12 bg-panel border-t border-line flex items-center px-4 gap-3 font-mono text-[11px]">
      {/* HP bars (blue + red) — replaces the wallclock. */}
      <HpStatus />

      <div className="w-px h-5 bg-line" />

      {/* Turn counter */}
      <div className="flex items-baseline gap-2">
        <span className="text-mute tracking-widest">TURN</span>
        <span className="text-fg text-base font-semibold">{turnInfo.turn + 1}</span>
        <span className="text-mute">/ {game.turn_limit}</span>
      </div>

      <div className="w-px h-5 bg-line" />

      {/* Per-side controls: only render the LOCK pill for sides that the
          human is driving. AI sides lock automatically when triggered. */}
      {controllers.blue === 'manual' ? (
        <SideControl
          side="blue"
          score={turnInfo.blue_score}
          ordered={localBlue}
          total={blueUnits.length}
          locked={blueLocked}
          onLock={lockBlue}
          disabled={resolving}
        />
      ) : (
        <AISideStatus
          side="blue"
          locked={blueLocked}
          thinking={aiThinking.blue}
          score={turnInfo.blue_score}
        />
      )}

      {controllers.red === 'manual' ? (
        <SideControl
          side="red"
          score={turnInfo.red_score}
          ordered={localRed}
          total={redUnits.length}
          locked={redLocked}
          onLock={lockRed}
          disabled={resolving}
        />
      ) : (
        <AISideStatus
          side="red"
          locked={redLocked}
          thinking={aiThinking.red}
          score={turnInfo.red_score}
        />
      )}

      <div className="flex-1" />

      <EndGameButton />

      {/* Action button: in BOTH-AI mode, single RUN AI TURN; otherwise the
          existing RESOLVE TURN that requires both sides to be locked. */}
      {controllers.blue !== 'manual' && controllers.red !== 'manual' ? (
        <RunAITurnButton
          onRun={runAITurn}
          thinking={aiThinking.blue || aiThinking.red}
          resolving={resolving}
        />
      ) : (
        <button
          onClick={() => canResolve && resolveTurn()}
          disabled={!canResolve}
          className={[
            'h-8 px-4 rounded-sm border tracking-widest text-[11px]',
            'transition-colors',
            canResolve
              ? 'bg-amber/10 border-amber text-amber animate-pulse hover:bg-amber/20'
              : 'border-line text-mute opacity-50 cursor-not-allowed',
          ].join(' ')}
        >
          {resolving ? 'RESOLVING…' : 'RESOLVE TURN'}
        </button>
      )}
    </div>
  )
}


function RunAITurnButton({
  onRun, thinking, resolving,
}: {
  onRun: () => Promise<void>
  thinking: boolean
  resolving: boolean
}) {
  const busy = thinking || resolving
  const label = thinking ? 'AI THINKING…' : resolving ? 'RESOLVING…' : 'RUN AI TURN'
  return (
    <button
      onClick={() => !busy && onRun()}
      disabled={busy}
      title="Both sides are AI — fire both controllers and resolve."
      className={[
        'h-8 px-4 rounded-sm border tracking-widest text-[11px]',
        'transition-colors',
        busy
          ? 'border-amber/60 text-amber/70 bg-amber/5'
          : 'bg-amber/10 border-amber text-amber animate-pulse hover:bg-amber/20',
      ].join(' ')}
    >
      {label}
    </button>
  )
}


function EndGameButton() {
  const reflecting = useStore((s) => s.reflecting)
  const endGameAndReflect = useStore((s) => s.endGameAndReflect)
  return (
    <button
      onClick={() => {
        if (reflecting) return
        if (!confirm('Force-end this game and run reflection? Lessons will be added to memory.')) return
        endGameAndReflect({ force: true })
      }}
      disabled={reflecting}
      title="Force-end the game and extract lessons (manual forfeit)"
      className={[
        'h-8 px-3 rounded-sm border text-[10px] font-mono tracking-widest mr-2',
        'transition-colors',
        reflecting
          ? 'border-line text-mute opacity-50'
          : 'border-line text-mute hover:text-fg hover:border-fg/40',
      ].join(' ')}
    >
      {reflecting ? 'REFLECTING…' : 'END & LEARN'}
    </button>
  )
}


function AISideStatus({
  side, locked, thinking, score,
}: {
  side: 'blue' | 'red'
  locked: boolean
  thinking: boolean
  score: number
}) {
  const c = side === 'blue' ? 'text-blue' : 'text-red'
  const dot = side === 'blue' ? 'bg-blue' : 'bg-red'
  const status = thinking ? 'THINKING…' : locked ? 'LOCKED' : 'IDLE'
  const statusCls = thinking ? 'text-amber animate-pulse'
                  : locked ? 'text-amber'
                  : 'text-mute'
  const shownScore = useTweenedNumber(score)
  return (
    <div className="flex items-center gap-2">
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      <span className={`${c} font-semibold tracking-widest`}>{side.toUpperCase()}</span>
      <span className="text-fg tabular-nums w-10 text-right">{shownScore.toFixed(1)}</span>
      <span className={`text-[10px] tracking-widest font-mono ${statusCls}`}>
        {status}
      </span>
    </div>
  )
}


function SideControl({
  side, score, ordered, total, locked, onLock, disabled,
}: {
  side: 'blue' | 'red'
  score: number
  ordered: number
  total: number
  locked: boolean
  onLock: () => void
  disabled: boolean
}) {
  const c = side === 'blue' ? 'text-blue' : 'text-red'
  const dim = side === 'blue' ? 'border-blue/40' : 'border-red/40'
  const dot = side === 'blue' ? 'bg-blue' : 'bg-red'
  const shownScore = useTweenedNumber(score)
  return (
    <div className="flex items-center gap-2">
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      <span className={`${c} font-semibold tracking-widest`}>{side.toUpperCase()}</span>
      <span className="text-fg tabular-nums w-10 text-right">{shownScore.toFixed(1)}</span>
      <span className="text-mute text-[10px]">{ordered}/{total}</span>
      <button
        onClick={onLock}
        disabled={disabled}
        className={[
          'h-7 px-2 rounded-sm border text-[10px] tracking-widest',
          locked
            ? `${c} ${dim} bg-panel2 cursor-default`
            : disabled
            ? 'border-line text-mute opacity-50 cursor-not-allowed'
            : `${c} ${dim} hover:bg-panel2`,
        ].join(' ')}
      >
        {locked ? 'LOCKED' : 'LOCK'}
      </button>
    </div>
  )
}
