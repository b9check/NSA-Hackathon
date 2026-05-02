import { useEffect } from 'react'
import { useStore } from './store'
import { MapStage } from './components/MapStage'
import { TopBar, RightRail } from './components/HUD'
import type { GameState } from './types'

export default function App() {
  const game = useStore((s) => s.game)
  const setGame = useStore((s) => s.setGame)

  useEffect(() => {
    fetch('/state.json')
      .then((r) => r.json())
      .then((g: GameState) => setGame(g))
      .catch((err) => console.error('failed to load /state.json', err))
  }, [setGame])

  return (
    <div className="h-screen w-screen flex flex-col">
      <TopBar />
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0 overflow-auto bg-bg">
          {game ? (
            <MapStage />
          ) : (
            <div className="h-full flex items-center justify-center text-mute font-mono text-sm">
              loading state.json…
            </div>
          )}
        </div>
        <RightRail />
      </div>
      <BottomBar />
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
