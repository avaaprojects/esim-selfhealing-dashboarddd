import { ChevronRight } from 'lucide-react'
import { Badge, Meter } from './Primitives'
import { FAULT_COLOR, clientReports, estate, fmt, statusStyle } from '../data/constants'

/** Belief distribution b_t as a stacked bar plus a legend. */
export function BeliefBar({ belief = [], compact = false }) {
  const shown = belief.filter((b) => (b.p ?? 0) > 0.001)
  return (
    <div>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
        {shown.map((b) => (
          <div
            key={b.fault_class}
            style={{ width: `${(b.p ?? 0) * 100}%`, background: FAULT_COLOR[b.fault_class] ?? '#64748B' }}
            title={`${b.label} ${fmt.pct(b.p, 1)}`}
          />
        ))}
      </div>
      {!compact && (
        <ul className="mt-2.5 space-y-1">
          {shown.slice(0, 4).map((b) => (
            <li key={b.fault_class} className="flex items-center justify-between gap-3 text-[12px]">
              <span className="flex min-w-0 items-center gap-2">
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: FAULT_COLOR[b.fault_class] ?? '#64748B' }}
                  aria-hidden
                />
                <span className="truncate text-slate-300">{b.label}</span>
              </span>
              <span className="num shrink-0 text-slate-400">{fmt.pct(b.p, 1)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function IncidentCard({ incident, onOpen, active = false, onOpenReports }) {
  const st = statusStyle(incident.status)
  const exceed = incident.exceedance ?? 1

  const reportCount = incident.report_count ?? 0

  return (
    <div
      className={[
        'glass w-full transition-colors',
        active ? 'border-agent/40 bg-agent/[0.06]' : 'hover:border-white/20',
      ].join(' ')}
    >
    <button
      type="button"
      onClick={() => onOpen?.(incident.incident_id)}
      className="w-full rounded-2xl p-4 text-left"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="num text-[13px] font-semibold tracking-wide text-slate-100">
            {incident.incident_id}
          </p>
          <p className="mt-0.5 truncate text-[12px] text-slate-400">
            {incident.fault_label} · {estate.device(incident.context, incident.observation?.euicc_id)}
          </p>
          {estate.client(incident.context) && (
            <p className="truncate text-[11px] text-slate-500">{estate.client(incident.context)}</p>
          )}
        </div>
        <Badge
          tone={
            incident.status === 'AUTO-REMEDIATED'
              ? 'pass'
              : incident.status === 'HUMAN-IN-LOOP'
                ? 'hold'
                : 'block'
          }
        >
          <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} aria-hidden />
          {st.label}
        </Badge>
      </div>

      <dl className="mt-3.5 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <div>
          <dt className="label">Anomaly score</dt>
          <dd className="num text-[13px] text-slate-100">{fmt.num(incident.anomaly_score)}</dd>
        </div>
        <div>
          <dt className="label">Threshold</dt>
          <dd className="num text-[13px] text-slate-400">{fmt.num(incident.threshold)}</dd>
        </div>
        <div>
          <dt className="label">Confidence</dt>
          <dd className="num text-[13px] text-slate-100">{fmt.pct(incident.confidence)}</dd>
        </div>
        <div>
          <dt className="label">Loop time</dt>
          <dd className="num text-[13px] text-slate-100">{fmt.ms(incident.total_latency_ms)}</dd>
        </div>
      </dl>

      <Meter
        className="mt-3"
        value={Math.min(exceed, 3)}
        max={3}
        limit={1}
        tone={exceed > 1 ? 'block' : 'pass'}
      />
      <p className="mt-1.5 text-[11px] text-slate-500">
        g_t sits {fmt.num(exceed, 2)}× the chi-square threshold
      </p>

      <div className="mt-3">
        <BeliefBar belief={incident.belief} compact />
      </div>

      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="truncate text-[12px] text-slate-400">
          {incident.selected_label ?? 'No feasible action'}
          {incident.fleet_size > 1 && (
            <span className="text-slate-500"> · fleet of {incident.fleet_size}</span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-1 text-[12px] text-agent">
          Open trace
          <ChevronRight size={13} aria-hidden />
        </span>
      </div>
    </button>
    {reportCount > 0 && (
      <button
        type="button"
        onClick={() => onOpenReports?.(incident.incident_id)}
        className="flex w-full items-center justify-between gap-2 border-t border-white/[0.07] px-4 py-2.5 text-left text-[12px] font-medium text-agent hover:bg-agent/[0.06]"
      >
        {clientReports(reportCount)}
        <ChevronRight size={13} aria-hidden />
      </button>
    )}
    </div>
  )
}
