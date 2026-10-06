import { Ban, Crosshair } from 'lucide-react'
import { Badge, Meter } from './Primitives'
import { fmt } from '../data/constants'

/**
 * One ranked candidate from PLAN. The selected action is the visually
 * prominent one; infeasible candidates stay visible but recede, and name the
 * constraint they violated rather than just disappearing.
 */
export default function ActionCard({ candidate: c }) {
  const selected = c.selected
  const blocked = !c.feasible

  return (
    <div
      className={[
        'rounded-xl border px-4 py-3.5 transition-colors',
        selected
          ? 'border-agent/50 bg-agent/[0.08] shadow-glow'
          : blocked
            ? 'border-block/20 bg-block/[0.04]'
            : 'border-white/[0.08] bg-white/[0.02]',
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={`text-[13.5px] font-semibold ${selected ? 'text-agent' : 'text-slate-100'}`}>
            {c.label}
          </p>
          <p className="mt-0.5 text-[11px] text-slate-500">proposed by {c.proposer}</p>
        </div>
        {selected && (
          <Badge tone="agent">
            <Crosshair size={11} aria-hidden />
            Selected action
          </Badge>
        )}
        {blocked && (
          <Badge tone="block">
            <Ban size={11} aria-hidden />
            Filtered out
          </Badge>
        )}
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-4">
        <Stat label="Predicted success" value={fmt.pct(c.p_success)} meter={{ value: c.p_success, max: 1 }} tone="pass" />
        <Stat label="Risk" value={fmt.num(c.risk)} meter={{ value: c.risk, max: 1, limit: 0.3 }} tone={c.risk > 0.3 ? 'block' : 'hold'} />
        <Stat label="Blast radius" value={c.blast_radius} meter={{ value: Math.min(c.blast_radius, 4), max: 4, limit: 1 }} tone={c.blast_radius > 1 ? 'block' : 'agent'} />
        <Stat label="Utility U(a)" value={fmt.num(c.utility, 3)} tone="agent" />
      </dl>

      {blocked && c.violated?.length > 0 && (
        <ul className="num mt-3 space-y-1 border-t border-white/[0.06] pt-2.5 text-[11px] text-block/90">
          {c.violated.map((v) => (
            <li key={v}>{v}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Stat({ label, value, meter, tone = 'agent' }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="num mt-0.5 text-[13px] text-slate-100">{value}</dd>
      {meter && <Meter className="mt-1.5" {...meter} tone={tone} />}
    </div>
  )
}
