// Scrolling battle narrative pinned to the bottom of the screen.
//
// Format examples:
//    [T2] BLUE  F-35  →  RED Shahed-1   HIT  Pk 0.75  −2 HP
//    [T2] BLUE  M1A2  move (5,12) → (5,13)
//    [T2] RED   ★ Shahed-2 destroyed
//    [T2] BLUE  ✓ control objective (9,7)
//    [T2] BLUE  ◎ scout sees: red-recon-1
import { useEffect, useRef } from 'react'
import { useStore, type AnnotatedEvent } from '../store'

export function BattleLog() {
  const log = useStore((s) => s.eventLog)
  const game = useStore((s) => s.game)
  const ref = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new entries.
  useEffect(() => {
    if (!ref.current) return
    ref.current.scrollTop = ref.current.scrollHeight
  }, [log.length])

  const idLookup = (id: string): string => {
    if (!game) return id
    const u = game.units.find((u) => u.id === id)
    if (u) return u.display
    const b = (game.bases ?? []).find((b) => b.id === id)
    if (b) return b.display
    return id
  }

  return (
    <div
      ref={ref}
      className="h-24 bg-panel border-t border-line overflow-y-auto px-4 py-2 font-mono text-[11px] leading-tight"
    >
      {log.length === 0 ? (
        <div className="text-mute opacity-60">awaiting first contact…</div>
      ) : (
        <div className="space-y-0.5">
          {log.map((e) => (
            <LogLine key={`${e._turn}-${e._seq}`} ev={e} idLookup={idLookup} />
          ))}
        </div>
      )}
    </div>
  )
}


function LogLine({
  ev, idLookup,
}: {
  ev: AnnotatedEvent
  idLookup: (id: string) => string
}) {
  const { type } = ev
  const turnTag = (
    <span className="text-mute opacity-60 inline-block w-9">[T{ev._turn + 1}]</span>
  )
  if (type === 'move') {
    const u = ev.unit as string
    const from = ev.path[0] as [number, number]
    const to = ev.path[ev.path.length - 1] as [number, number]
    return (
      <div className="text-mute">
        {turnTag}
        <Side side={sideOf(u)} />
        <span className="text-fg ml-1">{idLookup(u)}</span>
        <span className="ml-2">move ({from[0]},{from[1]}) → ({to[0]},{to[1]})</span>
        {ev.stopped_short && <span className="ml-2 text-amber">(blocked)</span>}
      </div>
    )
  }
  if (type === 'strike') {
    const a = ev.attacker as string
    const hex = ev.target_hex as [number, number]
    const hits = ev.targets_hit as string[]
    const whiff = ev.whiffed
    return (
      <div className={whiff ? 'text-mute' : 'text-fg'}>
        {turnTag}
        <Side side={sideOf(a)} />
        <span className="text-fg ml-1">{idLookup(a)}</span>
        <span className="text-mute mx-1">→</span>
        <span>strike ({hex[0]},{hex[1]})</span>
        {whiff ? (
          <span className="ml-2 text-mute">WHIFF (target moved)</span>
        ) : (
          <span className="ml-2 text-amber font-semibold">
            -{ev.damage} HP × {hits.length} target{hits.length === 1 ? '' : 's'}
          </span>
        )}
        {ev.self_destruct && <span className="ml-2 text-red">[SD]</span>}
        {ev.counter_damage > 0 && (
          <span className="ml-2 text-mute">cnt -{ev.counter_damage}</span>
        )}
      </div>
    )
  }
  if (type === 'overwatch_fire') {
    const a = ev.attacker as string
    const t = ev.target as string
    return (
      <div className="text-fg">
        {turnTag}
        <Side side={sideOf(a)} />
        <span className="text-fg ml-1">{idLookup(a)}</span>
        <span className="text-mute mx-1">→</span>
        <Side side={sideOf(t)} />
        <span className="ml-1">{idLookup(t)}</span>
        <span className="ml-2 font-semibold text-amber">-{ev.damage} HP</span>
        <span className="text-mute ml-2">[OW]</span>
      </div>
    )
  }
  if (type === 'destroyed') {
    return (
      <div className="text-red">
        {turnTag}
        <Side side={ev.side} />
        <span className="ml-1">★ {idLookup(ev.entity_id)} destroyed</span>
      </div>
    )
  }
  if (type === 'scout_reveal') {
    return (
      <div className="text-green">
        {turnTag}
        <Side side={ev.side} />
        <span className="ml-1">◎ scout {ev.revealed_units.length} contact(s)</span>
      </div>
    )
  }
  if (type === 'turn_end') {
    return (
      <div className="text-mute opacity-60 border-t border-line/40 pt-0.5 mt-0.5">
        {turnTag}— end turn —  blue {ev.blue_score.toFixed(1)} · red {ev.red_score.toFixed(1)}
      </div>
    )
  }
  return null
}

function Side({ side }: { side?: string }) {
  if (side === 'blue') return <span className="text-blue">BLUE </span>
  if (side === 'red') return <span className="text-red">RED  </span>
  return <span className="text-mute">—   </span>
}

function sideOf(id: string): 'blue' | 'red' | undefined {
  if (id.startsWith('blue-')) return 'blue'
  if (id.startsWith('red-')) return 'red'
  return undefined
}
