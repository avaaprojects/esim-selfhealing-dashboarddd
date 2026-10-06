import { ChevronRight } from 'lucide-react'
import { FLOW_STATE } from '../../data/constants'

/**
 * INPUT -> PROCESSING -> INCIDENT -> SELF-HEALING -> SAFETY GATE -> REMEDIATION -> RECOVERY
 *
 * Each node's state and one-line headline come from the backend's own read of
 * the run (`view.flow`), so a blocked gate or a failed recovery is visible in
 * the strip before you scroll to the section. Clicking a node jumps to it.
 */
export default function FlowStepper({ flow = [] }) {
  const go = (key) => (e) => {
    e.preventDefault()
    document.getElementById(`stage-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  return (
    <nav aria-label="Self-healing flow" className="overflow-x-auto">
      <ol className="flex min-w-max items-stretch gap-1 py-2.5">
        {flow.map((f, i) => {
          const st = FLOW_STATE[f.state] ?? FLOW_STATE.pending
          return (
            <li key={f.key} className="flex items-center gap-1">
              <a
                href={`#stage-${f.key}`}
                onClick={go(f.key)}
                title={`${f.label}: ${f.headline}${f.detail ? `. ${f.detail}` : ''}`}
                className={`flex w-[148px] items-start gap-2 rounded-xl px-2.5 py-2 ring-1 transition-colors hover:brightness-125 ${st.bg} ${st.ring}`}
              >
                <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${st.dot}`} aria-hidden />
                <span className="min-w-0">
                  <span className={`block text-[12px] font-semibold ${st.text}`}>{f.label}</span>
                  <span className="mt-0.5 block truncate text-[11px] text-slate-400">{f.headline}</span>
                </span>
              </a>
              {i < flow.length - 1 && <ChevronRight size={14} className="shrink-0 text-slate-600" aria-hidden />}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
