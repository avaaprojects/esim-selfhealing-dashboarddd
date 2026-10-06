import { FLOW_STATE } from '../../data/constants'

const SEVERITY = {
  low: 'bg-white/5 text-slate-300 ring-white/10',
  medium: 'bg-hold/10 text-hold ring-hold/30',
  high: 'bg-block/10 text-block ring-block/30',
  critical: 'bg-block text-slate-950 ring-block',
}

/** low / medium / high / critical. The working is shown next to it, never hidden. */
export function SeverityBadge({ level }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ${SEVERITY[level] ?? SEVERITY.low}`}
    >
      {level.charAt(0).toUpperCase() + level.slice(1)} severity
    </span>
  )
}

const TONE_FRAME = {
  pass: 'border-pass/40 bg-pass/[0.04]',
  hold: 'border-hold/40 bg-hold/[0.04]',
  block: 'border-block/40 bg-block/[0.04]',
  agent: 'border-agent/40 bg-agent/[0.04]',
  neutral: 'border-white/15 bg-white/[0.02]',
}

/**
 * One stage of INPUT -> ... -> RECOVERY. The number is real: the stages are a
 * sequence. `frame` gives the safety gate its own bordered, tinted enclosure so
 * it reads as a checkpoint between deciding and doing, not another card.
 */
export function Stage({ id, index, title, state = 'done', headline, frame = null, children }) {
  const st = FLOW_STATE[state] ?? FLOW_STATE.done
  const body = (
    <>
      <header className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-[12px] font-semibold ring-1 ${st.bg} ${st.text} ${st.ring}`}>
          {index}
        </span>
        <h2 className="text-[16px] font-semibold text-slate-50">{title}</h2>
        {headline && <p className="min-w-0 flex-1 truncate text-[13px] text-slate-400">{headline}</p>}
        <span className={`flex shrink-0 items-center gap-1.5 text-[12px] ${st.text}`}>
          <span className={`h-2 w-2 rounded-full ${st.dot}`} aria-hidden />
          {st.label}
        </span>
      </header>
      {children}
    </>
  )
  return (
    <section id={`stage-${id}`} className="scroll-mt-36" aria-labelledby={`stage-${id}-title`}>
      {frame ? (
        <div className={`rounded-2xl border-2 p-5 ${TONE_FRAME[frame] ?? TONE_FRAME.neutral}`}>{body}</div>
      ) : (
        <div>{body}</div>
      )}
    </section>
  )
}
