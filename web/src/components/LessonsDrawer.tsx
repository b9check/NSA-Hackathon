// Pops up after a game ends + reflection runs, showing the lessons the
// AI just extracted. These have already been appended to memory/lessons.jsonl
// server-side; the next AI turn will retrieve and inject them into its prompt.
import { useStore } from '../store'

export function LessonsDrawer() {
  const show = useStore((s) => s.showLessonsDrawer)
  const lessons = useStore((s) => s.lastLessons)
  const dismiss = useStore((s) => s.dismissLessonsDrawer)
  if (!show) return null

  return (
    <div className="absolute inset-0 z-40 bg-bg/80 backdrop-blur-sm flex items-center justify-center p-6">
      <div className="bg-panel border border-line rounded-sm w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <div className="px-5 py-3 border-b border-line flex items-center gap-3">
          <span className="text-[10px] tracking-[0.2em] text-amber">REFLECTION COMPLETE</span>
          <span className="text-mute text-[11px] font-mono">
            {lessons.length} lesson{lessons.length === 1 ? '' : 's'} added to memory
          </span>
          <button
            onClick={dismiss}
            className="ml-auto h-7 px-3 text-[10px] font-mono tracking-widest border border-line text-mute hover:text-fg hover:bg-panel2 rounded-sm"
          >
            CLOSE
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {lessons.length === 0 ? (
            <div className="text-mute text-[12px] italic">
              No lessons extracted (LLM returned empty or errored).
            </div>
          ) : (
            lessons.map((l, i) => (
              <div
                key={l.id}
                className="border border-line/70 bg-panel2/40 rounded-sm px-4 py-3"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] tracking-widest text-amber">
                    LESSON {i + 1}
                  </span>
                  <span className="text-[10px] text-mute opacity-60">{l.id}</span>
                  {l.tags?.phase && (
                    <span className="text-[10px] text-mute">phase: {l.tags.phase}</span>
                  )}
                  {l.outcome && (
                    <span className="text-[10px] text-mute">{l.outcome}</span>
                  )}
                </div>
                <div className="text-[12px] text-fg leading-snug">
                  "{l.claim}"
                </div>
              </div>
            ))
          )}
        </div>

        <div className="px-5 py-3 border-t border-line text-[10px] font-mono text-mute leading-relaxed">
          These lessons are now stored in <span className="text-amber">memory/lessons.jsonl</span>.
          The next AI turn will retrieve the most recent five and include them
          in its prompt — that's how learning carries forward between games.
        </div>
      </div>
    </div>
  )
}
