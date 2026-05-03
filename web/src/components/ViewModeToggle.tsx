import { useStore } from '../store'
import type { ViewMode } from '../types'

const MODES: Array<{ key: ViewMode; label: string; sub: string; cls: string }> = [
  { key: 'omniscient', label: 'OMNI', sub: 'omniscient',     cls: 'text-amber border-amber/60' },
  { key: 'blue',       label: 'BLUE', sub: 'blue commander', cls: 'text-blue border-blue/60'   },
  { key: 'red',        label: 'RED',  sub: 'red commander',  cls: 'text-red border-red/60'     },
]

export function ViewModeToggle() {
  const viewMode = useStore((s) => s.viewMode)
  const setViewMode = useStore((s) => s.setViewMode)
  return (
    <div className="flex items-center gap-1 border border-line rounded-sm overflow-hidden">
      {MODES.map((m) => {
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
