import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

export function RegionPicker() {
  const regions = useStore((s) => s.regions)
  const swapping = useStore((s) => s.swapping)
  const swapError = useStore((s) => s.swapError)
  const game = useStore((s) => s.game)
  const swapRegion = useStore((s) => s.swapRegion)
  const reroll = useStore((s) => s.reroll)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current) return
      if (!ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  if (regions.length === 0) {
    return (
      <span className="text-[11px] font-mono text-mute opacity-60 px-2">
        server down
      </span>
    )
  }

  const currentName = game?.name ?? '—'
  const seed = game?.seed

  return (
    <div ref={ref} className="relative flex items-center gap-1">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={swapping}
        className={[
          'h-8 px-3 flex items-center gap-2 border border-line rounded-sm',
          'text-[11px] font-mono uppercase tracking-wider',
          swapping ? 'text-mute' : 'text-fg hover:bg-panel2',
          'transition-colors',
        ].join(' ')}
        title="Swap satellite region"
      >
        <span className="text-mute">REGION</span>
        <span className="text-amber">
          {swapping ? 'fetching tiles…' : currentName}
        </span>
        {seed !== undefined && !swapping && (
          <span className="text-mute opacity-60 ml-1 text-[10px]">
            seed {seed}
          </span>
        )}
        <span className="text-mute opacity-60 ml-1">▾</span>
      </button>
      <button
        onClick={() => reroll()}
        disabled={swapping}
        className={[
          'h-8 w-8 flex items-center justify-center border border-line rounded-sm',
          'text-[14px] leading-none',
          swapping ? 'text-mute' : 'text-fg hover:bg-panel2 hover:text-amber',
          'transition-colors',
        ].join(' ')}
        title="Reroll unit placements (same terrain, new seed)"
        aria-label="Reroll units"
      >
        ⟲
      </button>
      {open && !swapping && (
        <div
          className="absolute right-0 top-full mt-1 w-72 z-50 bg-panel border border-line rounded-sm
                     shadow-2xl overflow-hidden"
        >
          <div className="px-3 py-2 text-[10px] tracking-[0.18em] font-mono text-mute border-b border-line">
            SATELLITE REGION
          </div>
          <ul className="max-h-80 overflow-auto">
            {regions.map((r) => {
              const active = game?.name === r.name
              return (
                <li key={r.key}>
                  <button
                    onClick={() => {
                      setOpen(false)
                      swapRegion(r.key)
                    }}
                    className={[
                      'w-full text-left px-3 py-2 flex items-center gap-3',
                      'border-l-2 transition-colors',
                      active
                        ? 'border-amber bg-panel2 text-fg'
                        : 'border-transparent text-mute hover:text-fg hover:bg-panel2/60',
                    ].join(' ')}
                  >
                    <span className="font-mono text-[10px] uppercase opacity-70 w-16">
                      {r.key}
                    </span>
                    <span className="flex-1 text-xs">{r.name}</span>
                    <span className="text-[10px] font-mono opacity-50">
                      z{r.zoom}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="px-3 py-2 border-t border-line text-[10px] font-mono text-mute leading-relaxed">
            Imagery © Esri / Maxar. Each swap re-fetches tiles, re-derives terrain, and re-places units.
          </div>
        </div>
      )}
      {swapError && (
        <div className="absolute right-0 top-full mt-1 w-80 z-50 bg-panel border border-red rounded-sm px-3 py-2 text-[11px] font-mono text-red">
          {swapError}
        </div>
      )}
    </div>
  )
}
