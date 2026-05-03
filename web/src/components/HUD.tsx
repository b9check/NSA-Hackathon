import * as React from 'react'
import { useStore } from '../store'
import type { BaseInstance, Contact, Mission, SensorRef, UnitInstance, WeaponRef } from '../types'
import { RegionPicker } from './RegionPicker'
import { ViewModeToggle, RealGameToggle } from './ViewModeToggle'

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
      <div className="ml-auto flex items-center gap-3 flex-shrink-0">
        <ViewModeToggle />
        <div className="w-px h-5 bg-line mx-1" />
        <RegionPicker />
      </div>
    </div>
  )
}


function WinChip({ label, tip }: { label: string; tip: string }) {
  return (
    <span
      title={tip}
      className="h-7 px-2.5 inline-flex items-center rounded-sm border border-line/80
                 bg-panel2/40 text-[10px] font-mono text-fg tracking-wider whitespace-nowrap"
    >
      {label}
    </span>
  )
}


function WinChipSep() {
  return <span className="text-mute text-[10px] tracking-widest opacity-50">OR</span>
}

function FactionPill({ side }: { side: 'blue' | 'red' }) {
  const game = useStore((s) => s.game)
  if (!game) return null
  const units = game.units.filter((u) => u.side === side)
  const power = units.reduce((acc, u) => acc + u.cost, 0)
  const accent =
    side === 'blue'
      ? { text: 'text-blue', bg: 'bg-blue', border: 'border-blue/40' }
      : { text: 'text-red',  bg: 'bg-red',  border: 'border-red/40'  }
  return (
    <div
      className={[
        'h-8 px-2.5 inline-flex items-center gap-2 rounded-sm border bg-panel2/40',
        'font-mono text-[11px]',
        accent.border,
      ].join(' ')}
      title={`${side.toUpperCase()} — ${units.length} units, ${power} cost-points`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${accent.bg}`} />
      <span className={`${accent.text} font-semibold`}>{side.toUpperCase()}</span>
      <span className="text-mute tabular-nums">{units.length}</span>
      <span className="text-mute opacity-50">|</span>
      <span className="text-mute tabular-nums">{power}<span className="opacity-60">pt</span></span>
    </div>
  )
}

export function RightRail() {
  const game = useStore((s) => s.game)
  const selectedUnitId = useStore((s) => s.selectedUnitId)
  const viewMode = useStore((s) => s.viewMode)
  if (!game) return null
  const selected = game.units.find((u) => u.id === selectedUnitId) ?? null

  // If viewing as a side (blue/red), and the selected unit is the OTHER side's,
  // show the fused intel picture (contact + probabilities) instead of ground truth.
  let contactView: Contact | null = null
  if (selected && viewMode !== 'omniscient' && selected.side !== viewMode) {
    const myContacts = game.contacts?.[viewMode] ?? []
    contactView = myContacts.find((c) => c.contact_id === selected.id) ?? null
  }

  return (
    <div className="w-[360px] bg-panel border-l border-line flex flex-col h-full text-sm min-h-0">
      <Section title={contactView ? 'CONTACT' : 'SELECTED UNIT'} grow>
        {contactView ? (
          <ContactDetail contact={contactView} truthHidden />
        ) : selected ? (
          <UnitDetail unit={selected} />
        ) : (
          <Empty />
        )}
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

// Lookup table for human-readable platform-key labels in the class-prob bars.
const PLATFORM_LABEL: Record<string, string> = {
  infantry: 'Infantry',
  armor: 'Armor',
  missile_launcher: 'SAM Launcher',
  scout_drone: 'Scout Drone',
  strike_drone: 'Strike Drone',
  fighter: 'Fighter',
  bomber: 'Bomber',
  destroyer: 'Destroyer',
}

function ContactDetail({ contact, truthHidden }: { contact: Contact; truthHidden?: boolean }) {
  // Sort class probabilities high-to-low; trim to top 5
  const probs = Object.entries(contact.class_probs).sort((a, b) => b[1] - a[1])
  const leadingClass = probs[0]?.[0] ?? '?'
  const leadingProb = probs[0]?.[1] ?? 0
  const leadingLabel = PLATFORM_LABEL[leadingClass] ?? leadingClass
  // Effective ID confidence = existence × leading conditional. Bounded by existence.
  const effective = contact.existence * leadingProb
  const stale = !contact.currently_observed
  return (
    <div className="space-y-3 min-w-0">
      <div className="min-w-0">
        <div className="flex items-baseline gap-2 min-w-0">
          <span className="text-[13px] font-semibold text-amber truncate">
            {leadingLabel}
          </span>
          <span className="shrink-0 text-[10px] font-mono text-amber tabular-nums">
            {(effective * 100).toFixed(0)}%
          </span>
          {stale && (
            <span className="shrink-0 text-[9px] font-mono text-mute border border-mute/40 px-1 rounded-sm uppercase tracking-wider">
              Stale
            </span>
          )}
          {!stale && (
            <span className="shrink-0 text-[9px] font-mono text-blue border border-blue/40 px-1 rounded-sm uppercase tracking-wider">
              {contact.dominant_modality}
            </span>
          )}
        </div>
        <div className="text-[10px] font-mono text-mute mt-0.5 truncate">
          {contact.contact_id} · believed ({contact.believed_col},{contact.believed_row})
        </div>
        {truthHidden && (
          <div className="text-[10px] text-mute mt-0.5 italic leading-snug">
            Fused from your sensors. Truth hidden.
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-y-2 gap-x-3 text-[11px] font-mono">
        <Stat label="EXISTENCE" value={`${(contact.existence * 100).toFixed(0)}`} suffix="%" />
        <Stat label="UNCERT." value={`±${contact.position_uncertainty_hexes.toFixed(1)}`} suffix="hex" />
        <Stat label="REFINED" value={`t${contact.last_refined_turn}`} />
        <Stat label="OBSERVERS" value={`${contact.contributing_sensor_ids.length}`} />
      </div>

      <div className="min-w-0">
        <div className="text-[10px] tracking-widest text-mute mb-1.5 font-mono">
          CLASS DISTRIBUTION
        </div>
        <div className="space-y-1.5">
          {probs.slice(0, 5).map(([key, p]) => (
            <ProbBar key={key} label={PLATFORM_LABEL[key] ?? key} value={p} />
          ))}
        </div>
      </div>

      {contact.contributing_sensor_ids.length > 0 && (
        <div className="min-w-0">
          <div className="text-[10px] tracking-widest text-mute mb-1.5 font-mono">
            CONTRIBUTING SENSORS
          </div>
          <div className="text-[10px] font-mono text-blue space-y-0.5">
            {contact.contributing_sensor_ids.map((sid) => (
              <div key={sid} className="truncate">{sid}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ProbBar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="min-w-0">
      <div className="flex justify-between text-[10px] font-mono">
        <span className="text-fg truncate min-w-0">{label}</span>
        <span className="shrink-0 text-mute tabular-nums">{pct.toFixed(0)}%</span>
      </div>
      <div className="h-1 bg-line/60 rounded-sm overflow-hidden mt-0.5">
        <div
          className="h-full bg-amber/70"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
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
      {canCommand && <MissionPanel unit={unit} />}
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
        <SensorList unitId={unit.id} sensors={unit.sensors} canCommand={canCommand} />
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
    gun: 'GUN',
    missile: 'MISSILE',
    sam: 'SAM',
    kamikaze: 'OWA',
    bomb: 'BOMB',
  } as Record<string, string>)[k] ?? k.toUpperCase()
}

function weaponMeta(w: WeaponRef): string {
  const parts: string[] = [`r${w.range}`, `${w.damage}dmg`]
  if (w.ammo > 0) parts.push(`${w.ammo}rd`)
  if (w.ammo === -1) parts.push('∞')
  if (w.self_destruct) parts.push('SD')
  return parts.join(' · ')
}

function MissionPanel({ unit }: { unit: UnitInstance }) {
  const game = useStore((s) => s.game)
  const setMission = useStore((s) => s.setMission)
  const clearMission = useStore((s) => s.clearMission)
  const startTargeting = useStore((s) => s.startTargeting)
  const cancelTargeting = useStore((s) => s.cancelTargeting)
  const targeting = useStore((s) => s.targeting)
  const mission: Mission | undefined = game?.missions?.[unit.id]
  const inTargetingForThis =
    !!targeting && targeting.unitId === unit.id && targeting.kind === 'MISSION'

  const onPickTarget = () => {
    if (inTargetingForThis) cancelTargeting()
    else startTargeting(unit.id, 'MISSION')
  }
  const onSetRoe = (r: Mission['roe']) => {
    if (!mission) return
    // Surveil/avoid recon plays out across many turns — bump cap so the
    // run loop doesn't halt for max_turns when the player wanted "go and
    // see what's there." Engage missions stay short; they expect frequent
    // intervention.
    const max_turns = r === 'surveil' || r === 'avoid' ? 30 : 8
    void setMission({ ...mission, roe: r, max_turns })
  }
  const onClear = () => {
    void clearMission(unit.id)
  }

  const targetLabel = mission
    ? `(${mission.target_hex[0]}, ${mission.target_hex[1]})`
    : 'no target'

  return (
    <div className="border border-line rounded-sm p-2 space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] font-mono uppercase tracking-wider text-blue">MISSION</span>
        {mission && (
          <span className="text-[9px] font-mono text-mute">{mission.roe}</span>
        )}
      </div>
      <div className="flex items-center gap-2 text-[10px]">
        <button
          onClick={onPickTarget}
          className={`px-2 py-1 border rounded-sm font-mono uppercase tracking-wider transition ${
            inTargetingForThis
              ? 'border-amber text-amber bg-amber/10'
              : 'border-line text-mute hover:border-blue/60 hover:text-blue'
          }`}
        >
          {inTargetingForThis ? 'click hex…' : (mission ? 'change target' : 'set target')}
        </button>
        <span className="text-mute font-mono">{targetLabel}</span>
      </div>
      {mission && (
        <>
          <div className="flex gap-1 text-[9px] font-mono">
            {(['engage', 'surveil', 'avoid'] as const).map((opt) => (
              <button
                key={opt}
                onClick={() => onSetRoe(opt)}
                className={`flex-1 px-1.5 py-1 border rounded-sm uppercase tracking-wider transition ${
                  mission.roe === opt
                    ? 'border-amber text-amber bg-amber/10'
                    : 'border-line text-mute hover:text-fg'
                }`}
              >
                {opt}
              </button>
            ))}
          </div>
          <button
            onClick={onClear}
            className="w-full text-[10px] px-2 py-1 border border-line rounded-sm font-mono uppercase tracking-wider text-mute hover:border-red/60 hover:text-red transition"
          >
            clear mission
          </button>
        </>
      )}
    </div>
  )
}


function SensorList({
  unitId, sensors, canCommand,
}: {
  unitId: string
  sensors: SensorRef[]
  canCommand: boolean
}) {
  const toggleSensor = useStore((s) => s.toggleSensor)
  const [pendingKey, setPendingKey] = React.useState<string | null>(null)
  const onToggle = async (key: string, active: boolean) => {
    setPendingKey(key)
    try {
      await toggleSensor(unitId, key, active)
    } finally {
      setPendingKey(null)
    }
  }
  return (
    <div className="min-w-0">
      <div className="text-[10px] tracking-widest text-mute mb-1.5 font-mono">SENSORS</div>
      <div className="space-y-1">
        {sensors.map((s) => {
          const togglable = s.modality === 'radar'
          const on = s.is_active
          return (
            <div key={s.key} className="border border-line rounded-sm px-2 py-1 min-w-0">
              <div className="flex items-baseline justify-between gap-2 min-w-0">
                <span className="text-[11px] text-fg truncate min-w-0 flex-1">
                  {s.display}
                </span>
                <span
                  className={`shrink-0 text-[9px] font-mono uppercase tracking-wider ${SENSOR_BADGE[s.modality] ?? 'text-mute'}`}
                >
                  {s.modality}
                </span>
              </div>
              <div className="flex items-center justify-between mt-0.5 gap-2">
                <span className="text-[10px] font-mono text-mute">
                  range {s.range}{s.emits ? ' · emits' : ''}{s.los_required ? ' · LOS' : ''}
                </span>
                {togglable ? (
                  <button
                    disabled={!canCommand || pendingKey === s.key}
                    onClick={() => onToggle(s.key, !on)}
                    className={`shrink-0 text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm border transition ${
                      on
                        ? 'border-amber/60 text-amber bg-amber/10'
                        : 'border-line text-mute hover:border-blue/60 hover:text-blue'
                    } ${!canCommand ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
                    title={canCommand ? (on ? 'Click to turn OFF' : 'Click to turn ON') : 'Other side'}
                  >
                    {on ? 'ON' : 'OFF'}
                  </button>
                ) : (
                  <span className="shrink-0 text-[9px] font-mono uppercase tracking-wider text-mute">
                    PASSIVE
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
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
