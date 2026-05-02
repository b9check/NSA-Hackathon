import { useStore } from '../store'
import type { UnitInstance } from '../types'
import { RegionPicker } from './RegionPicker'
import { ViewModeToggle } from './ViewModeToggle'

const DOMAIN_LABEL: Record<string, string> = {
  air: 'AIR',
  sea: 'SEA',
  land: 'LAND',
  amphib: 'AMPHIB',
}

export function TopBar() {
  const game = useStore((s) => s.game)
  if (!game) return null
  return (
    <div className="h-12 bg-panel border-b border-line flex items-center px-5 text-sm font-mono">
      <div className="text-amber font-semibold tracking-widest">{game.name.toUpperCase()}</div>
      <div className="mx-6 text-mute">|</div>
      <div className="text-mute">TURN</div>
      <div className="ml-2 text-fg font-semibold">{game.turn}/{game.turn_limit}</div>
      <div className="mx-6 text-mute">|</div>
      <div className="text-mute">OBJECTIVE</div>
      <div className="ml-2 text-amber">
        Capture &amp; hold {game.map.objective_hexes.length} urban hexes for {game.victory.capture_hold_turns} turns
      </div>
      <div className="ml-auto flex items-center gap-4">
        <FactionPill side="blue" />
        <FactionPill side="red" />
        <div className="w-px h-5 bg-line" />
        <ViewModeToggle />
        <RegionPicker />
      </div>
    </div>
  )
}

function FactionPill({ side }: { side: 'blue' | 'red' }) {
  const game = useStore((s) => s.game)
  if (!game) return null
  const units = game.units.filter((u) => u.side === side)
  const power = units.reduce((acc, u) => acc + u.cost, 0)
  const color = side === 'blue' ? 'text-blue' : 'text-red'
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`font-semibold ${color}`}>{side.toUpperCase()}</span>
      <span className="text-mute">{units.length} units · {power} pts</span>
    </div>
  )
}

export function RightRail() {
  const game = useStore((s) => s.game)
  const selectedUnitId = useStore((s) => s.selectedUnitId)
  const selectUnit = useStore((s) => s.selectUnit)
  if (!game) return null
  const selected = game.units.find((u) => u.id === selectedUnitId) ?? null
  const blue = game.units.filter((u) => u.side === 'blue')
  const red = game.units.filter((u) => u.side === 'red')

  return (
    <div className="w-[340px] bg-panel border-l border-line flex flex-col h-full text-sm">
      <Section title="ORDER OF BATTLE">
        <RosterGroup label="BLUE" side="blue" units={blue} selectedId={selectedUnitId} onSelect={selectUnit} />
        <div className="h-2" />
        <RosterGroup label="RED" side="red" units={red} selectedId={selectedUnitId} onSelect={selectUnit} />
      </Section>
      <Section title="SELECTED UNIT" grow>
        {selected ? <UnitDetail unit={selected} /> : <Empty />}
      </Section>
      <Section title="LEGEND">
        <Legend />
      </Section>
    </div>
  )
}

function Section({
  title, children, grow,
}: {
  title: string
  children: React.ReactNode
  grow?: boolean
}) {
  return (
    <div className={`border-b border-line ${grow ? 'flex-1 min-h-0' : ''}`}>
      <div className="px-4 py-2 text-[10px] tracking-[0.18em] font-mono text-mute">
        {title}
      </div>
      <div className="px-4 pb-3 overflow-auto">{children}</div>
    </div>
  )
}

function RosterGroup({
  label, side, units, selectedId, onSelect,
}: {
  label: string
  side: 'blue' | 'red'
  units: UnitInstance[]
  selectedId: string | null
  onSelect: (id: string | null) => void
}) {
  const viewMode = useStore((s) => s.viewMode)
  const game = useStore((s) => s.game)
  const color = side === 'blue' ? 'text-blue' : 'text-red'
  const dot = side === 'blue' ? 'bg-blue' : 'bg-red'

  // In a side view, an "own" roster entry is fully clickable; an enemy entry
  // is shown but disabled, and units that aren't currently sensed are hidden.
  let displayUnits = units
  if (viewMode !== 'omniscient' && side !== viewMode && game) {
    const visible = computeRosterVisibility(game, viewMode)
    displayUnits = units.filter((u) => visible.has(`${u.col},${u.row}`))
  }

  return (
    <div>
      <div className={`flex items-center gap-2 text-[11px] font-mono ${color} mb-1.5`}>
        <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
        {label}
        {viewMode !== 'omniscient' && side !== viewMode && (
          <span className="text-[9px] text-mute ml-1">
            ({displayUnits.length}/{units.length} sensed)
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-0.5">
        {displayUnits.map((u) => {
          const active = u.id === selectedId
          // Selectable when the unit's side matches the active player view
          // (or in omniscient mode for the friendlies-as-Blue convention).
          const enabled = viewMode === 'omniscient'
            ? u.side === 'blue'
            : u.side === viewMode
          return (
            <button
              key={u.id}
              onClick={() => enabled && onSelect(active ? null : u.id)}
              disabled={!enabled}
              className={[
                'flex items-center justify-between px-2 py-1 rounded-sm font-mono text-xs',
                'border border-transparent text-left',
                active ? 'bg-panel2 border-amber text-fg' : 'text-mute hover:text-fg hover:bg-panel2/60',
                !enabled ? 'cursor-default opacity-75' : 'cursor-pointer',
              ].join(' ')}
            >
              <span className="truncate">{u.display}</span>
              <span className="text-[10px] opacity-70">
                ({u.col},{u.row})
              </span>
            </button>
          )
        })}
        {displayUnits.length === 0 && (
          <div className="text-[10px] font-mono text-mute opacity-60 px-1 py-0.5">
            no contacts
          </div>
        )}
      </div>
    </div>
  )
}

// Tiny copy of the visibility math so HUD components don't import from MapStage.
// Accepts blue/red view modes; returns the set of (col,row) keys reachable
// by ANY friendly unit's sensor range.
function computeRosterVisibility(
  game: import('../types').GameState,
  side: 'blue' | 'red',
): Set<string> {
  const out = new Set<string>()
  for (const u of game.units) {
    if (u.side !== side) continue
    for (const cell of game.map.cells) {
      const d = hexDist(u.col, u.row, cell.col, cell.row)
      if (d <= u.sensor) out.add(`${cell.col},${cell.row}`)
    }
  }
  return out
}

function hexDist(ac: number, ar: number, bc: number, br: number) {
  const a = toCube(ac, ar)
  const b = toCube(bc, br)
  return (Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.z - b.z)) / 2
}
function toCube(c: number, r: number) {
  const x = c - (r - (r & 1)) / 2
  const z = r
  return { x, y: -x - z, z }
}

function UnitDetail({ unit }: { unit: UnitInstance }) {
  const accent = unit.side === 'blue' ? 'text-blue' : 'text-red'
  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-baseline gap-2">
          <span className={`text-base font-semibold ${accent}`}>{unit.display}</span>
          {unit.stealth && (
            <span className="text-[10px] font-mono text-amber border border-amber/60 px-1 rounded-sm">
              STEALTH
            </span>
          )}
        </div>
        <div className="text-[11px] font-mono text-mute mt-0.5">
          {unit.id} · {DOMAIN_LABEL[unit.domain] ?? unit.domain.toUpperCase()} · ({unit.col},{unit.row})
        </div>
      </div>
      <div className="grid grid-cols-2 gap-y-2 gap-x-4 text-xs font-mono">
        <Stat label="HP"      value={`${unit.hp}`} />
        <Stat label="COST"    value={`${unit.cost} pts`} />
        <Stat label="SPEED"   value={`${unit.speed}`} suffix="hex/turn" />
        <Stat label="SENSOR"  value={`${unit.sensor}`} suffix="hex" />
        <Stat label="WEAPON"  value={unit.weapon === 0 ? '—' : `${unit.weapon}`} suffix={unit.weapon === 0 ? 'ISR only' : 'hex'} />
        <Stat label="GLYPH"   value={unit.glyph} />
      </div>
      <div className="text-[11px] text-mute leading-relaxed">
        Click any blue unit to inspect. The amber ring shows weapon range, the green ring shows sensor range, and the tinted hexes show reachable destinations this turn.
      </div>
    </div>
  )
}

function Stat({ label, value, suffix }: { label: string; value: string; suffix?: string }) {
  return (
    <div>
      <div className="text-[10px] tracking-widest text-mute">{label}</div>
      <div className="text-fg">
        {value}
        {suffix && <span className="text-mute ml-1 text-[10px]">{suffix}</span>}
      </div>
    </div>
  )
}

function Empty() {
  return (
    <div className="text-[11px] text-mute leading-relaxed">
      Select a unit on the map or in the roster to view its capabilities.
    </div>
  )
}

function Legend() {
  const items = [
    { swatch: '#0E1A2E', label: 'water' },
    { swatch: '#223028', label: 'open' },
    { swatch: '#18301F', label: 'forest' },
    { swatch: '#3A342A', label: 'mountain' },
    { swatch: '#2E3645', label: 'urban' },
  ]
  return (
    <div className="grid grid-cols-2 gap-y-1 text-[11px] font-mono text-mute">
      {items.map((it) => (
        <div key={it.label} className="flex items-center gap-2">
          <span
            className="w-3 h-3 border border-line"
            style={{ background: it.swatch }}
          />
          <span>{it.label}</span>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <span className="w-3 h-3 rounded-sm border-2 border-amber" />
        <span>objective</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="w-3 h-3 border border-blue" />
        <span>blue unit</span>
      </div>
      <div className="flex items-center gap-2">
        <span
          className="w-3 h-3 border border-red"
          style={{ transform: 'rotate(45deg)' }}
        />
        <span>red unit</span>
      </div>
    </div>
  )
}
