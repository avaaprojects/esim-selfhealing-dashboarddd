import { useState } from 'react'
import { Brain, ChevronDown, Crosshair } from 'lucide-react'
import { Badge, Field, Notice, Panel } from '../Primitives'
import ActionCard from '../ActionCard'
import MemoryCard from '../MemoryCard'
import { BeliefBar } from '../IncidentCard'
import { ReactTraceList } from '../DecisionTrace'
import { fmt } from '../../data/constants'

/**
 * Stage 4: what the agent made of the incident. Detected issue, diagnosis
 * (with the recorded reasoning steps on demand), and the action it selected
 * with the alternatives it weighed. All of it is the existing loop's trace.
 */
export default function AgentPanel({ view }) {
  const { trace } = view
  const [showSteps, setShowSteps] = useState(false)
  const [showOthers, setShowOthers] = useState(false)
  const d = trace.diagnosis
  const plan = trace.plan
  const selected = plan?.selected ?? null
  const others = [...(plan?.ranked ?? []), ...(plan?.infeasible ?? [])].filter((c) => !c.selected)

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title="Detected issue and diagnosis" icon={Brain}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-[18px] font-semibold text-slate-50">{d.fault_label}</p>
          <p className="num text-[13px] text-slate-400">confidence {fmt.pct(d.confidence)}</p>
        </div>
        <p className="mt-2 text-[13px] leading-relaxed text-slate-300">{d.text}</p>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <p className="label mb-2">Belief over fault classes</p>
            <BeliefBar belief={trace.incident.belief} />
          </div>
          <div className="divide-y divide-white/[0.05]">
            <Field label="Tool calls" value={d.tool_calls} />
            <Field label="Precedents retrieved" value={d.retrieved_ids.length} />
            <Field label="Actions proposed" value={d.candidate_actions.length} />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {d.candidate_actions.map((a) => (
            <Badge key={a.action}>{a.label}</Badge>
          ))}
        </div>

        <button
          type="button"
          onClick={() => setShowSteps(!showSteps)}
          aria-expanded={showSteps}
          className="mt-4 flex items-center gap-1.5 text-[12.5px] font-medium text-agent hover:underline"
        >
          {showSteps ? 'Hide' : 'Show'} the {d.trace.length} reasoning steps
          <ChevronDown size={13} className={showSteps ? 'rotate-180' : ''} aria-hidden />
        </button>
        {showSteps && (
          <div className="animate-risein mt-3">
            <ReactTraceList steps={d.trace} />
          </div>
        )}

        {trace.retrieved?.length > 0 && (
          <div className="mt-4">
            <p className="label mb-2">Similar past incidents the agent relied on</p>
            <div className="space-y-2">
              {trace.retrieved.slice(0, 2).map((r) => (
                <MemoryCard key={r.record_id} record={r} compact />
              ))}
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Selected action" icon={Crosshair}>
        {!selected ? (
          <Notice tone="block" title="No feasible action">
            Every candidate was filtered out by the planning constraints, so the agent had nothing safe to propose and
            escalated the incident with its full reasoning trace.
          </Notice>
        ) : (
          <>
            <ActionCard candidate={selected} />
            <p className="mt-3 text-[12px] leading-relaxed text-slate-500">
              Chosen by utility U(a) = w1·P_success − w2·Cost − w3·BlastRadius with w ={' '}
              <span className="num">
                ({fmt.num(plan.weights.w1)}, {fmt.num(plan.weights.w2)}, {fmt.num(plan.weights.w3)})
              </span>
              {plan.fleet_size > 1 ? `, on a profile shared by ${plan.fleet_size} devices` : ''}. This is a proposal: the safety
              gate below decides whether it runs.
            </p>
          </>
        )}
        {others.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setShowOthers(!showOthers)}
              aria-expanded={showOthers}
              className="mt-4 flex items-center gap-1.5 text-[12.5px] font-medium text-agent hover:underline"
            >
              {showOthers ? 'Hide' : 'Show'} the {others.length} other candidates considered
              <ChevronDown size={13} className={showOthers ? 'rotate-180' : ''} aria-hidden />
            </button>
            {showOthers && (
              <div className="animate-risein mt-3 space-y-2">
                {others.map((c) => (
                  <ActionCard key={c.action} candidate={c} />
                ))}
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  )
}
