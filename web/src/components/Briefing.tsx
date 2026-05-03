import { useState } from 'react'
import { useStore } from '../store'

export function Briefing() {
  const game = useStore((s) => s.game)
  const [dismissed, setDismissed] = useState(false)

  if (!game || !game.briefing || dismissed) return null
  const b = game.briefing
  if (!b.title && !b.situation && !b.mission) return null

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-bg/80 backdrop-blur-sm">
      <div className="max-w-2xl w-[90%] max-h-[85vh] overflow-y-auto bg-panel border border-line rounded-sm shadow-2xl">
        <div className="px-6 py-4 border-b border-line">
          <div className="text-[10px] font-mono tracking-[0.3em] text-mute">OPLAN BRIEFING</div>
          <div className="text-amber font-mono text-lg tracking-wider mt-1">
            {b.title}
          </div>
        </div>
        <div className="px-6 py-5 space-y-5 font-mono text-[12px] text-fg leading-relaxed">
          {b.situation && (
            <section>
              <div className="text-amber text-[11px] tracking-[0.25em] mb-2">SITUATION</div>
              <div className="whitespace-pre-line text-fg/90">{b.situation}</div>
            </section>
          )}
          {b.mission && (
            <section>
              <div className="text-amber text-[11px] tracking-[0.25em] mb-2">MISSION</div>
              <div className="whitespace-pre-line text-fg/90">{b.mission}</div>
            </section>
          )}
          {b.rules_of_engagement && (
            <section>
              <div className="text-amber text-[11px] tracking-[0.25em] mb-2">RULES OF ENGAGEMENT</div>
              <div className="whitespace-pre-line text-fg/90">{b.rules_of_engagement}</div>
            </section>
          )}
        </div>
        <div className="px-6 py-4 border-t border-line flex justify-center">
          <button
            onClick={() => setDismissed(true)}
            className="px-6 py-2 border border-amber text-amber font-mono text-[11px] tracking-[0.3em] hover:bg-amber hover:text-bg transition-colors"
          >
            ACKNOWLEDGE
          </button>
        </div>
      </div>
    </div>
  )
}
