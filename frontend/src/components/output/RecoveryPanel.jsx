import { Check, Minus, X } from 'lucide-react'
import { Panel } from '../Primitives'
import { fmt } from '../../data/constants'

const BANNER = {
  pass: 'border-pass/40 bg-pass/[0.07] text-pass',
  hold: 'border-hold/40 bg-hold/[0.07] text-hold',
  block: 'border-block/40 bg-block/[0.07] text-block',
  agent: 'border-agent/40 bg-agent/[0.07] text-agent',
}

/**
 * Stage 7: did the system return to the expected state? The verdict is
 * backed by a before / after table read from the eUICC state immediately
 * around the action, never inferred from the action's success flag alone.
 */
export default function RecoveryPanel({ view }) {
  const rec = view.recovery
  const tele = rec.telemetry
  return (
    <div className="space-y-4">
      <div className={`rounded-2xl border-2 px-5 py-4 ${BANNER[rec.tone] ?? BANNER.agent}`}>
        <p className="text-[18px] font-semibold">{rec.headline}</p>
        <p className="mt-1 max-w-[80ch] text-[13px] leading-relaxed text-slate-300">{rec.detail}</p>
      </div>

      {rec.checks.length > 0 && (
        <Panel title="Expected state of the eUICC" meta="before and after the action">
          <div className="overflow-x-auto rounded-lg border border-white/[0.07]">
            <table className="w-full min-w-[560px] text-left text-[12.5px]">
              <thead className="bg-white/[0.03] text-slate-400">
                <tr>
                  {['Condition', 'Expected', 'Before the action', 'After the action'].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.05]">
                {rec.checks.map((c) => (
                  <tr key={c.label} className="text-slate-300">
                    <td className="px-3 py-2.5 text-slate-100">{c.label}</td>
                    <td className="px-3 py-2.5 text-slate-400">{c.expected}</td>
                    <td className="px-3 py-2.5"><Result ok={c.ok_before} text={c.before} /></td>
                    <td className="px-3 py-2.5"><Result ok={c.ok_after} text={c.after} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11.5px] text-slate-500">{rec.state_source}.</p>
        </Panel>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {tele && (
          <div className="glass-quiet px-4 py-3">
            <p className="label">Telemetry after the action</p>
            <p className="mt-1 text-[12.5px] leading-relaxed text-slate-300">{tele.note}</p>
          </div>
        )}
        {rec.loop_time_ms !== null && rec.loop_time_ms !== undefined && (
          <div className="glass-quiet px-4 py-3">
            <p className="label">Detection to action</p>
            <p className="num mt-1 text-xl font-semibold text-slate-100">{fmt.ms(rec.loop_time_ms)}</p>
            <p className="mt-1 text-[11px] text-slate-500">time the loop spent, including the RSP round trip</p>
          </div>
        )}
      </div>
    </div>
  )
}

function Result({ ok, text }) {
  if (ok === null || ok === undefined) return <Minus size={13} className="text-slate-600" aria-hidden />
  return (
    <span className={`flex items-center gap-1.5 ${ok ? 'text-pass' : 'text-block'}`}>
      {ok ? <Check size={13} aria-hidden /> : <X size={13} aria-hidden />}
      <span className="num">{text}</span>
    </span>
  )
}
