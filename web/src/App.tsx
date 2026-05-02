import { useEffect } from 'react'
import { useStore } from './store'
import { MapStage } from './components/MapStage'
import { TopBar, RightRail } from './components/HUD'

export default function App() {
  const game = useStore((s) => s.game)
  const assetVersion = useStore((s) => s.assetVersion)
  const swapping = useStore((s) => s.swapping)
  const loadRegions = useStore((s) => s.loadRegions)
  const refetchState = useStore((s) => s.refetchState)

  // On mount: fetch the region list (for the picker) + the initial state.
  useEffect(() => {
    loadRegions()
    refetchState().catch((e) => console.error('initial state load failed', e))
  }, [loadRegions, refetchState])

  return (
    <div className="h-screen w-screen flex flex-col">
      <TopBar />
      <div className="flex-1 flex min-h-0 relative">
        <div className="flex-1 min-w-0 overflow-auto bg-bg">
          {game ? (
            // Key by version so the Pixi stage fully unmounts on region swap
            // and re-loads /terrain.png with a fresh cache-busted URL.
            <MapStage key={assetVersion} />
          ) : (
            <div className="h-full flex items-center justify-center text-mute font-mono text-sm">
              loading state.json…
            </div>
          )}
        </div>
        <RightRail />
        {swapping && <SwapOverlay />}
      </div>
      <BottomBar />
    </div>
  )
}

function SwapOverlay() {
  return (
    <div className="absolute inset-0 bg-bg/70 backdrop-blur-sm flex items-center justify-center z-40">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-amber border-t-transparent rounded-full animate-spin" />
        <div className="text-[11px] font-mono tracking-[0.2em] text-amber">
          REGENERATING SCENARIO
        </div>
      </div>
    </div>
  )
}

function BottomBar() {
  const game = useStore((s) => s.game)
  const hover = useStore((s) => s.hoverHex)
  const selected = useStore((s) => s.selectedUnit())
  if (!game) return null
  const cell = hover
    ? game.map.cells.find((c) => c.col === hover.col && c.row === hover.row)
    : null
  return (
    <div className="h-8 bg-panel border-t border-line flex items-center px-5 text-[11px] font-mono text-mute gap-6">
      <span>SCENARIO {game.name}</span>
      <span>SEED {game.seed}</span>
      {hover && (
        <span>
          HEX ({hover.col},{hover.row}) — {cell ? cell.terrain : '—'}
        </span>
      )}
      {selected && (
        <span className="text-amber">SELECTED {selected.display}</span>
      )}
      <span className="ml-auto opacity-50">alex_game_engine · 0→1 build</span>
    </div>
  )
}
