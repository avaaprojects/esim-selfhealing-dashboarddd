import { Wrench } from 'lucide-react'
import { Badge, Field, Notice, Panel } from '../Primitives'
import { fmt } from '../../data/constants'

const TONE = { pass: 'pass', agent: 'agent', hold: 'hold', block: 'block' }

/**
 * Stage 6: what was actually done to the device, step by step: the action, its
 * status, when it was recorded and the result. Timestamps are the audit entry's
 * own wall-clock time; a step that never happened has none.
 */
export default function RemediationPanel({ view }) {
  const r = view.remediation
  if (!r.steps.length) {
    return (
      <Notice title="No remedial action was taken">
        Nothing was signed, gated or dispatched, so the device was left exactly as the agent found it.
      </Notice>
    )
  }
  return (
    <Panel title="Remedial actions" icon={Wrench} meta={r.recorded_at ? `recorded ${fmt.datetimeSec(r.recorded_at)}` : null}>
      <div className="overflow-x-auto rounded-lg border border-white/[0.07]">
        <table className="w-full min-w-[720px] text-left text-[12.5px]">
          <thead className="bg-white/[0.03] text-slate-400">
            <tr>
              {['#', 'Action', 'Status', 'Timestamp', 'Result'].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.05]">
            {r.steps.map((s, i) => (
              <tr key={s.key} className="align-top text-slate-300">
                <td className="num px-3 py-2.5 text-slate-500">{i + 1}</td>
                <td className="px-3 py-2.5 text-slate-100">{s.action}</td>
                <td className="px-3 py-2.5"><Badge tone={TONE[s.tone] ?? 'neutral'}>{s.status}</Badge></td>
                <td className="num whitespace-nowrap px-3 py-2.5 text-[12px]">{s.ts ? fmt.datetimeSec(s.ts) : '—'}</td>
                <td className="px-3 py-2.5">
                  <span className="break-words">{s.result}</span>
                  {s.latency_ms !== null && s.latency_ms !== undefined && (
                    <span className="num ml-2 text-[11px] text-slate-500">{fmt.ms(s.latency_ms)}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {r.command && (
        <div className="mt-4 grid gap-x-8 sm:grid-cols-2">
          <div className="divide-y divide-white/[0.05]">
            <Field label="Command" value={r.command.command_id} />
            <Field label="Endpoint" value={r.command.endpoint} />
          </div>
          <div className="divide-y divide-white/[0.05]">
            <Field label="Inverse (rollback)" value={r.command.inverse_endpoint ?? 'none'} />
            <Field label="Signature" value={r.command.signature_preview} />
          </div>
        </div>
      )}
    </Panel>
  )
}
