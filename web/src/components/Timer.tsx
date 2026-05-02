import { useEffect } from 'react'
import { useStore } from '../store'


/** Drives the match timer via a 4 Hz tick. Mounted once at App level. */
export function useMatchTimer() {
  const tickMatch = useStore((s) => s.tickMatch)
  useEffect(() => {
    let last = performance.now()
    const id = setInterval(() => {
      const now = performance.now()
      const dt = (now - last) / 1000
      last = now
      tickMatch(dt)
    }, 250)
    return () => clearInterval(id)
  }, [tickMatch])
}


export function TimerPill() {
  const elapsed = useStore((s) => s.matchElapsed)
  const total = useStore((s) => s.matchSeconds)
  const started = useStore((s) => s.matchStarted)
  const remaining = Math.max(0, total - elapsed)
  const mm = Math.floor(remaining / 60)
  const ss = Math.floor(remaining % 60)
  const text = `${mm}:${ss.toString().padStart(2, '0')}`

  let color = 'text-fg'
  let pulse = ''
  if (started && remaining <= 15) {
    color = 'text-red'
    pulse = 'animate-pulse'
  } else if (started && remaining <= 60) {
    color = 'text-amber'
  } else if (!started) {
    color = 'text-mute'
  }

  return (
    <div className={`flex items-center gap-2 ${pulse}`}>
      <span className="text-mute tracking-widest text-[10px]">CLOCK</span>
      <span className={`text-base font-semibold tabular-nums font-mono ${color}`}>
        {text}
      </span>
    </div>
  )
}
