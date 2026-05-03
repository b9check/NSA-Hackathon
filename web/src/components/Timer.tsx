import { useStore } from '../store'

/** Sum current HP for one side across units + bases. */
function sideHp(game: ReturnType<typeof useStore.getState>['game'], side: 'blue' | 'red') {
  if (!game) return { cur: 0, max: 0 }
  let cur = 0, max = 0
  for (const u of game.units) if (u.side === side) { cur += u.hp; max += u.max_hp }
  for (const b of game.bases) if (b.side === side) { cur += b.hp; max += b.max_hp }
  const startMax = game.starting_hp?.[side] ?? max
  return { cur, max: Math.max(max, startMax) }
}

/** Compact two-line HP readout for the TurnBar — replaces the wallclock. */
export function HpStatus() {
  const game = useStore((s) => s.game)
  const victory = game?.victory
  if (!game) return null
  const blue = sideHp(game, 'blue')
  const red = sideHp(game, 'red')
  const blueStart = game.starting_hp?.blue ?? blue.max
  const redStart = game.starting_hp?.red ?? red.max
  const bluePct = blueStart ? blue.cur / blueStart : 0
  const redPct = redStart ? red.cur / redStart : 0
  const threshold = victory?.hp_loss_threshold ?? 0.25

  return (
    <div className="flex items-center gap-3">
      <span className="text-mute tracking-widest text-[10px]">HP</span>
      <HpBar side="blue" pct={bluePct} cur={blue.cur} max={blueStart} threshold={threshold} />
      <HpBar side="red" pct={redPct} cur={red.cur} max={redStart} threshold={threshold} />
    </div>
  )
}

function HpBar({ side, pct, cur, max, threshold }: {
  side: 'blue' | 'red'
  pct: number
  cur: number
  max: number
  threshold: number
}) {
  const c = side === 'blue' ? 'bg-blue' : 'bg-red'
  const txt = side === 'blue' ? 'text-blue' : 'text-red'
  const danger = pct <= threshold
  return (
    <div className="flex items-center gap-1.5">
      <span className={`text-[10px] font-semibold ${txt} tracking-widest`}>{side[0].toUpperCase()}</span>
      <div className="relative w-16 h-2 bg-panel2 border border-line rounded-sm overflow-hidden">
        <div
          className={`absolute inset-y-0 left-0 ${c} transition-all`}
          style={{ width: `${Math.max(0, Math.min(100, pct * 100))}%` }}
        />
        <div
          className="absolute inset-y-0 border-l border-amber/60"
          style={{ left: `${threshold * 100}%` }}
        />
      </div>
      <span className={`text-[10px] tabular-nums font-mono ${danger ? 'text-amber animate-pulse' : 'text-mute'}`}>
        {cur}/{max}
      </span>
    </div>
  )
}
