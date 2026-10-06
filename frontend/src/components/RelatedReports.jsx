import { ChevronRight, MessageSquareText, Send } from 'lucide-react'
import { Panel, Spinner } from './Primitives'
import { ReportStatusBadge, hasOwnerReply } from './ReportParts'
import { clientReports, fmt } from '../data/constants'

/**
 * "2 Client Reports" — the reports that point at an incident or a device,
 * shown where that incident or device is being looked at. Each row opens the
 * report on the Reports screen.
 *
 * `reports` is null while loading. A client is only ever sent their own.
 */
export default function RelatedReports({
  id,
  title = 'Client reports',
  reports,
  onOpenReport,
  onReportIssue,
  reportLabel = 'Report an issue',
  emptyText = 'No client reports are linked to this yet.',
}) {
  const count = reports?.length ?? 0

  return (
    <Panel
      title={title}
      icon={MessageSquareText}
      meta={reports ? clientReports(count) : null}
      className="scroll-mt-20"
    >
      <div id={id}>
        {reports === null && <Spinner label="Loading reports" />}

        {reports && count === 0 && <p className="text-[13px] text-slate-500">{emptyText}</p>}

        {count > 0 && (
          <ul className="space-y-2">
            {reports.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => onOpenReport?.(r.id)}
                  className="glass-quiet flex w-full items-start gap-3 px-3.5 py-3 text-left transition-colors hover:border-white/20"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                      <span className="num text-[12px] font-semibold text-slate-100">{r.id}</span>
                      <ReportStatusBadge status={r.status} />
                      {hasOwnerReply(r) && <span className="text-[11px] text-agent">owner replied</span>}
                    </div>
                    <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-slate-300">{r.message}</p>
                    <p className="mt-1 text-[11px] text-slate-500">
                      {r.client} · {fmt.datetime(r.created_at)}
                    </p>
                  </div>
                  <ChevronRight size={14} className="mt-1 shrink-0 text-slate-500" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}

        {onReportIssue && (
          <button
            type="button"
            onClick={onReportIssue}
            className="mt-3 flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium text-agent ring-1 ring-agent/25 hover:bg-agent/10"
          >
            <Send size={12} aria-hidden />
            {reportLabel}
          </button>
        )}
      </div>
    </Panel>
  )
}
