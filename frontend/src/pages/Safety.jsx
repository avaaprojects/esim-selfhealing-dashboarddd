import { FileLock2, ShieldCheck, UserCheck } from 'lucide-react'
import SafetyGate from '../components/SafetyGate'
import { Badge, Field, Notice, Panel, Spinner } from '../components/Primitives'
import { fmt, statusStyle } from '../data/constants'

/**
 * The envelope Σ = ⟨Verify, B_max, τ_rollback, ρ_max⟩, every gate decision taken
 * this session, and the hash-chained audit log that pins each command to the
 * reasoning trace that produced it.
 */
export default function Safety({ safety, loading, error }) {
  if (loading) return <Spinner label="Loading safety envelope" />
  if (error) {
    return <Notice tone="block" title="Safety data is unavailable">The backend is not responding.</Notice>
  }

  const env = safety?.envelope ?? {}
  const gates = safety?.gates ?? []

  return (
    <div className="space-y-5">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="Safety envelope" icon={ShieldCheck}>
          <p className="mb-3 max-w-[72ch] text-[12.5px] leading-relaxed text-slate-400">
            Four clauses, evaluated independently so a rejection always names the guarantee that
            failed. The inverse command is constructed and checked before the forward command is
            ever sent — an action with no constructible inverse is inadmissible by definition.
          </p>
          <div className="grid gap-x-8 sm:grid-cols-2">
            <div className="divide-y divide-white/[0.05]">
              <Field label="B_max (blast radius bound)" value={env.b_max} />
              <Field label="τ_rollback" value={`${fmt.num(env.tau_rollback_s, 0)} s`} />
              <Field label="ρ_max (residual risk)" value={fmt.num(env.rho_max)} />
            </div>
            <div className="divide-y divide-white/[0.05]">
              <Field label="ρ_human (escalate above)" value={fmt.num(env.rho_human)} />
              <Field label="Signature required" value={env.require_signature ? 'yes' : 'no'} />
              <Field label="Gate budget" value={fmt.ms(safety?.latency_budget_ms)} />
            </div>
          </div>
        </Panel>

        <Panel title="Command signing" icon={FileLock2}>
          <p className="text-[13px] font-medium text-slate-100">{safety?.signer?.algorithm}</p>
          <div className="mt-2">
            <Badge tone={safety?.signer?.post_quantum ? 'pass' : 'hold'}>
              {safety?.signer?.post_quantum ? 'post-quantum backend live' : 'stub backend — not quantum-safe'}
            </Badge>
          </div>
          {!safety?.signer?.post_quantum && (
            <p className="mt-2.5 text-[11.5px] leading-relaxed text-slate-500">
              The HMAC stub keeps the reference implementation runnable without liboqs. It is not a
              security control. Install <code className="num">oqs</code> for real ML-DSA.
            </p>
          )}
          <div className="mt-3 divide-y divide-white/[0.05] border-t border-white/[0.07] pt-1">
            <Field
              label="Audit chain"
              value={safety?.audit_chain_valid ? 'intact' : 'BROKEN'}
              tone={safety?.audit_chain_valid ? 'text-pass' : 'text-block'}
            />
            <Field label="Entries" value={safety?.audit?.length ?? 0} />
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Gate decisions" icon={ShieldCheck} meta={`${gates.length} evaluated`}>
          {gates.length ? (
            <div className="space-y-4">
              {gates.map((g) => (
                <div key={g.incident_id} className="border-t border-white/[0.06] pt-3 first:border-0 first:pt-0">
                  <div className="mb-2.5 flex items-center justify-between gap-3">
                    <span className="num text-[12.5px] text-slate-200">
                      {g.incident_id} · {g.label ?? '—'}
                    </span>
                    <span className={`num text-[11px] ${statusStyle(g.status).text}`}>{g.status}</span>
                  </div>
                  <SafetyGate admissibility={g} compact />
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-slate-500">No action has reached the gate yet.</p>
          )}
        </Panel>

        <div className="space-y-4">
          <Panel title="Operator queue" icon={UserCheck} meta={`${safety?.human_queue?.length ?? 0} waiting`}>
            {safety?.human_queue?.length ? (
              <ul className="space-y-2">
                {safety.human_queue.map((q, i) => (
                  <li key={`${q.command_id}-${i}`} className="glass-quiet px-3.5 py-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className="num text-[12px] text-slate-200">{q.incident_id}</span>
                      <Badge tone="hold">risk {fmt.num(q.risk)}</Badge>
                    </div>
                    <p className="num mt-1 text-[11px] text-slate-500">{q.action}</p>
                    <p className="mt-1 text-[11.5px] leading-snug text-hold/90">
                      {(q.failed_clauses ?? []).join(', ')}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-slate-500">Nothing is waiting on an operator.</p>
            )}
          </Panel>

          <Panel title="Escalations" meta={`${safety?.escalations?.length ?? 0}`}>
            {safety?.escalations?.length ? (
              <ul className="space-y-2">
                {safety.escalations.map((e, i) => (
                  <li key={`${e.incident_id}-${i}`} className="glass-quiet px-3.5 py-2.5">
                    <p className="num text-[12px] text-slate-200">{e.incident_id}</p>
                    <p className="mt-1 text-[11.5px] leading-snug text-slate-400">
                      {e.reason ?? (e.failed_clauses ?? []).join(', ')}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-slate-500">Nothing has been escalated.</p>
            )}
          </Panel>
        </div>
      </div>

      <Panel title="Audit log" icon={FileLock2} meta="hash-chained, append-only">
        {safety?.audit?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[11.5px]">
              <thead className="label border-b border-white/[0.08]">
                <tr>
                  {['Entry', 'Incident', 'Action', 'Status', 'Trace digest', 'Hash'].map((h) => (
                    <th key={h} className="py-2 pr-4 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="num divide-y divide-white/[0.05] text-slate-300">
                {safety.audit.map((e) => (
                  <tr key={e.entry_id}>
                    <td className="py-2 pr-4">{e.entry_id}</td>
                    <td className="py-2 pr-4">{e.incident_id}</td>
                    <td className="py-2 pr-4">{e.action}</td>
                    <td className={`py-2 pr-4 ${statusStyle(e.status).text}`}>{e.status}</td>
                    <td className="py-2 pr-4 text-slate-500">{e.trace_digest}</td>
                    <td className="py-2 pr-4 text-slate-500">{e.entry_hash}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-[13px] text-slate-500">The log is empty until the first command is built.</p>
        )}
      </Panel>
    </div>
  )
}
