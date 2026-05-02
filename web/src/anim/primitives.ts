// High-level animation primitives composed from tween() + Pixi nodes.
//
// Each primitive returns a Promise that resolves when the animation has
// played out. Primitives are pure visual — they don't touch game state.
// State updates (HP decrement, removal of dead units) happen AFTER the
// primitive resolves, in the resolveTurn() pipeline.
import { Container, Graphics, Text, TextStyle } from 'pixi.js'
import { GlowFilter } from 'pixi-filters'
import {
  delay,
  easeInOutQuad,
  easeOutCubic,
  linear,
  tween,
} from './tween'
import {
  DAMAGE_FLASH_MS,
  DEATH_MS,
  STRIKE_IMPACT_MS,
  STRIKE_TRACER_MS,
} from './timing'
import { COLORS } from '../theme'


/** Tween a Container's (x, y) along a sequence of pixel waypoints.
 *  Each segment uses `msPerHex` ms; total duration scales linearly with
 *  path length. Heading rotation is set to face the next waypoint.
 *
 *  When `fxLayer` and `pathColor` are provided, a dashed track is drawn
 *  for the full path at the start of the animation and fades out at the
 *  end. The "already traversed" portion dims so you can read progress.
 */
export async function animateMovePath(
  node: Container,
  path: Array<{ x: number; y: number }>,
  msPerHex: number,
  rotateSprite: boolean = false,
  fxLayer?: Container,
  pathColor?: number,
): Promise<void> {
  if (path.length < 2 || msPerHex <= 0) return
  // Optional dashed track + arrowhead.
  let pathGfx: Graphics | null = null
  let traveledGfx: Graphics | null = null
  if (fxLayer && pathColor !== undefined) {
    pathGfx = new Graphics()
    drawDashedThroughPath(pathGfx, path, pathColor, 0.7)
    drawArrowAt(pathGfx, path[path.length - 2], path[path.length - 1], pathColor)
    fxLayer.addChildAt(pathGfx, 0)
    // Bright "traveled so far" overlay — drawn each segment to reflect the
    // committed portion of the route.
    traveledGfx = new Graphics()
    fxLayer.addChildAt(traveledGfx, 1)
  }
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1]
    const b = path[i]
    if (rotateSprite) {
      node.rotation = Math.atan2(b.y - a.y, b.x - a.x)
    }
    await tween(node, { x: b.x, y: b.y }, msPerHex, easeInOutQuad)
    if (traveledGfx && pathColor !== undefined) {
      // Re-stroke the committed prefix in solid bright side colour.
      traveledGfx.clear()
      const seg = path.slice(0, i + 1)
      for (let k = 1; k < seg.length; k++) {
        traveledGfx
          .moveTo(seg[k - 1].x, seg[k - 1].y)
          .lineTo(seg[k].x, seg[k].y)
      }
      traveledGfx.stroke({ color: pathColor, width: 2, alpha: 0.85 })
    }
  }
  // Fade + cleanup.
  if (pathGfx) {
    await tween(pathGfx, { alpha: 0 }, 400, easeOutCubic)
    pathGfx.destroy()
  }
  if (traveledGfx) {
    await tween(traveledGfx, { alpha: 0 }, 400, easeOutCubic)
    traveledGfx.destroy()
  }
}


function drawDashedThroughPath(
  g: Graphics,
  path: Array<{ x: number; y: number }>,
  color: number,
  alpha: number,
) {
  for (let i = 1; i < path.length; i++) {
    drawDashedSegment(g, path[i - 1], path[i], color, alpha)
  }
}


function drawDashedSegment(
  g: Graphics,
  a: { x: number; y: number },
  b: { x: number; y: number },
  color: number,
  alpha: number,
) {
  const dx = b.x - a.x, dy = b.y - a.y
  const len = Math.hypot(dx, dy)
  if (len < 1) return
  const ux = dx / len, uy = dy / len
  const dash = 7
  const gap = 5
  let t = 0
  while (t < len) {
    const t2 = Math.min(t + dash, len)
    g.moveTo(a.x + ux * t, a.y + uy * t)
      .lineTo(a.x + ux * t2, a.y + uy * t2)
    t = t2 + gap
  }
  g.stroke({ color, width: 1.6, alpha })
}


function drawArrowAt(
  g: Graphics,
  from: { x: number; y: number },
  to: { x: number; y: number },
  color: number,
) {
  const dx = to.x - from.x, dy = to.y - from.y
  const len = Math.hypot(dx, dy)
  if (len < 1) return
  const ux = dx / len, uy = dy / len
  const tipX = to.x - ux * 4
  const tipY = to.y - uy * 4
  const px = -uy, py = ux
  const baseX = tipX - ux * 9
  const baseY = tipY - uy * 9
  g.poly([
    tipX, tipY,
    baseX + px * 5, baseY + py * 5,
    baseX - px * 5, baseY - py * 5,
  ]).fill({ color, alpha: 0.95 })
}


/** Tracer line + impact burst + floating popup. Total ~600ms.
 *  Adds graphics to `fxLayer`; cleans them up on completion. */
export async function animateStrike(
  fxLayer: Container,
  from: { x: number; y: number },
  to: { x: number; y: number },
  result: { hit: boolean; pkill: number; damage: number; weaponLabel?: string },
): Promise<void> {
  const tracer = new Graphics()
  tracer.filters = [
    new GlowFilter({
      distance: 6,
      outerStrength: 1.6,
      innerStrength: 0,
      color: result.hit ? 0xFF6B4D : COLORS.mute,
    }),
  ]
  fxLayer.addChild(tracer)

  // 0 -> STRIKE_TRACER_MS: line draws from attacker to target
  const head = { t: 0 }
  await tween(head, { t: 1 }, STRIKE_TRACER_MS, easeOutCubic)
    .then(() => undefined)
    .catch(() => undefined)
  // We need to redraw each tick — simpler: set up the tween manually here
  // so we can clear/restroke on every frame. Re-implement:

  // (The above sequential await is wrong — we need redraw every frame.
  // Replace with a custom per-frame redraw.)
  await new Promise<void>((resolve) => {
    const start = performance.now()
    const total = STRIKE_TRACER_MS
    const tick = () => {
      const e = Math.min(1, (performance.now() - start) / total)
      tracer
        .clear()
        .moveTo(from.x, from.y)
        .lineTo(
          from.x + (to.x - from.x) * e,
          from.y + (to.y - from.y) * e,
        )
        .stroke({
          color: result.hit ? 0xFF6B4D : 0xB3B7C0,
          width: 2,
          alpha: 0.95,
        })
      if (e < 1) requestAnimationFrame(tick)
      else resolve()
    }
    requestAnimationFrame(tick)
  })

  // Spawn impact burst + popup concurrently with line fade.
  const burst = result.hit
    ? spawnBurst(fxLayer, to.x, to.y, 'fire', 22)
    : spawnBurst(fxLayer, to.x, to.y, 'puff', 8)
  const pop = popupText(
    fxLayer,
    to.x,
    to.y - 8,
    result.hit
      ? `−${result.damage} HP  Pk ${result.pkill.toFixed(2)}`
      : `MISS  Pk ${result.pkill.toFixed(2)}`,
    result.hit ? COLORS.amber : COLORS.mute,
  )

  // Fade tracer
  tween(tracer, { alpha: 0 }, STRIKE_IMPACT_MS, linear).then(() => {
    tracer.destroy({ children: true })
  })

  await Promise.all([burst, pop])
}


export async function animateDeath(node: Container, fxLayer: Container) {
  const cx = node.x
  const cy = node.y

  // 0–100ms: white-ish flash via tint on the shape graphics (approx)
  await delay(100)

  // Particle burst spawned at center
  const burst = spawnBurst(fxLayer, cx, cy, 'fire', 28)
  const smoke = spawnBurst(fxLayer, cx, cy, 'smoke', 12)

  // 100–800ms: scale + fade-out + slight rotation drift
  await Promise.all([
    tween(node.scale, { x: 1.4, y: 1.4 }, DEATH_MS - 100, easeOutCubic),
    tween(node, { alpha: 0, rotation: 0.25 }, DEATH_MS - 100, linear),
  ])
  node.destroy({ children: true })
  await Promise.all([burst, smoke])
}


export async function flashAndShake(node: Container) {
  const ox = node.x
  const oy = node.y
  await new Promise<void>((resolve) => {
    const start = performance.now()
    const tick = () => {
      const t = Math.min(1, (performance.now() - start) / DAMAGE_FLASH_MS)
      // small jitter
      node.x = ox + (Math.random() * 4 - 2)
      node.y = oy + (Math.random() * 4 - 2)
      if (t >= 1) {
        node.x = ox
        node.y = oy
        resolve()
      } else requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  })
}


/** Floating text that drifts up + fades out. */
export async function popupText(
  layer: Container,
  cx: number,
  cy: number,
  text: string,
  color: number = COLORS.fg,
  durationMs: number = 800,
): Promise<void> {
  const t = new Text({
    text,
    style: new TextStyle({
      fontFamily: 'IBM Plex Mono',
      fontSize: 12,
      fontWeight: '700',
      fill: color,
      stroke: { color: COLORS.bg, width: 3 },
      align: 'center',
    }),
  })
  t.anchor.set(0.5)
  t.x = cx
  t.y = cy
  layer.addChild(t)
  await Promise.all([
    tween(t, { y: cy - 28, alpha: 0 }, durationMs, easeOutCubic),
  ])
  t.destroy()
}


// ---- Particle bursts (lightweight rolled-our-own) ----

interface ParticleSpec {
  count: number
  speed: [number, number]    // px/sec range
  life: [number, number]     // ms range
  size: [number, number]     // px radius
  colors: number[]
  gravity: number            // px/sec^2 downward (negative = upward)
  fadeOut: boolean
}

const FIRE: ParticleSpec = {
  count: 0,
  speed: [80, 200],
  life: [350, 600],
  size: [2.5, 5],
  colors: [0xFFB84D, 0xFF8E3C, 0xFFFFFF],
  gravity: -40,
  fadeOut: true,
}

const SMOKE: ParticleSpec = {
  count: 0,
  speed: [20, 60],
  life: [600, 1200],
  size: [3, 6],
  colors: [0x7A879A, 0x4D5564],
  gravity: -20,
  fadeOut: true,
}

const PUFF: ParticleSpec = {
  count: 0,
  speed: [40, 110],
  life: [220, 350],
  size: [1.5, 3],
  colors: [0xB3B7C0, 0x7A879A],
  gravity: 0,
  fadeOut: true,
}


export function spawnBurst(
  layer: Container,
  cx: number,
  cy: number,
  kind: 'fire' | 'smoke' | 'puff',
  count: number,
): Promise<void> {
  const spec = { ...(kind === 'fire' ? FIRE : kind === 'smoke' ? SMOKE : PUFF), count }
  const particles: Array<{
    g: Graphics
    vx: number
    vy: number
    age: number
    life: number
  }> = []
  for (let i = 0; i < spec.count; i++) {
    const angle = Math.random() * Math.PI * 2
    const sp = lerp(spec.speed[0], spec.speed[1], Math.random())
    const r0 = lerp(spec.size[0], spec.size[1], Math.random())
    const life = lerp(spec.life[0], spec.life[1], Math.random())
    const color = spec.colors[Math.floor(Math.random() * spec.colors.length)]
    const g = new Graphics().circle(0, 0, r0).fill({ color })
    g.x = cx
    g.y = cy
    layer.addChild(g)
    particles.push({
      g,
      vx: Math.cos(angle) * sp,
      vy: Math.sin(angle) * sp,
      age: 0,
      life,
    })
  }
  return new Promise<void>((resolve) => {
    let last = performance.now()
    const tick = () => {
      const now = performance.now()
      const dt = (now - last) / 1000
      last = now
      let alive = 0
      for (const p of particles) {
        if (p.age >= p.life) continue
        p.age += dt * 1000
        p.g.x += p.vx * dt
        p.g.y += p.vy * dt
        p.vy += spec.gravity * dt
        if (spec.fadeOut) p.g.alpha = Math.max(0, 1 - p.age / p.life)
        if (p.age >= p.life) {
          p.g.destroy()
        } else {
          alive++
        }
      }
      if (alive > 0) requestAnimationFrame(tick)
      else resolve()
    }
    requestAnimationFrame(tick)
  })
}


function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t
}
