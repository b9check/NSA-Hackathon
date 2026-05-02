// One-shot icon loader. Resolves once on app boot so unit/base nodes can
// pull a Sprite from the texture cache synchronously.
import { Assets, Texture } from 'pixi.js'


// Keys must match either Platform.key or Base.key — the same identifiers
// scripts/fetch_icons.py wrote SVGs for under web/public/icons/<key>.svg.
const PLATFORM_KEYS = [
  'f35a', 'j20', 'mq9', 'recon_uav', 'cg47', 'type055',
  'patriot', 'hq9', 'm1a2', 'mech_b', 'mech_r', 'shahed',
] as const

const BASE_KEYS = [
  'blue_airbase', 'red_airbase',
  'blue_navalbase', 'red_navalbase',
  'blue_fob', 'red_launchsite',
] as const

const textures = new Map<string, Texture>()
let loaded = false


export async function loadIcons(): Promise<void> {
  if (loaded) return
  const all = [...PLATFORM_KEYS, ...BASE_KEYS]
  // Pixi v8 rasterizes SVG to a Texture at native viewBox size on Assets.load.
  await Promise.all(
    all.map(async (key) => {
      try {
        const tex = (await Assets.load(`/icons/${key}.svg`)) as Texture
        textures.set(key, tex)
      } catch (e) {
        console.warn(`icon load failed: ${key}`, e)
      }
    }),
  )
  loaded = true
}


export function getIcon(key: string): Texture | null {
  return textures.get(key) ?? null
}
