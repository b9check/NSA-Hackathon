import { useStore } from '../store'
import type { BaseInstance, SensorRef, UnitInstance, WeaponRef } from '../types'
import { ActionMenu } from './ActionMenu'
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
  const blueObj = game.objective_points?.blue ?? 0
  const redObj = game.objective_points?.red ?? 0
  return (
    <div className="h-12 bg-panel border-b border-line flex items-center px-5 text-sm font-mono">
      <div className="text-amber font-semibold tracking-widest">{game.name.toUpperCase()}</div>
      <div className="mx-6 text-mute">|</div>
      <div className="text-mute">WIN</div>
      <div className="ml-2 text-fg">
        highest score in 5:00
      </div>
      <div className="mx-6 text-mute">|</div>
      <div className="text-mute">OBJECTIVES</div>
      <div className="ml-2 flex items-center gap-2">
        <span className="text-amber">{game.map.objective_hexes.length}</span>
        <span className="text-mute text-xs">+5/turn ea, cap +30</span>
        {(blueObj > 0 || redObj > 0) && (
          <>
            <span className="text-mute mx-1">·</span>
            <span className="text-blue">B {blueObj.toFixed(0)}</span>
            <span className="text-mute">/</span>
            <span className="text-red">R {redObj.toFixed(0)}</span>
          </>
        )}
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
  const blueBases = (game.bases ?? []).filter((b) => b.side === 'blue')
  const redBases = (game.bases ?? []).filter((b) => b.side === 'red')

  return (
    <div className="w-[360px] bg-panel border-l border-line flex flex-col h-full text-sm min-h-0">
      <Section title="ORDER OF BATTLE" maxH="max-h-[13rem]">
        <RosterGroup label="BLUE" side="blue" units={blue} selectedId={selectedUnitId} onSelect={selectUnit} />
        <div className="h-2" />
        <RosterGroup label="RED" side="red" units={red} selectedId={selectedUnitId} onSelect={selectUnit} />
      </Section>
      {(blueBases.length > 0 || redBases.length > 0) && (
        <Section title="BASES" maxH="max-h-[7rem]">
          {blueBases.length > 0 && <BaseGroup label="BLUE" side="blue" bases={blueBases} />}
          {blueBases.length > 0 && redBases.length > 0 && <div className="h-2" />}
          {redBases.length > 0 && <BaseGroup label="RED" side="red" bases={redBases} />}
        </Section>
      )}
      <Section title="SELECTED UNIT" grow>
        {selected ? <UnitDetail unit={selected} /> : <Empty />}
      </Section>
    </div>
  )
}

function Section({
  title, children, grow, maxH,
}: {
  title: string
  children: React.ReactNode
  grow?: boolean
  /** Tailwind max-h-* class for non-grow sections that can get long
   *  (rosters, bases). Ignored when grow is set. */
  maxH?: string
}) {
  return (
    <div
      className={[
        'border-b border-line flex flex-col min-h-0',
        grow ? 'flex-1' : 'shrink-0',
      ].join(' ')}
    >
      <div className="px-4 py-2 text-[10px] tracking-[0.18em] font-mono text-mute shrink-0">
        {title}
      </div>
      <div
        className={[
          'px-4 pb-3 overflow-y-auto overflow-x-hidden',
          grow ? 'flex-1 min-h-0' : (maxH ?? ''),
        ].join(' ')}
      >
        {children}
      </div>
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
// by ANY friendly UNIT or BASE sensor range (so the base's own hex always
// counts even when no mobile unit is parked nearby).
function computeRosterVisibility(
  game: import('../types').GameState,
  side: 'blue' | 'red',
): Set<string> {
  const out = new Set<string>()
  const sources: Array<{ col: number; row: number; sensor: number }> = []
  for (const u of game.units) {
    if (u.side === side) sources.push({ col: u.col, row: u.row, sensor: u.sensor })
  }
  for (const b of game.bases ?? []) {
    if (b.side === side) sources.push({ col: b.col, row: b.row, sensor: b.sensor })
  }
  for (const s of sources) {
    for (const cell of game.map.cells) {
      const d = hexDist(s.col, s.row, cell.col, cell.row)
      if (d <= s.sensor) out.add(`${cell.col},${cell.row}`)
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
  const viewMode = useStore((s) => s.viewMode)
  const accent = unit.side === 'blue' ? 'text-blue' : 'text-red'
  // Action menu only shows for the side currently in control:
  //   - omniscient view -> Blue is the player by convention
  //   - blue view       -> Blue
  //   - red view        -> Red
  const playerSide: 'blue' | 'red' =
    viewMode === 'red' ? 'red' : 'blue'
  const canCommand = unit.side === playerSide
  return (
    <div className="space-y-3 min-w-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`text-[13px] font-semibold ${accent} truncate`}>
            {unit.display}
          </span>
          {unit.stealth && (
            <span className="shrink-0 text-[9px] font-mono text-amber border border-amber/60 px-1 rounded-sm">
              STEALTH
            </span>
          )}
        </div>
        {unit.role && (
          <div className="text-[11px] text-mute mt-0.5 leading-snug">{unit.role}</div>
        )}
        <div className="text-[10px] font-mono text-mute mt-0.5 truncate">
          {unit.id} · {DOMAIN_LABEL[unit.domain] ?? unit.domain.toUpperCase()} · ({unit.col},{unit.row})
        </div>
      </div>
      {canCommand && <ActionMenu unit={unit} />}
      <div className="grid grid-cols-3 gap-y-2 gap-x-3 text-[11px] font-mono">
        <Stat label="HP"     value={`${unit.hp}/${unit.max_hp}`} />
        <Stat label="SPEED"  value={`${unit.speed}`}  suffix="hx/t" />
        <Stat label="COST"   value={`${unit.cost}`}   suffix="pts" />
        <Stat label="SENSOR" value={`${unit.sensor}`} suffix={unit.sensor === 0 ? 'own hex' : 'hex'} />
        <Stat label="WEAPON" value={unit.weapon === 0 ? '—' : `${unit.weapon}`}
              suffix={unit.weapon === 0 ? 'ISR' : 'hex'} />
        <Stat label="GLYPH"  value={unit.glyph} />
      </div>
      {unit.sensors.length > 0 && (
        <SubsystemList
          title="SENSORS"
          items={unit.sensors.map((s) => ({
            key: s.key,
            primary: s.display,
            badge: s.modality,
            badgeClass: SENSOR_BADGE[s.modality] ?? 'text-mute',
            meta: `range ${s.range}${s.emits ? ' · emits' : ''}${s.los_required ? ' · LOS' : ''}`,
          }))}
        />
      )}
      {unit.weapons.length > 0 && (
        <SubsystemList
          title="WEAPONS"
          items={unit.weapons.map((w) => ({
            key: w.key,
            primary: w.display,
            badge: humanKind(w.kind),
            badgeClass: 'text-amber',
            meta: weaponMeta(w),
          }))}
        />
      )}
    </div>
  )
}

const SENSOR_BADGE: Record<string, string> = {
  radar:  'text-blue',
  eo:     'text-green',
  ir:     'text-amber',
  sigint: 'text-red',
  sonar:  'text-mute',
}

function humanKind(k: WeaponRef['kind']): string {
  return ({
    aam: 'AAM',
    asm_air: 'ASM',
    asm_ship: 'ASM',
    sam: 'SAM',
    gun_naval: 'GUN',
    gun_armor: 'GUN',
    manpads: 'MANPADS',
    loitering: 'LOITER',
  } as Record<string, string>)[k] ?? k.toUpperCase()
}

function weaponMeta(w: WeaponRef): string {
  const parts: string[] = [`r${w.range}`]
  if (w.ammo > 0) parts.push(`${w.ammo}rd`)
  const pk = Object.entries(w.pkill)
    .map(([d, p]) => `${d[0].toUpperCase()}=${p.toFixed(2)}`)
    .join(' ')
  if (pk) parts.push(pk)
  return parts.join(' · ')
}

function SubsystemList({
  title,
  items,
}: {
  title: string
  items: Array<{
    key: string
    primary: string
    badge: string
    badgeClass: string
    meta: string
  }>
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] tracking-widest text-mute mb-1.5 font-mono">{title}</div>
      <div className="space-y-1">
        {items.map((it) => (
          <div key={it.key} className="border border-line rounded-sm px-2 py-1 min-w-0">
            <div className="flex items-baseline justify-between gap-2 min-w-0">
              <span className="text-[11px] text-fg truncate min-w-0 flex-1">
                {it.primary}
              </span>
              <span
                className={`shrink-0 text-[9px] font-mono uppercase tracking-wider ${it.badgeClass}`}
              >
                {it.badge}
              </span>
            </div>
            <div className="text-[10px] font-mono text-mute mt-0.5 break-all">
              {it.meta}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function BaseGroup({
  label, side, bases,
}: {
  label: string
  side: 'blue' | 'red'
  bases: BaseInstance[]
}) {
  const color = side === 'blue' ? 'text-blue' : 'text-red'
  const dot = side === 'blue' ? 'bg-blue' : 'bg-red'
  return (
    <div>
      <div className={`flex items-center gap-2 text-[11px] font-mono ${color} mb-1.5`}>
        <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
        {label}
      </div>
      <div className="grid grid-cols-1 gap-0.5">
        {bases.map((b) => (
          <div
            key={b.id}
            className="px-2 py-1 rounded-sm font-mono text-xs text-mute flex items-center justify-between"
          >
            <span className="truncate">{b.display}</span>
            <span className="text-[10px] opacity-70">
              ({b.col},{b.row}) · hp {b.hp}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Stat({ label, value, suffix }: { label: string; value: string; suffix?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[9px] tracking-widest text-mute">{label}</div>
      <div className="text-fg truncate text-[11px]">
        {value}
        {suffix && <span className="text-mute ml-1 text-[9px]">{suffix}</span>}
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
