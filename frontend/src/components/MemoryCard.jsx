import { Badge } from './Primitives'
import { fmt } from '../data/constants'

/**
 * One record from incident memory D. When it arrived via retrieval it carries a
 * cosine similarity, which is what justifies the agent leaning on it.
 */
export default function MemoryCard({ record, compact = false }) {
  return (
    <div className="glass-quiet px-3.5 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="num text-[12px] font-semibold text-slate-200">{record.record_id}</p>
          <p className="mt-0.5 truncate text-[11px] text-slate-500">{record.fault_label}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {record.similarity !== undefined && (
            <Badge tone="policy">sim {fmt.num(record.similarity)}</Badge>
          )}
          <Badge tone={record.success ? 'pass' : 'hold'}>
            {record.success ? 'resolved' : 'unresolved'}
          </Badge>
        </div>
      </div>

      {!compact && (
        <p className="mt-2 text-[12px] leading-relaxed text-slate-400">{record.diagnosis}</p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span className="num">{record.action_label}</span>
        <span className="num">{record.status}</span>
        <span className="num">{fmt.num(record.resolution_s, 1)} s</span>
      </div>
    </div>
  )
}
