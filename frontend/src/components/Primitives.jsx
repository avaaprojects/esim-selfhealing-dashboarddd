import { AlertCircle, Loader2 } from 'lucide-react'

/** The single surface treatment. Everything in the app sits on one of these. */
export function Panel({ title, meta, icon: Icon, children, className = '', dense = false }) {
  return (
    <section className={`glass ${dense ? 'p-4' : 'p-5'} ${className}`}>
      {(title || meta) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            {Icon && <Icon size={15} className="shrink-0 text-agent" aria-hidden />}
            <h2 className="text-[13px] font-semibold text-slate-100">{title}</h2>
          </div>
          {meta && <div className="label shrink-0 text-right">{meta}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Badge({ children, tone = 'neutral', className = '' }) {
  const tones = {
    neutral: 'bg-white/5 text-slate-300 ring-white/10',
    agent: 'bg-agent/10 text-agent ring-agent/25',
    policy: 'bg-policy/10 text-policy ring-policy/25',
    pass: 'bg-pass/10 text-pass ring-pass/25',
    hold: 'bg-hold/10 text-hold ring-hold/25',
    block: 'bg-block/10 text-block ring-block/25',
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ${tones[tone]} ${className}`}
    >
      {children}
    </span>
  )
}

/** Horizontal meter. `limit` draws the constraint bound as a tick. */
export function Meter({ value = 0, max = 1, limit = null, tone = 'agent', className = '' }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100))
  const limitPct = limit === null ? null : Math.max(0, Math.min(100, (limit / (max || 1)) * 100))
  const fills = {
    agent: 'bg-agent',
    policy: 'bg-policy',
    pass: 'bg-pass',
    hold: 'bg-hold',
    block: 'bg-block',
  }
  return (
    <div className={`relative h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07] ${className}`}>
      <div
        className={`h-full rounded-full transition-[width] duration-500 ${fills[tone]}`}
        style={{ width: `${pct}%` }}
      />
      {limitPct !== null && (
        <div
          className="absolute inset-y-0 w-px bg-white/55"
          style={{ left: `${limitPct}%` }}
          title={`limit ${limit}`}
        />
      )}
    </div>
  )
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-slate-400">
      <Loader2 size={15} className="animate-spin" aria-hidden />
      {label}
    </div>
  )
}

/** Empty and error states say what happened and what to do next. */
export function Notice({ title, children, tone = 'neutral', action }) {
  const border = tone === 'block' ? 'border-block/30' : 'border-white/10'
  return (
    <div className={`glass-quiet border ${border} p-5`}>
      <div className="flex items-start gap-2.5">
        {tone === 'block' && <AlertCircle size={15} className="mt-0.5 shrink-0 text-block" aria-hidden />}
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-100">{title}</p>
          {children && <div className="mt-1.5 text-[13px] leading-relaxed text-slate-400">{children}</div>}
          {action && <div className="mt-3">{action}</div>}
        </div>
      </div>
    </div>
  )
}

/** Label/value row used throughout the detail panes. */
export function Field({ label, value, mono = true, tone = '' }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="label">{label}</span>
      <span className={`text-[13px] ${mono ? 'num' : ''} ${tone || 'text-slate-200'}`}>{value}</span>
    </div>
  )
}
