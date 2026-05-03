// Operational bottom bar: PLAY / PAUSE / SPEED.
// Continuous-time mode — the player plans missions, hits play, watches the
// sim run at adjustable speed, intervenes on auto-pause (first contact,
// friendly damage, mission complete) or manual pause.
import { useStore } from '../store'

const SPEED_OPTIONS = [1, 5, 15, 30, 60]

export function TurnBar() {
  const game = useStore((s) => s.game)
  const playing = useStore((s) => s.playing)
  const simSpeed = useStore((s) => s.simSpeed)
  const lastPauseReason = useStore((s) => s.lastPauseReason)
  const replaying = useStore((s) => s.replaying)
  const pendingEvents = useStore((s) => s.pendingEvents)
  const play = useStore((s) => s.play)
  const pause = useStore((s) => s.pause)
  const setSpeed = useStore((s) => s.setSpeed)

  if (!game) return null

  const missionsCount = game.missions ? Object.keys(game.missions).length : 0
  const busy = replaying || pendingEvents != null
  const canPlay = !playing && missionsCount > 0 && !game.winner
  const playLabel = playing ? 'PAUSE' : (busy ? 'WORKING…' : 'PLAY')

  const onPlayPause = () => {
    if (playing) pause('manual')
    else if (canPlay) void play()
  }

  return (
    <div className="h-12 bg-panel border-t border-line flex items-center justify-end px-4 gap-3 font-mono text-[11px]">
      {lastPauseReason && (
        <span className="text-[10px] font-mono text-amber max-w-[420px] truncate" title={lastPauseReason}>
          ⏸ {lastPauseReason}
        </span>
      )}
      {missionsCount > 0 && (
        <span className="text-[10px] font-mono text-mute">
          {missionsCount} mission{missionsCount === 1 ? '' : 's'}
        </span>
      )}

      {/* Speed selector */}
      <div className="flex items-center gap-1 border border-line rounded-sm overflow-hidden">
        {SPEED_OPTIONS.map((s) => (
          <button
            key={s}
            onClick={() => setSpeed(s)}
            className={[
              'px-2.5 h-8 text-[10px] font-mono tracking-wider transition-colors',
              s === simSpeed
                ? 'bg-amber/20 text-amber'
                : 'text-mute hover:text-fg hover:bg-line/30',
            ].join(' ')}
            title={`${s}× speed`}
          >
            {s}×
          </button>
        ))}
      </div>

      {/* Play / Pause primary */}
      <button
        onClick={onPlayPause}
        disabled={!playing && !canPlay}
        title={
          playing ? 'Pause the sim' :
          missionsCount === 0 ? 'Set a mission to enable PLAY' :
          game.winner ? 'Game over' : 'Run the sim until auto-pause'
        }
        className={[
          'h-8 px-5 rounded-sm border tracking-widest text-[11px]',
          'transition-colors min-w-[88px]',
          playing
            ? 'bg-amber/15 border-amber text-amber hover:bg-amber/25'
            : canPlay
              ? 'bg-blue/10 border-blue text-blue hover:bg-blue/20'
              : 'border-line text-mute opacity-50 cursor-not-allowed',
        ].join(' ')}
      >
        {playing ? '⏸ PAUSE' : busy ? '… BUSY' : `▶ ${playLabel}`}
      </button>
    </div>
  )
}
