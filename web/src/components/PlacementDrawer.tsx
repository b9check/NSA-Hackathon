// Placement-mode drawer: replaces the right-rail unit-stats panel
// while placement_phase is active. Compact icon grid — one tile per
// stashed unit on each manual side. Tiles are HTML5-draggable; the
// MapStage canvas has matching dragover/drop listeners that route the
// drop through placeUnit.
import { useEffect, useState } from 'react'
import { useStore } from '../store'
import type { UnitInstance } from '../types'


const TYPE_LABEL: Record<string, string> = {
  destroyer: 'DD',
  fighter: 'FTR',
  bomber: 'BMR',
  scout_drone: 'SCT',
  missile_launcher: 'MSL',
  armor: 'ARM',
  infantry: 'INF',
  strike_drone: 'STR',
}


function shortLabel(unit: UnitInstance): string {
  return TYPE_LABEL[unit.type] ?? unit.type.slice(0, 3).toUpperCase()
}


export function PlacementDrawer() {
  const placementActive = useStore((s) => s.placementActive)
  const controllers = useStore((s) => s.controllers)
  const stashed = useStore((s) => s.placementStashed)
  if (!placementActive) return null

  const sides: Array<'blue' | 'red'> = []
  if (controllers.blue === 'manual') sides.push('blue')
  if (controllers.red === 'manual') sides.push('red')
  if (sides.length === 0) return null

  const totalUnplaced = sides.reduce((n, s) => n + (stashed[s]?.length || 0), 0)

  return (
    <div className="h-full w-full flex flex-col bg-panel border-l border-line">
      <div className="px-3 py-3 border-b border-line">
        <div className="text-[10px] font-mono tracking-[0.2em] text-amber mb-1">
          PLACEMENT
        </div>
        <div className="text-[11px] font-mono text-mute leading-snug">
          Drag units onto your half of the map.
        </div>
        <div className="text-[10px] font-mono mt-2 tabular-nums">
          {totalUnplaced === 0
            ? <span className="text-amber">all placed — ready to lock in</span>
            : <span className="text-mute">{totalUnplaced} left to place</span>}
        </div>
      </div>
      <PlacementErrorBanner />
      <div className="flex-1 min-h-0 overflow-y-auto p-3 flex flex-col gap-3">
        {sides.map((side) => (
          <SideRoster key={side} side={side} units={stashed[side] ?? []} />
        ))}
      </div>
    </div>
  )
}


function PlacementErrorBanner() {
  const err = useStore((s) => s.placementError)
  const ts = useStore((s) => s.placementErrorTs)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (!err) return
    setVisible(true)
    const t = setTimeout(() => setVisible(false), 2800)
    return () => clearTimeout(t)
  }, [err, ts])
  if (!err || !visible) return null
  return (
    <div className="px-3 pt-2">
      <div className="border border-red/60 bg-red/10 text-red rounded-sm px-2 py-1.5 font-mono text-[10px] leading-snug">
        {err}
      </div>
    </div>
  )
}


function SideRoster({ side, units }: { side: 'blue' | 'red'; units: UnitInstance[] }) {
  const c = side === 'blue' ? 'border-blue/40' : 'border-red/40'
  const sideText = side === 'blue' ? 'text-blue' : 'text-red'
  return (
    <div className={`bg-bg/30 border ${c} rounded-sm p-2`}>
      <div className={`text-[10px] font-mono tracking-widest mb-2 ${sideText}`}>
        {side.toUpperCase()} · {units.length}
      </div>
      {units.length === 0 ? (
        <div className="text-[10px] font-mono text-mute py-1">all placed</div>
      ) : (
        <div className="grid grid-cols-3 gap-1.5">
          {units.map((u) => <UnitTile key={u.id} unit={u} />)}
        </div>
      )}
    </div>
  )
}


function UnitTile({ unit }: { unit: UnitInstance }) {
  const sideRing = unit.side === 'blue'
    ? 'border-blue/40 hover:border-blue'
    : 'border-red/40 hover:border-red'
  return (
    <div
      draggable
      onDragStart={(ev) => {
        ev.dataTransfer.effectAllowed = 'move'
        ev.dataTransfer.setData('application/x-unit-id', unit.id)
        ev.dataTransfer.setData('text/plain', unit.id)
      }}
      title={unit.display}
      className={[
        'relative aspect-square rounded-sm border bg-panel/90',
        'flex flex-col items-center justify-center gap-0.5 px-0.5',
        'cursor-grab active:cursor-grabbing select-none transition-colors',
        sideRing,
      ].join(' ')}
    >
      <img
        src={`/icons/${unit.type}.svg`}
        alt={unit.type}
        draggable={false}
        className="w-7 h-7 pointer-events-none opacity-90"
        onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
      />
      <div className="text-[9px] font-mono tracking-wider text-mute leading-none">
        {shortLabel(unit)}
      </div>
    </div>
  )
}
