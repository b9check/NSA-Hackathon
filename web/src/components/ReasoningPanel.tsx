// Show the AI's last-completed-turn reasoning. Renders after the call
// returns (no streaming yet). One panel per side; collapses if no reasoning
// has been generated. Lives at the bottom of the right rail.
import { useStore } from '../store'

export function ReasoningPanel() {
  const blue = useStore((s) => s.aiReasoning.blue)
  const red  = useStore((s) => s.aiReasoning.red)
  const blueThinking = useStore((s) => s.aiThinking.blue)
  const redThinking  = useStore((s) => s.aiThinking.red)
  const blueCtrl = useStore((s) => s.controllers.blue)
  const redCtrl  = useStore((s) => s.controllers.red)

  const blueActive = blueCtrl !== 'manual' || blue !== null
  const redActive  = redCtrl !== 'manual' || red !== null
  if (!blueActive && !redActive) return null

  return (
    <div className="border-t border-line bg-panel">
      <div className="px-3 py-2 text-[10px] tracking-[0.18em] text-mute border-b border-line">
        AI REASONING
      </div>
      <div className="max-h-[14rem] overflow-y-auto">
        {blueActive && (
          <Side side="blue" data={blue} thinking={blueThinking} ctrl={blueCtrl} />
        )}
        {redActive && (
          <Side side="red" data={red} thinking={redThinking} ctrl={redCtrl} />
        )}
      </div>
    </div>
  )
}


function Side({
  side, data, thinking, ctrl,
}: {
  side: 'blue' | 'red'
  data: import('../store').AIReasoning | null
  thinking: boolean
  ctrl: import('../store').ControllerKind
}) {
  const c = side === 'blue' ? 'text-blue' : 'text-red'
  return (
    <div className="px-3 py-2 border-b border-line/60 text-[11px] font-mono">
      <div className="flex items-center gap-2 mb-1">
        <span className={`font-semibold ${c}`}>{side.toUpperCase()}</span>
        <span className="text-mute">{ctrl.toUpperCase()}</span>
        {thinking && <span className="text-amber animate-pulse">thinking…</span>}
        {data?.fallback && <span className="text-amber">[fallback]</span>}
        {data?.turn !== null && data?.turn !== undefined && (
          <span className="text-mute opacity-60 ml-auto">T{data.turn + 1}</span>
        )}
      </div>
      {!data && !thinking && (
        <div className="text-mute opacity-60">no turns yet</div>
      )}
      {data && (
        <>
          {data.summary && (
            <div className="text-fg leading-snug mb-1.5">{data.summary}</div>
          )}
          {data.decisions.length > 0 && (
            <ul className="space-y-0.5">
              {data.decisions.map((d, i) => (
                <li key={i} className="text-mute leading-tight">
                  <span className="text-fg">{d.unit_id}</span>
                  <span className="mx-1.5 opacity-50">·</span>
                  <span>{d.kind ?? d.action_id ?? '?'}</span>
                  {d.target_hex && (
                    <span className="ml-1.5 opacity-70">
                      ({d.target_hex[0]},{d.target_hex[1]})
                    </span>
                  )}
                  {d.intent && (
                    <span className="ml-2 italic opacity-80">{d.intent}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
