import { STAGES, fmt } from '../data/constants'

/**
 * OBSERVE → REASON → PLAN → SAFETY → ACT → VERIFY → LEARN.
 *
 * `latencies` is the real per-stage profile from the orchestrator's last loop
 * record, so once a run completes each row carries the measured time rather
 * than a decorative tick.
 */
export default function AgentPipeline({ stage, latencies = {}, orientation = 'vertical' }) {
  const latencyKey = {
    observe: 'monitor',
    reason: 'reason',
    plan: 'plan',
    safety: 'act_gate',
    act: 'act_dispatch',
    verify: null,
    learn: 'learn',
  }
  const activeIndex = STAGES.findIndex((s) => s.key === stage)
  const horizontal = orientation === 'horizontal'

  return (
    <ol className={horizontal ? 'flex flex-wrap items-stretch gap-2' : 'space-y-1'}>
      {STAGES.map((s, i) => {
        const isActive = s.key === stage
        const isPast = activeIndex > -1 && i < activeIndex
        const ms = latencyKey[s.key] ? latencies[latencyKey[s.key]] : null

        return (
          <li key={s.key} className={horizontal ? 'flex-1 min-w-[108px]' : ''}>
            <div
              className={[
                'flex items-center gap-3 rounded-xl border px-3 py-2 transition-all duration-300',
                isActive
                  ? 'border-agent/45 bg-agent/10 shadow-glow'
                  : isPast
                    ? 'border-pass/25 bg-pass/[0.06]'
                    : 'border-white/[0.07] bg-white/[0.02]',
              ].join(' ')}
            >
              <span
                className={[
                  'h-2 w-2 shrink-0 rounded-full transition-colors',
                  isActive ? 'bg-agent animate-corepulse' : isPast ? 'bg-pass' : 'bg-slate-600',
                ].join(' ')}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p
                  className={[
                    'num text-[11px] font-semibold tracking-wider',
                    isActive ? 'text-agent' : isPast ? 'text-pass' : 'text-slate-400',
                  ].join(' ')}
                >
                  {s.label}
                </p>
                {!horizontal && (
                  <p className="truncate text-[11px] text-slate-500">{s.blurb}</p>
                )}
              </div>
              {ms !== null && ms !== undefined && (
                <span className="num shrink-0 text-[11px] text-slate-500">{fmt.ms(ms)}</span>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
