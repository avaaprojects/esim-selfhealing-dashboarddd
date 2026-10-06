import { Meter } from './Primitives'
import { fmt } from '../data/constants'

/**
 * A single PPO-Lagrangian readout. `limit` is the constraint bound d_i, drawn
 * on the meter so a binding constraint is visible rather than implied.
 */
export default function LearningCard({ label, value, hint, meter, tone = 'policy' }) {
  return (
    <div className="glass-quiet px-4 py-3.5">
      <p className="label">{label}</p>
      <p className="num mt-1 text-2xl font-semibold text-slate-100">{value}</p>
      {meter && <Meter className="mt-2.5" {...meter} tone={tone} />}
      {hint && <p className="mt-2 text-[11px] leading-snug text-slate-500">{hint}</p>}
    </div>
  )
}
