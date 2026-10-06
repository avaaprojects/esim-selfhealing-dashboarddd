import { Building2 } from 'lucide-react'
import { Badge } from './Primitives'

/**
 * WHOLE ESTATE — read straight off `AgentSession._estate_summary`, which in
 * turn reads the RSP client's registered fleet size and the records this run
 * actually produced. Shown whenever a run touches more than one device
 * (`shared_fleet`, `night_shift`), so a multi-device scenario reads as one
 * estate the agent reasoned over holistically, not as isolated rows.
 */
export default function EstateSummary({ estate }) {
  if (!estate) return null
  const degraded = estate.overall_state === 'DEGRADED'

  return (
    <div className="glass-quiet p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Building2 size={14} className="text-agent" aria-hidden />
          <p className="text-[12.5px] font-semibold text-slate-100">Whole estate</p>
        </div>
        <Badge tone={degraded ? 'hold' : 'pass'}>{estate.overall_state}</Badge>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Cell label="Devices monitored" value={estate.devices_monitored} />
        <Cell label="Telemetry samples" value={estate.telemetry_samples} />
        <Cell label="Incidents" value={estate.incidents_total} />
        <Cell label="Active incidents" value={estate.active_incidents} tone={estate.active_incidents ? 'text-hold' : 'text-pass'} />
        <Cell label="Affected profiles" value={estate.affected_profiles} />
        <Cell label="Blast radius" value={estate.blast_radius} />
      </dl>
    </div>
  )
}

function Cell({ label, value, tone = 'text-slate-100' }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className={`num mt-0.5 text-[15px] font-semibold ${tone}`}>{value}</dd>
    </div>
  )
}
