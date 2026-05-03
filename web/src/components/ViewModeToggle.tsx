import { useStore } from '../store'
import type { ViewMode } from '../types'

const ALL: Array<{ key: ViewMode; label: string; sub: string; cls: string }> = [
  { key: 'omniscient', label: 'OMNI', sub: 'omniscient',     cls: 'text-amber border-amber/60' },
  { key: 'blue',       label: 'BLUE', sub: 'blue commander', cls: 'text-blue border-blue/60'   },
  { key: 'red',        label: 'RED',  sub: 'red commander',  cls: 'text-red border-red/60'     },
]

export function ViewModeToggle() {
  const viewMode = useStore((s) => s.viewMode)
  const setViewMode = useStore((s) => s.setViewMode)
  const realGame = useStore((s) => s.realGame)
  // In real-game mode the OMNI option is hidden — two humans hot-seat
  // and only see their own side.
  const modes = realGame ? ALL.filter((m) => m.key !== 'omniscient') : ALL
  return (
    <div className="flex items-center gap-1 border border-line rounded-sm overflow-hidden">
      {modes.map((m) => {
        const active = viewMode === m.key
        return (
          <button
            key={m.key}
            onClick={() => setViewMode(m.key)}
            title={`view: ${m.sub}`}
            className={[
              'h-8 px-3 text-[11px] font-mono tracking-wider transition-colors',
              active
                ? `bg-panel2 ${m.cls.split(' ')[0]} font-semibold`
                : 'text-mute hover:text-fg hover:bg-panel2/60',
            ].join(' ')}
          >
            {m.label}
          </button>
        )
      })}
    </div>
  )
}


export function RealGameToggle() {
  const realGame = useStore((s) => s.realGame)
  const toggleRealGame = useStore((s) => s.toggleRealGame)
  return (
    <button
      onClick={toggleRealGame}
      title={
        realGame
          ? 'REAL-GAME mode ON — OMNI hidden, hot-seat 2 players. Click to disable.'
          : 'Enable REAL-GAME mode (no OMNI; play with a friend).'
      }
      className={[
        'h-8 px-3 rounded-sm border text-[11px] font-mono tracking-widest transition-colors',
        realGame
          ? 'border-amber text-amber bg-amber/10 hover:bg-amber/20'
          : 'border-line text-mute hover:text-fg hover:bg-panel2',
      ].join(' ')}
    >
      {realGame ? '● REAL GAME' : '○ REAL GAME'}
    </button>
  )
}
