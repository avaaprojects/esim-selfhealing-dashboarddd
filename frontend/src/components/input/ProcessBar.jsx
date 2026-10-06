import { AlertTriangle, Check, Loader2, PlayCircle, X } from 'lucide-react'
import { TypeBadge } from './parts'
import { fmt } from '../../data/constants'

/**
 * PROCESS DATA. Sticks to the bottom of the screen so it is always in reach.
 * Blocking checks must pass; advisory ones (a stopped real-time feed, no
 * uploads) are shown but do not stop it. Pressing it freezes the current input
 * as a snapshot that the output stage will read.
 */
export default function ProcessBar({ view, commit, processing, message, onProcess, onOpenOutput }) {
  const checks = view?.readiness?.checks ?? []
  const ready = Boolean(view?.readiness?.ready)
  const shown = checks.filter((c) => c.key !== 'attachments')

  return (
    <div className="sticky bottom-0 z-10 -mx-4 border-t border-white/[0.09] bg-void/90 px-4 py-3 backdrop-blur-xl sm:-mx-6 sm:px-6">
      {commit && (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-pass/30 bg-pass/[0.06] px-3.5 py-2.5 text-[12.5px]">
          <span className="flex items-center gap-1.5 font-medium text-pass">
            <Check size={14} aria-hidden /> Last processed as <span className="num">{commit.id}</span>
          </span>
          <span className="text-slate-300">
            {commit.resolved?.client?.name} · {commit.resolved?.device?.device_label}
          </span>
          <TypeBadge typeKey={commit.source?.mode} label={commit.source?.type} />
          <span className="text-slate-500">
            {commit.attachments?.length ?? 0} files · {fmt.datetime(commit.created_at)}
          </span>
          {onOpenOutput && (
            <button type="button" onClick={onOpenOutput} className="ml-auto text-agent hover:underline">
              View output
            </button>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <ul className="flex min-w-0 flex-1 flex-wrap gap-x-4 gap-y-1.5" aria-label="Readiness">
          {shown.map((c) => (
            <li
              key={c.key}
              title={c.detail}
              className={`flex items-center gap-1.5 text-[12px] ${c.ok ? 'text-slate-400' : c.blocking ? 'text-block' : 'text-hold'}`}
            >
              {c.ok ? <Check size={12} className="text-pass" aria-hidden /> : c.blocking ? <X size={12} aria-hidden /> : <AlertTriangle size={12} aria-hidden />}
              {c.label}
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={onProcess}
          disabled={!ready || processing}
          className="flex shrink-0 items-center gap-2.5 rounded-xl bg-gradient-to-r from-agent to-cyan-300 px-5 py-2.5 text-[13px] font-semibold text-slate-950 shadow-glow transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
        >
          {processing ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <PlayCircle size={15} aria-hidden />}
          Process data
        </button>
      </div>
      {message && (
        <p role="alert" className="mt-2 text-[12.5px] text-block">
          {message}
        </p>
      )}
      {!ready && <p className="mt-1.5 text-[11.5px] text-slate-500">Process data unlocks when every check marked with a cross is done.</p>}
    </div>
  )
}
