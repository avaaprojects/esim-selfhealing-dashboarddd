import { Meter } from './Primitives'

/** Compact number tile. `limit` renders the constraint bound on the meter. */
export default function MetricCard({ label, value, unit, hint, meter, tone = 'agent' }) {
  return (
    <div className="glass-quiet px-3.5 py-3">
      <p className="label">{label}</p>
      <p className="num mt-1 text-xl font-semibold text-slate-100">
        {value}
        {unit && <span className="ml-1 text-[11px] font-normal text-slate-500">{unit}</span>}
      </p>
      {meter && (
        <Meter className="mt-2" value={meter.value} max={meter.max} limit={meter.limit} tone={tone} />
      )}
      {hint && <p className="mt-1.5 text-[11px] leading-snug text-slate-500">{hint}</p>}
    </div>
  )
}
