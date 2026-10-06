import { ArrowRight, Ban, Check, ShieldAlert, ShieldCheck } from 'lucide-react'
import { Badge, Field } from '../Primitives'
import SafetyGate from '../SafetyGate'
import { GATE_ROUTE, fmt } from '../../data/constants'

const STEP = {
  done: { text: 'text-pass', ring: 'ring-pass/40', bg: 'bg-pass/10' },
  blocked: { text: 'text-block', ring: 'ring-block/40', bg: 'bg-block/10' },
  attention: { text: 'text-hold', ring: 'ring-hold/40', bg: 'bg-hold/10' },
  skipped: { text: 'text-slate-500', ring: 'ring-white/10', bg: 'bg-white/[0.03]' },
}

/**
 * Stage 5, deliberately framed apart from the rest. The gate sits between what
 * the agent wants to do and what actually happens to the device. It shows four
 * states in order (Proposed, Approved or Blocked, Executed or Not executed),
 * the four clauses of the safety envelope, and where the action went next.
 */
export default function GatePanel({ view }) {
  const { gate, trace } = view
  const ok = gate.tone === 'pass' || gate.tone === 'agent'
  const Icon = gate.state === 'BLOCKED' || gate.state === 'NONE' ? ShieldAlert : ShieldCheck

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ring-1 ${ok ? 'bg-pass/10 text-pass ring-pass/30' : gate.tone === 'hold' ? 'bg-hold/10 text-hold ring-hold/30' : 'bg-block/10 text-block ring-block/30'}`}>
          <Icon size={22} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[17px] font-semibold text-slate-50">{gate.headline}</p>
          <p className="mt-0.5 max-w-[80ch] text-[13px] leading-relaxed text-slate-400">{gate.detail}</p>
        </div>
        <Badge tone={gate.route === 'auto-dispatch' ? 'pass' : gate.route === 'operator-queue' ? 'hold' : 'block'}>
          {GATE_ROUTE[gate.route]}
        </Badge>
      </div>

      <ol className="flex flex-wrap items-stretch gap-2" aria-label="Gate states">
        {gate.steps.map((s, i) => {
          const st = STEP[s.state] ?? STEP.skipped
          const Mark = s.state === 'done' ? Check : s.state === 'blocked' ? Ban : null
          return (
            <li key={s.key} className="flex items-center gap-2">
              <div className={`min-w-[170px] rounded-xl px-3.5 py-2.5 ring-1 ${st.bg} ${st.ring}`}>
                <p className={`flex items-center gap-1.5 text-[13px] font-semibold ${st.text}`}>
                  {Mark && <Mark size={13} aria-hidden />}
                  {s.label}
                </p>
                <p className="mt-0.5 max-w-[260px] truncate text-[11.5px] text-slate-400" title={s.detail}>{s.detail}</p>
              </div>
              {i < gate.steps.length - 1 && <ArrowRight size={15} className="shrink-0 text-slate-600" aria-hidden />}
            </li>
          )
        })}
      </ol>

      {trace.act ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div>
            <p className="label mb-2">Four clauses of the safety envelope</p>
            <SafetyGate admissibility={trace.act.admissibility} command={trace.command} />
          </div>
          <div>
            <p className="label mb-2">Envelope limits</p>
            <div className="divide-y divide-white/[0.05]">
              <Field label="Blast radius limit B_max" value={gate.limits.b_max} />
              <Field label="Automatic risk limit ρ_max" value={fmt.num(gate.limits.rho_max, 2)} />
              <Field label="Operator risk limit ρ_human" value={fmt.num(gate.limits.rho_human, 2)} />
              <Field label="Rollback window τ" value={`${fmt.num(gate.limits.tau_rollback_s, 0)} s`} />
              <Field label="Signature required" value={gate.limits.require_signature ? 'yes' : 'no'} />
              <Field label="Gate latency" value={fmt.ms(gate.gate_latency_ms)} />
            </div>
          </div>
        </div>
      ) : (
        <p className="text-[12.5px] text-slate-500">
          No command was built, so there was nothing for the four clauses to check.
        </p>
      )}
    </div>
  )
}
