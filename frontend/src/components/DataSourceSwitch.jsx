import { FlaskConical, Radio } from 'lucide-react'

/**
 * DATA SOURCE — the prominent, dashboard-level toggle the brief asks for.
 *
 * This does not itself start/stop the real-time feed (that is an explicit
 * owner action on the Real-Time screen with its own Start/Stop buttons); it
 * only navigates to the screen for the chosen source and reflects which mode
 * the backend is actually in right now (`status.data_source.mode`), read off
 * the live orchestrator/feed rather than local UI state.
 */
export default function DataSourceSwitch({ active, onSelect, className = '' }) {
  return (
    <div className={`grid grid-cols-2 gap-3 ${className}`}>
      <button
        type="button"
        onClick={() => onSelect('realtime')}
        className={[
          'flex items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors',
          active === 'realtime'
            ? 'border-agent/40 bg-agent/10'
            : 'border-white/10 bg-white/[0.02] hover:bg-white/[0.05]',
        ].join(' ')}
      >
        <Radio size={16} className={active === 'realtime' ? 'text-agent' : 'text-slate-400'} aria-hidden />
        <div className="min-w-0">
          <p className="text-[12.5px] font-semibold text-slate-100">Real-Time Data</p>
          <p className="truncate text-[11px] text-slate-500">Live prototype server / grid telemetry</p>
        </div>
      </button>
      <button
        type="button"
        onClick={() => onSelect('simulation')}
        className={[
          'flex items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors',
          active === 'simulation'
            ? 'border-agent/40 bg-agent/10'
            : 'border-white/10 bg-white/[0.02] hover:bg-white/[0.05]',
        ].join(' ')}
      >
        <FlaskConical size={16} className={active === 'simulation' ? 'text-agent' : 'text-slate-400'} aria-hidden />
        <div className="min-w-0">
          <p className="text-[12.5px] font-semibold text-slate-100">Simulated Data</p>
          <p className="truncate text-[11px] text-slate-500">Controlled scenario catalogue</p>
        </div>
      </button>
    </div>
  )
}
