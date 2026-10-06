import { Activity, Check, Cpu, MessageSquareText } from 'lucide-react'
import { Badge } from './Primitives'
import { REPORT_STEPS, estate, fmt, reportStatus } from '../data/constants'

/** SENT / ACKNOWLEDGED / RESOLVED as a badge. */
export function ReportStatusBadge({ status }) {
  const st = reportStatus(status)
  return (
    <Badge tone={st.tone}>
      <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} aria-hidden />
      {st.label}
    </Badge>
  )
}

/** Small pill for a linked device / incident / owner reply. */
export function Chip({ icon: Icon, children, tone = 'neutral', mono = true }) {
  const tones = {
    neutral: 'bg-white/[0.04] text-slate-300 ring-white/10',
    agent: 'bg-agent/10 text-agent ring-agent/25',
  }
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] ring-1 ${tones[tone]}`}
    >
      {Icon && <Icon size={11} className="shrink-0" aria-hidden />}
      <span className={`${mono ? 'num ' : ''}truncate`}>{children}</span>
    </span>
  )
}

/**
 * Sent -> Acknowledged -> Resolved, with the time each step happened.
 * A step that has not happened yet says so instead of showing an empty slot.
 */
export function ReportProgress({ report }) {
  const at = {
    SENT: report.created_at,
    ACKNOWLEDGED: report.acknowledged_at,
    RESOLVED: report.resolved_at,
  }
  const reached = (key) => at[key] !== null && at[key] !== undefined

  return (
    <ol className="grid grid-cols-3 gap-3" aria-label="Report progress">
      {REPORT_STEPS.map((key, i) => {
        const st = reportStatus(key)
        const done = reached(key)
        return (
          <li key={key} className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={[
                  'grid h-5 w-5 shrink-0 place-items-center rounded-full ring-1',
                  done ? st.done : 'bg-white/[0.03] text-slate-600 ring-white/10',
                ].join(' ')}
              >
                {done ? (
                  <Check size={11} aria-hidden />
                ) : (
                  <span className="h-1 w-1 rounded-full bg-slate-600" aria-hidden />
                )}
              </span>
              <span className={`text-[12px] font-medium ${done ? 'text-slate-100' : 'text-slate-500'}`}>
                {st.label}
              </span>
              {i < REPORT_STEPS.length - 1 && (
                <span
                  className={`h-px min-w-3 flex-1 ${reached(REPORT_STEPS[i + 1]) ? 'bg-agent/40' : 'bg-white/10'}`}
                  aria-hidden
                />
              )}
            </div>
            <p className="mt-1 pl-7 text-[11px] leading-snug text-slate-500">
              {done ? <span className="num">{fmt.datetime(at[key])}</span> : st.waiting}
            </p>
          </li>
        )
      })}
    </ol>
  )
}

/** True once the owner has written anything back to the client. */
export const hasOwnerReply = (report) => Boolean(report.response_note || report.resolution_note)

/** One report in a list. */
export function ReportRow({ report, active, onOpen, showClient = false }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(report.id)}
      aria-current={active ? 'true' : undefined}
      className={[
        'glass w-full p-4 text-left transition-colors',
        active ? 'border-agent/40 bg-agent/[0.06]' : 'hover:border-white/20',
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="num text-[13px] font-semibold tracking-wide text-slate-100">{report.id}</p>
          <p className="mt-0.5 truncate text-[11.5px] text-slate-500">
            {fmt.datetime(report.created_at)}
            {showClient && <> · {report.customer ? `${report.client} (${report.customer})` : report.client}</>}
          </p>
        </div>
        <ReportStatusBadge status={report.status} />
      </div>

      <p className="mt-2.5 line-clamp-2 text-[13px] leading-relaxed text-slate-300">{report.message}</p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {report.device && (
          <Chip icon={Cpu}>{estate.device(report.device.context, report.device.euicc_id)}</Chip>
        )}
        {report.incident && <Chip icon={Activity}>{report.incident.incident_id}</Chip>}
        {!report.device && !report.incident && (
          <span className="text-[11px] text-slate-600">Not linked to a device or incident</span>
        )}
        {hasOwnerReply(report) && (
          <Chip icon={MessageSquareText} tone="agent" mono={false}>
            Owner replied
          </Chip>
        )}
      </div>
    </button>
  )
}
