import { useStore } from '../store'


export function EndGameOverlay() {
  const info = useStore((s) => s.gameOver)
  const game = useStore((s) => s.game)
  const reroll = useStore((s) => s.reroll)
  const resetMatch = useStore((s) => s.resetMatch)
  const refetchState = useStore((s) => s.refetchState)
  const dismissGameOver = useStore((s) => s.dismissGameOver)
  if (!info) return null

  const winnerColor =
    info.winner === 'blue' ? 'text-blue' :
    info.winner === 'red'  ? 'text-red'  :
    'text-amber'
  const tagline =
    info.winner === 'draw' ? 'STALEMATE'
    : info.reason === 'annihilation' ? 'TOTAL VICTORY'
    : info.reason === 'hp_collapse' ? 'FORCE BROKEN'
    : 'TURN CAP REACHED'

  const onReplay = async () => {
    await reroll()
    resetMatch()
    await refetchState()
  }
  const onDismiss = () => {
    // Clear the overlay AND mark the game-over dismissed so useWinCheck
    // doesn't immediately re-pop it (game.winner is still set on state).
    dismissGameOver()
  }

  return (
    <div className="absolute inset-0 z-50 bg-bg/85 backdrop-blur-sm flex items-center justify-center">
      <div className="bg-panel border border-line rounded-sm px-10 py-8 max-w-[520px] w-[90%]">
        <div className="text-[10px] font-mono tracking-[0.2em] text-mute mb-2">
          {tagline}
        </div>
        <div className={`text-4xl font-bold tracking-wider ${winnerColor} mb-1`}>
          {info.winner === 'draw' ? 'DRAW' : `${info.winner.toUpperCase()} WINS`}
        </div>
        {game && (
          <div className="text-[12px] font-mono text-mute mb-6">
            {game.name} · turn {info.turn}
          </div>
        )}
        <div className="grid grid-cols-2 gap-6 mb-6">
          <ScoreRow side="blue" pct={info.blue_hp_pct} winner={info.winner} />
          <ScoreRow side="red"  pct={info.red_hp_pct}  winner={info.winner} />
        </div>
        <div className="flex gap-3">
          <button
            onClick={onReplay}
            className="flex-1 h-10 border border-amber text-amber bg-amber/10 hover:bg-amber/20 rounded-sm font-mono text-sm tracking-widest transition-colors"
          >
            REPLAY
          </button>
          <button
            onClick={onDismiss}
            className="flex-1 h-10 border border-line text-mute hover:text-fg hover:bg-panel2 rounded-sm font-mono text-sm tracking-widest transition-colors"
          >
            VIEW MAP
          </button>
        </div>
      </div>
    </div>
  )
}


function ScoreRow({ side, pct, winner }: {
  side: 'blue' | 'red'
  pct: number
  winner: 'blue' | 'red' | 'draw'
}) {
  const c = side === 'blue' ? 'text-blue' : 'text-red'
  const won = winner === side
  return (
    <div>
      <div className={`text-[10px] font-mono tracking-widest ${c} mb-1`}>
        {side.toUpperCase()} HP
        {won && <span className="ml-2 text-amber">★</span>}
      </div>
      <div className="text-2xl font-mono tabular-nums text-fg">
        {(pct * 100).toFixed(0)}%
      </div>
    </div>
  )
}
