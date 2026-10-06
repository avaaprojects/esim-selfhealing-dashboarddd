import { Brain } from 'lucide-react'
import DecisionTrace from '../components/DecisionTrace'
import { Notice, Panel, Spinner } from '../components/Primitives'
import { fmt } from '../data/constants'

/**
 * The same trace as the Incidents screen, opened on REASON and framed around
 * the evidence rather than the incident. Nothing private is shown: every line
 * here is a recorded tool call, its arguments and what the RSP returned.
 */
export default function Reasoning({ trace, loading, incidents = [], selected, onSelect }) {
  if (loading) return <Spinner label="Loading agent trace" />

  if (!trace) {
    return (
      <Notice title="No reasoning trace yet">
        The agent builds a trace while diagnosing an incident. Run self-healing to produce one.
      </Notice>
    )
  }

  const d = trace.diagnosis

  return (
    <div className="space-y-5">
      <Panel title="Diagnosis" icon={Brain} meta={`${d.tool_calls} tool calls`}>
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_260px]">
          <div>
            <p className="text-lg font-semibold text-slate-100">{d.fault_label}</p>
            <p className="num mt-1.5 text-[12px] leading-relaxed text-slate-400">{d.text}</p>
          </div>
          <dl className="grid grid-cols-2 gap-3">
            <Stat label="Confidence" value={fmt.pct(d.confidence)} />
            <Stat label="Precedents" value={d.retrieved_ids.length} />
            <Stat label="Tool calls" value={d.tool_calls} />
            <Stat label="LLM calls" value={d.llm_calls} />
          </dl>
        </div>

        {incidents.length > 1 && (
          <div className="mt-4 flex flex-wrap gap-1.5 border-t border-white/[0.07] pt-3">
            {incidents.map((inc) => (
              <button
                key={inc.incident_id}
                type="button"
                onClick={() => onSelect?.(inc.incident_id)}
                className={[
                  'num rounded-lg px-2.5 py-1 text-[11px] transition-colors',
                  inc.incident_id === selected
                    ? 'bg-agent/15 text-agent ring-1 ring-agent/30'
                    : 'bg-white/[0.04] text-slate-400 hover:text-slate-200',
                ].join(' ')}
              >
                {inc.incident_id}
              </button>
            ))}
          </div>
        )}
      </Panel>

      <DecisionTrace trace={trace} defaultOpen="reason" />
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="glass-quiet px-3 py-2">
      <dt className="label">{label}</dt>
      <dd className="num mt-0.5 text-[15px] font-semibold text-slate-100">{value}</dd>
    </div>
  )
}
