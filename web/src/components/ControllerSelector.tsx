// Per-side controller pills: MANUAL / RANDOM / HEURISTIC / LLM.
// State mirrored in store.controllers; setController() POSTs to the server.
// When a side is non-manual, that side's lock flow is automated by the
// server when the other (human) side locks.
import { useStore } from '../store'
import type { ControllerKind } from '../store'

const KINDS: ControllerKind[] = ['manual', 'random', 'llm']

const COLOR: Record<'blue' | 'red', { text: string; border: string; dot: string }> = {
  blue: { text: 'text-blue', border: 'border-blue/60', dot: 'bg-blue' },
  red:  { text: 'text-red',  border: 'border-red/60',  dot: 'bg-red'  },
}

export function ControllerSelector({ side }: { side: 'blue' | 'red' }) {
  const kind = useStore((s) => s.controllers[side])
  const setController = useStore((s) => s.setController)
  const thinking = useStore((s) => s.aiThinking[side])
  const c = COLOR[side]

  const next = () => {
    const i = KINDS.indexOf(kind)
    setController(side, KINDS[(i + 1) % KINDS.length])
  }

  // Single-letter prefix keeps the pill narrow (~78px) — important since
  // it lives next to the faction pills, RealGameToggle, view-mode toggle,
  // and region picker in a dense top bar.
  const label = thinking ? '…' : kind === 'manual' ? 'man' : kind
  return (
    <button
      onClick={next}
      disabled={thinking}
      title={`${side.toUpperCase()} controller: ${kind} — click to cycle (manual → random → llm)`}
      className={[
        'h-8 px-2 inline-flex items-center gap-1.5 rounded-sm border bg-panel2/40',
        'font-mono text-[10px] tracking-widest transition-colors whitespace-nowrap',
        c.border,
      ].join(' ')}
    >
      <span className={`${c.text} font-semibold`}>{side[0].toUpperCase()}</span>
      <span className={kind === 'manual' ? 'text-mute' : 'text-amber'}>
        {label.toUpperCase()}
      </span>
    </button>
  )
}
