// Promise-based tween that drives off pixi.js's app.ticker.
//
// Usage:
//   await tween(sprite, { x: 100, y: 50 }, 600, easeInOutQuad)
//   await Promise.all([anim1, anim2])         // sync gate
//
// The tween writes directly to mutable target props (Pixi Containers,
// Graphics, etc. all support that). It self-detaches from the ticker on
// completion. No setInterval drift; ticker provides per-frame deltaMS.
import type { Application, Ticker } from 'pixi.js'

export type Ease = (t: number) => number

export const linear: Ease = (t) => t
export const easeInQuad: Ease = (t) => t * t
export const easeOutQuad: Ease = (t) => 1 - (1 - t) * (1 - t)
export const easeInOutQuad: Ease = (t) =>
  t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
export const easeOutCubic: Ease = (t) => 1 - Math.pow(1 - t, 3)

let _app: Application | null = null

/** Hook the app once at startup so tweens can subscribe to its ticker. */
export function bindApp(app: Application) {
  _app = app
}

export function tween(
  target: any,
  props: Record<string, number>,
  durationMs: number,
  ease: Ease = easeInOutQuad,
): Promise<void> {
  if (!_app) {
    // Best-effort fallback: snap to end and resolve.
    for (const k of Object.keys(props)) (target as any)[k] = (props as any)[k]
    return Promise.resolve()
  }
  const app = _app
  return new Promise<void>((resolve) => {
    const start = performance.now()
    const from: Record<string, number> = {}
    for (const k of Object.keys(props)) from[k] = (target as any)[k]
    const onTick = (_t: Ticker) => {
      const elapsed = performance.now() - start
      const t = Math.min(1, elapsed / Math.max(durationMs, 1))
      const e = ease(t)
      for (const k of Object.keys(props)) {
        const a = from[k]
        const b = (props as any)[k] as number
        ;(target as any)[k] = a + (b - a) * e
      }
      if (t >= 1) {
        app.ticker.remove(onTick)
        resolve()
      }
    }
    app.ticker.add(onTick)
  })
}

/** Wait `ms` real-time using the ticker (so it pauses with the app). */
export function delay(ms: number): Promise<void> {
  return tween({ _x: 0 }, { _x: 1 }, ms, linear) as Promise<void>
}
