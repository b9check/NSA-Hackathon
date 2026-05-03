// Action menu shown in the SELECTED UNIT panel when a friendly unit is
// selected. Six buttons map to the engine's Order kinds. MOVE / STRIKE /
// SCOUT / CAPTURE start a targeting flow (Phase 3.B picks up the click
// on the map). HOLD / OVERWATCH commit immediately.
import { useStore } from '../store'
import type { Order, UnitInstance } from '../types'

type Kind = 'MOVE' | 'STRIKE' | 'SCOUT' | 'OVERWATCH' | 'HOLD' | 'CAPTURE'

const KEY_HINTS: Record<Kind, string> = {
  MOVE: 'M',
  STRIKE: 'S',
  SCOUT: 'V',
  OVERWATCH: 'O',
  HOLD: 'H',
  CAPTURE: 'C',
}


function reachableFromCurrent(unit: UnitInstance): boolean {
  return unit.speed > 0
}


function inWeaponRange(unit: UnitInstance): boolean {
  return unit.weapon > 0
}


function adjacentObjective(
  unit: UnitInstance,
  game: import('../types').GameState,
): boolean {
  if (!(unit.domain === 'land' || unit.domain === 'amphib')) return false
  for (const o of game.map.objective_hexes) {
    const dq = Math.abs(o.col - unit.col)
    const dr = Math.abs(o.row - unit.row)
    if (dq <= 1 && dr <= 1) return true
  }
  return false
}


export function ActionMenu({ unit }: { unit: UnitInstance }) {
  const game = useStore((s) => s.game)
  const pending = useStore((s) => s.pendingOrders[unit.id])
  const targeting = useStore((s) => s.targeting)
  const setOrder = useStore((s) => s.setOrder)
  const clearOrder = useStore((s) => s.clearOrder)
  const startTargeting = useStore((s) => s.startTargeting)
  const cancelTargeting = useStore((s) => s.cancelTargeting)

  if (!game) return null

  const moveable = reachableFromCurrent(unit)
  const canStrike = inWeaponRange(unit)
  const canScout = unit.sensor > 0
  const canCapture = adjacentObjective(unit, game)

  const inTargetingForThis = !!targeting && targeting.unitId === unit.id

  const click = (kind: Kind) => {
    if (kind === 'HOLD' || kind === 'OVERWATCH') {
      const order: Order = { kind, unit_id: unit.id }
      setOrder(order)
      return
    }
    // MOVE / STRIKE / SCOUT / CAPTURE need a target; flip into targeting mode.
    if (inTargetingForThis && targeting?.kind === kind) {
      cancelTargeting()
    } else {
      startTargeting(unit.id, kind)
    }
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-1.5">
        <ActionBtn
          label="MOVE"
          hint={KEY_HINTS.MOVE}
          enabled={moveable}
          tip={moveable ? 'Pick destination on map' : 'Stationary platform'}
          active={inTargetingForThis && targeting?.kind === 'MOVE'}
          queued={pending?.kind === 'MOVE'}
          onClick={() => click('MOVE')}
        />
        <ActionBtn
          label="STRIKE"
          hint={KEY_HINTS.STRIKE}
          enabled={canStrike}
          tip={canStrike ? 'Pick visible target' : 'No weapons'}
          active={inTargetingForThis && targeting?.kind === 'STRIKE'}
          queued={pending?.kind === 'STRIKE'}
          onClick={() => click('STRIKE')}
        />
        <ActionBtn
          label="SCOUT"
          hint={KEY_HINTS.SCOUT}
          enabled={canScout}
          tip={canScout ? 'Pick area to scan' : 'No sensors'}
          active={inTargetingForThis && targeting?.kind === 'SCOUT'}
          queued={pending?.kind === 'SCOUT'}
          onClick={() => click('SCOUT')}
        />
        <ActionBtn
          label="OVERWATCH"
          hint={KEY_HINTS.OVERWATCH}
          enabled={canStrike}
          tip={canStrike ? 'Auto-fire on movers in range' : 'No weapons'}
          queued={pending?.kind === 'OVERWATCH'}
          onClick={() => click('OVERWATCH')}
        />
        <ActionBtn
          label="HOLD"
          hint={KEY_HINTS.HOLD}
          enabled={true}
          tip="Stand fast"
          queued={pending?.kind === 'HOLD'}
          onClick={() => click('HOLD')}
        />
        <ActionBtn
          label="CAPTURE"
          hint={KEY_HINTS.CAPTURE}
          enabled={canCapture}
          tip={canCapture ? 'Capture adjacent objective' : 'Not near an objective'}
          active={inTargetingForThis && targeting?.kind === 'CAPTURE'}
          queued={pending?.kind === 'CAPTURE'}
          onClick={() => click('CAPTURE')}
        />
      </div>

      <div className="flex items-center justify-between text-[10px] font-mono">
        <span className="text-mute">
          {pending
            ? <>order: <span className="text-amber">{describeOrder(pending)}</span></>
            : <>no order queued</>}
        </span>
        {pending && (
          <button
            onClick={() => clearOrder(unit.id)}
            className="text-mute hover:text-fg underline underline-offset-2"
          >
            clear
          </button>
        )}
      </div>

      {inTargetingForThis && (
        <div className="text-[10px] font-mono text-amber border border-amber/40 rounded-sm px-2 py-1">
          targeting {targeting?.kind} — pick a hex on the map (Esc to cancel)
        </div>
      )}
    </div>
  )
}


function describeOrder(o: Order): string {
  switch (o.kind) {
    case 'MOVE':
    case 'SCOUT':
    case 'CAPTURE':
      return `${o.kind} (${o.target_hex[0]},${o.target_hex[1]})`
    case 'STRIKE':
      return `STRIKE → ${o.target_id}`
    default:
      return o.kind
  }
}


function ActionBtn({
  label, hint, enabled, tip, active, queued, onClick,
}: {
  label: string
  hint: string
  enabled: boolean
  tip: string
  active?: boolean
  queued?: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={enabled ? onClick : undefined}
      disabled={!enabled}
      title={tip}
      className={[
        'h-9 px-2 flex items-center justify-between rounded-sm border',
        'font-mono text-[11px] tracking-wider transition-colors',
        active
          ? 'border-amber text-amber bg-amber/10'
          : queued
          ? 'border-blue text-blue bg-blue/10'
          : enabled
          ? 'border-line text-fg hover:bg-panel2 hover:border-fg/40'
          : 'border-line/40 text-mute opacity-50 cursor-not-allowed',
      ].join(' ')}
    >
      <span>{label}</span>
      <span className="text-[9px] opacity-60">{hint}</span>
    </button>
  )
}
