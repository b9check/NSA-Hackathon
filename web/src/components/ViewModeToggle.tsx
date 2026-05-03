import { useStore } from '../store'
import type { ViewMode } from '../types'

const ALL: Array<{ key: ViewMode; label: string; sub: string; activeText: string; activeBorder: string }> = [
  { key: 'omniscient', label: 'OMNI', sub: 'omniscient',     activeText: 'text-amber', activeBorder: 'border-amber' },
  { key: 'blue',       label: 'BLUE', sub: 'blue commander', activeText: 'text-blue',  activeBorder: 'border-blue'  },
  { key: 'red',        label: 'RED',  sub: 'red commander',  activeText: 'text-red',   activeBorder: 'border-red'   },
]

export function ViewModeToggle() {
  const viewMode = useStore((s) => s.viewMode)
  const setViewMode = useStore((s) => s.setViewMode)
  const realGame = useStore((s) => s.realGame)
  // In real-game mode the OMNI option is hidden — two humans hot-seat
  // and only see their own side.
  const modes = realGame ? ALL.filter((m) => m.key !== 'omniscient') : ALL
  return (
    <div className="flex items-center gap-1">
      {modes.map((m) => {
        const active = viewMode === m.key
        return (
          <button
            key={m.key}
            onClick={() => setViewMode(m.key)}
            title={`view: ${m.sub}`}
            className={[
              'h-8 min-w-[3.25rem] px-3 rounded-sm border text-[11px] font-mono font-semibold',
              'transition-colors',
              active
                ? `${m.activeBorder} ${m.activeText} bg-panel2`
                : 'border-line text-mute hover:text-fg hover:bg-panel2/60',
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
        'h-8 px-3 inline-flex items-center gap-1.5 rounded-sm border',
        'text-[11px] font-mono font-semibold transition-colors whitespace-nowrap',
        realGame
          ? 'border-amber text-amber bg-amber/10 hover:bg-amber/20'
          : 'border-line text-mute hover:text-fg hover:bg-panel2',
      ].join(' ')}
    >
      <span
        className={[
          'inline-block w-2 h-2 rounded-full transition-colors',
          realGame ? 'bg-amber' : 'bg-mute/50',
        ].join(' ')}
      />
      <span>HOT-SEAT</span>
    </button>
  )
}
