import { useStore } from '../store'

export function ObjectivesPanel() {
  const game = useStore((s) => s.game)
  if (!game || !game.phases || game.phases.length === 0) return null

  const current = game.current_phase ?? 0

  return (
    <div
      className="absolute z-20 bg-panel/85 backdrop-blur-sm border border-line rounded-sm font-mono text-[11px] text-fg shadow-lg"
      style={{ top: 64, left: 8, maxWidth: 300, width: 280 }}
    >
      <div className="px-3 py-2 border-b border-line text-[10px] tracking-[0.3em] text-mute">
        OBJECTIVES
      </div>
      <div className="px-3 py-2 space-y-3 max-h-[70vh] overflow-y-auto">
        {game.phases.map((phase, idx) => {
          const locked = idx > current
          const active = idx === current
          return (
            <div key={idx} className={locked ? 'opacity-40' : ''}>
              <div
                className={[
                  'text-[10px] tracking-[0.2em] mb-1',
                  active ? 'text-amber' : 'text-fg/70',
                ].join(' ')}
              >
                {phase.name.toUpperCase()}
                {locked && <span className="text-mute"> (LOCKED)</span>}
                {phase.completed && <span className="text-blue"> ✓</span>}
              </div>
              {!locked && phase.objectives.length > 0 && (
                <ul className="space-y-0.5 pl-1">
                  {phase.objectives.map((o, oi) => (
                    <li
                      key={oi}
                      className={[
                        'flex items-start gap-2',
                        o.completed ? 'text-blue' : 'text-fg/80',
                      ].join(' ')}
                    >
                      <span className="select-none mt-0.5">
                        {o.completed ? '✓' : '☐'}
                      </span>
                      <span className={o.completed ? 'line-through opacity-70' : ''}>
                        {o.label}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
