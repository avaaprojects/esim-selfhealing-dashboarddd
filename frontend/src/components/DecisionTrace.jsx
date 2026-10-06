import { useState } from 'react'
import { Check, ChevronDown, Minus, X } from 'lucide-react'
import { Badge, Field } from './Primitives'
import { BeliefBar } from './IncidentCard'
import ActionCard from './ActionCard'
import SafetyGate from './SafetyGate'
import MemoryCard from './MemoryCard'
import { fmt } from '../data/constants'

/**
 * INCIDENT → OBSERVE → REASON → PLAN → SAFETY → ACT → VERIFY → LEARN.
 *
 * Every stage expands into the auditable evidence for that step. The REASON
 * stage deliberately shows only what the system recorded and hashed into the
 * audit entry — the published rationale for each tool call, the tool called,
 * its arguments, and what came back. There is no hidden scratchpad here to
 * expose, and none is invented.
 */
export default function DecisionTrace({ trace, defaultOpen = 'reason' }) {
  const [open, setOpen] = useState(defaultOpen)
  if (!trace) return null

  const stages = trace.stages ?? []

  return (
    <div className="space-y-2">
      <div className="glass-quiet px-4 py-3">
        <p className="label">Incident</p>
        <p className="num mt-0.5 text-sm font-semibold text-slate-100">
          {trace.incident.incident_id}
        </p>
        <p className="mt-0.5 text-[12px] text-slate-400">
          {trace.diagnosis.fault_label} on eUICC {trace.incident.observation.euicc_id} ·
          cell {trace.incident.observation.cell_id}
        </p>
      </div>

      {stages.map((stage) => {
        const isOpen = open === stage.key
        return (
          <article
            key={stage.key}
            className={`glass overflow-hidden transition-colors ${isOpen ? 'border-agent/30' : ''}`}
          >
            <button
              type="button"
              onClick={() => setOpen(isOpen ? null : stage.key)}
              aria-expanded={isOpen}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.03]"
            >
              <StageMark ok={stage.ok} />
              <div className="min-w-0 flex-1">
                <p className="num text-[11px] font-semibold tracking-wider text-slate-400">
                  {stage.label}
                </p>
                <p className="truncate text-[13px] text-slate-100">{stage.headline}</p>
              </div>
              {stage.latency_ms !== null && stage.latency_ms !== undefined && (
                <span className="num hidden shrink-0 text-[11px] text-slate-500 sm:block">
                  {fmt.ms(stage.latency_ms)}
                </span>
              )}
              <ChevronDown
                size={15}
                className={`shrink-0 text-slate-500 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                aria-hidden
              />
            </button>

            {isOpen && (
              <div className="animate-risein border-t border-white/[0.07] px-4 py-4">
                <p className="mb-3 text-[12.5px] leading-relaxed text-slate-400">{stage.detail}</p>
                <StageBody stageKey={stage.key} trace={trace} />
              </div>
            )}
          </article>
        )
      })}
    </div>
  )
}

/** The recorded ReAct steps: rationale, tool called, what came back. */
export function ReactTraceList({ steps = [] }) {
  return (
    <ol className="space-y-2.5">
      {steps.map((step) => (
        <li key={step.k} className="glass-quiet px-3.5 py-3">
          <div className="flex items-center justify-between gap-3">
            <Badge tone={step.tool === 'FINISH' ? 'neutral' : 'agent'}>
              {step.tool === 'FINISH' ? 'terminate' : step.tool}
            </Badge>
            <span className="num text-[11px] text-slate-500">step {step.k}</span>
          </div>
          <p className="mt-2 text-[12.5px] leading-relaxed text-slate-300">{step.rationale}</p>
          <p className="num mt-2 break-words rounded-lg bg-black/25 px-3 py-2 text-[11.5px] leading-relaxed text-cyan-100/80">
            {step.observation}
          </p>
        </li>
      ))}
    </ol>
  )
}

function StageMark({ ok }) {
  if (ok === null || ok === undefined) {
    return <Minus size={14} className="shrink-0 text-slate-500" aria-hidden />
  }
  return ok ? (
    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-pass/15 text-pass">
      <Check size={12} aria-hidden />
    </span>
  ) : (
    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-block/15 text-block">
      <X size={12} aria-hidden />
    </span>
  )
}

function StageBody({ stageKey, trace }) {
  const { incident, diagnosis, plan, act, command, retrieved } = trace

  if (stageKey === 'observe') {
    const feats = incident.observation.features
    return (
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <p className="label mb-2">Observed signals x_t</p>
          <div className="divide-y divide-white/[0.05]">
            {Object.entries(feats).map(([k, v]) => (
              <Field key={k} label={k} value={fmt.num(v, 3)} />
            ))}
          </div>
        </div>
        <div>
          <p className="label mb-2">Belief b_t over fault classes</p>
          <BeliefBar belief={incident.belief} />
        </div>
      </div>
    )
  }

  if (stageKey === 'reason') {
    return (
      <div>
        <ReactTraceList steps={diagnosis.trace} />
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="divide-y divide-white/[0.05]">
            <Field label="Diagnosis" value={diagnosis.fault_label} mono={false} />
            <Field label="Confidence" value={fmt.pct(diagnosis.confidence)} />
            <Field label="Tool calls" value={diagnosis.tool_calls} />
            <Field label="Precedents retrieved" value={diagnosis.retrieved_ids.length} />
          </div>
          <div>
            <p className="label mb-2">Candidate actions proposed</p>
            <div className="flex flex-wrap gap-1.5">
              {diagnosis.candidate_actions.map((a) => (
                <Badge key={a.action}>{a.label}</Badge>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (stageKey === 'plan') {
    if (!plan) return <p className="text-[13px] text-slate-500">No plan was produced.</p>
    return (
      <div>
        <p className="label mb-2">
          Utility U(a) = w1·P_success − w2·Cost − w3·BlastRadius, with w ={' '}
          <span className="num">
            ({fmt.num(plan.weights.w1)}, {fmt.num(plan.weights.w2)}, {fmt.num(plan.weights.w3)})
          </span>
        </p>
        <div className="space-y-2">
          {plan.ranked.map((c) => (
            <ActionCard key={c.action} candidate={c} />
          ))}
          {plan.infeasible.map((c) => (
            <ActionCard key={c.action} candidate={c} />
          ))}
        </div>
      </div>
    )
  }

  if (stageKey === 'safety') {
    if (!act) return <p className="text-[13px] text-slate-500">The gate was never reached.</p>
    return <SafetyGate admissibility={act.admissibility} command={command} />
  }

  if (stageKey === 'act') {
    if (!act) return <p className="text-[13px] text-slate-500">Nothing was dispatched.</p>
    return (
      <div className="grid gap-4 md:grid-cols-2">
        <div className="divide-y divide-white/[0.05]">
          <Field label="Action" value={act.label} mono={false} />
          <Field label="Endpoint" value={command?.endpoint ?? '—'} />
          <Field label="Command id" value={command?.command_id ?? '—'} />
          <Field label="Signature" value={command?.signature_preview ?? '—'} />
        </div>
        <div className="divide-y divide-white/[0.05]">
          <Field label="Dispatched" value={act.dispatched ? 'yes' : 'no'} />
          <Field label="Gate latency" value={fmt.ms(act.gate_latency_ms)} />
          <Field label="RSP round trip" value={fmt.ms(act.dispatch_latency_ms)} />
          <Field label="Inverse a⁻¹" value={command?.inverse_endpoint ?? 'none'} />
        </div>
      </div>
    )
  }

  if (stageKey === 'verify') {
    if (!act) return <p className="text-[13px] text-slate-500">Nothing to verify.</p>
    return (
      <div className="divide-y divide-white/[0.05]">
        <Field
          label="Outcome"
          value={act.success ? 'fault cleared' : 'fault persisted'}
          tone={act.success ? 'text-pass' : 'text-hold'}
          mono={false}
        />
        <Field label="Rolled back" value={act.rolled_back ? 'yes' : 'no'} />
        <Field label="Routed to" value={act.status} />
        <Field label="RSP detail" value={act.message} mono={false} />
      </div>
    )
  }

  if (stageKey === 'learn') {
    return (
      <div className="grid gap-4 md:grid-cols-2">
        <div className="divide-y divide-white/[0.05]">
          <Field label="Reward r_t" value={fmt.num(trace.reward, 3)} />
          <Field label="Cost c_1 (envelope)" value={fmt.num(trace.costs?.[0], 3)} />
          <Field label="Cost c_2 (blast)" value={fmt.num(trace.costs?.[1], 3)} />
          <Field label="Total loop time" value={fmt.ms(trace.total_latency_ms)} />
        </div>
        <div>
          <p className="label mb-2">Precedent retrieved from memory D</p>
          {retrieved?.length ? (
            <div className="space-y-2">
              {retrieved.map((r) => (
                <MemoryCard key={r.record_id} record={r} compact />
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-slate-500">
              No precedent cleared the similarity floor — a cold start for this fault.
            </p>
          )}
        </div>
      </div>
    )
  }

  return null
}
