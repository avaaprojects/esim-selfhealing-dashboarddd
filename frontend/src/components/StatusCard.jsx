import { PANEL_STATE_STYLE } from '../data/constants'

function fixed(v, p = 3) {
  return typeof v === 'number' ? v.toFixed(p) : '—'
}

/**
 * One channel's live value inside its own nominal range.
 *
 * The bar is positioned within [low, high], which the backend recomputes
 * every sample from the detector's EWMA baseline and band and clamps to the
 * channel's physical limits. So the range moves with the device: a gateway
 * under tree cover and a unit roaming between cells have genuinely
 * different idea of normal, and a fixed cut-off would call one of them
 * faulty for behaving the way it always behaves there.
 */
function Range({ r }) {
  const span = (r.high ?? 0) - (r.low ?? 0)
  const pct = span > 0 ? ((r.value - r.low) / span) * 100 : 50
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <div className="mt-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-[10.5px] text-slate-500">{r.label}</span>
        <span className={`num shrink-0 text-[10.5px] ${r.in_range ? 'text-slate-300' : 'text-rose-400'}`}>
          {fixed(r.value, r.precision)}
        </span>
      </div>
      <div className="relative mt-1 h-1 rounded-full bg-white/[0.06]">
        <span
          className={`absolute top-1/2 h-2 w-0.5 -translate-y-1/2 rounded-full ${
            r.in_range ? 'bg-emerald-400' : 'bg-rose-400'
          }`}
          style={{ left: `${clamped}%` }}
          aria-hidden
        />
      </div>
      <p className="num mt-0.5 text-[10px] text-slate-600">
        nominal {fixed(r.low, r.precision)} – {fixed(r.high, r.precision)}
      </p>
    </div>
  )
}

/**
 * One row of the left-hand system column. `state` comes from the backend's
 * status panels, which derive it from live RSP/monitor/signer objects, and
 * `ranges` from the detector's own baseline and band at the latest sample.
 */
export default function StatusCard({ label, state, value, detail, ranges }) {
  const tone = PANEL_STATE_STYLE[state] ?? 'text-slate-400'
  return (
    <div className="glass-quiet flex items-start gap-3 px-3.5 py-3">
      <span
        className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${tone.replace('text-', 'bg-')}`}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-[13px] font-medium text-slate-200">{label}</p>
          <p className={`num shrink-0 text-[11px] font-semibold ${tone}`}>{state}</p>
        </div>
        <p className="num mt-0.5 truncate text-[12px] text-slate-400">{value}</p>
        {detail && <p className="mt-0.5 truncate text-[11px] text-slate-500" title={detail}>{detail}</p>}
        {(ranges ?? []).map((r) => (
          <Range key={r.key} r={r} />
        ))}
      </div>
    </div>
  )
}
