import { useState } from 'react'
import { FlaskConical, Layers, Loader2, Play, RotateCcw } from 'lucide-react'
import AgentPipeline from '../components/AgentPipeline'
import SafetyGate from '../components/SafetyGate'
import { Badge, Field, Notice, Panel, Spinner } from '../components/Primitives'
import EstateSummary from '../components/EstateSummary'
import { fmt, statusStyle } from '../data/constants'

/**
 * SIMULATED DATA — a testing environment for the agent, organised by what
 * each group of scenarios exercises (`CATEGORY_ORDER` from the backend),
 * not a flat list. Owners run scenarios; clients see the same catalogue,
 * the same categorisation and the same results, read-only.
 */
export default function Simulation({
  simulation,
  loading,
  error,
  onRun,
  running,
  runningKey,
  stage,
  onOpenIncident,
  onReset,
  isOwner,
}) {
  const [expanded, setExpanded] = useState(null)

  if (loading) return <Spinner label="Loading scenarios" />
  if (error) {
    return <Notice tone="block" title="Scenarios are unavailable">The backend is not responding.</Notice>
  }

  const scenarios = simulation?.scenarios ?? []
  const categories = simulation?.categories ?? []
  const last = simulation?.last_result

  const byCategory = categories.map((cat) => ({
    ...cat,
    scenarios: scenarios.filter((s) => s.category === cat.key),
  }))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-[72ch] text-[13px] leading-relaxed text-slate-400">
          A controlled testing environment: every scenario drives a real telemetry stream past
          the detector and lets the actual agent handle whatever it opens — nothing here is a
          scripted animation. Each run targets freshly-numbered eUICCs, since the detector folds
          every sample into its own baseline.
        </p>
        {isOwner && (
          <button
            type="button"
            onClick={onReset}
            className="flex shrink-0 items-center gap-2 rounded-xl border border-white/10 px-3.5 py-2 text-[12.5px] text-slate-300 transition-colors hover:bg-white/[0.05]"
          >
            <RotateCcw size={13} aria-hidden />
            Reset session
          </button>
        )}
      </div>

      {byCategory.map((cat) => (
        <section key={cat.key} className="space-y-3">
          <header className="flex items-center gap-2.5">
            <Layers size={14} className="text-agent" aria-hidden />
            <h2 className="text-[13.5px] font-semibold text-slate-100">{cat.label}</h2>
            <span className="text-[11.5px] text-slate-500">{cat.blurb}</span>
          </header>

          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {cat.scenarios.map((s) => {
              const busy = running && runningKey === s.key
              const isOpen = expanded === s.key
              const result = last && last.scenario?.key === s.key ? last : null
              const isMultiDevice = s.fleet && Object.keys(s.fleet).length > 0

              return (
                <article key={s.key} className={`glass flex flex-col p-5 ${busy ? 'border-agent/40' : ''}`}>
                  <header className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="text-[14px] font-semibold text-slate-100">{s.name}</h3>
                      <p className="num mt-0.5 text-[11px] text-slate-500">{s.use_case}</p>
                    </div>
                    {isMultiDevice && <Badge tone="hold">shared profile</Badge>}
                  </header>

                  <p className="mt-2.5 flex-1 text-[12.5px] leading-relaxed text-slate-400">
                    {s.description}
                  </p>
                  <p className="mt-2 text-[12px] leading-relaxed text-agent/80">{s.expectation}</p>

                  {onRun ? (
                    <button
                      type="button"
                      onClick={() => {
                        setExpanded(s.key)
                        onRun(s.key)
                      }}
                      disabled={running}
                      className={[
                        'mt-4 flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-[12.5px] font-semibold transition-all',
                        busy
                          ? 'cursor-wait bg-agent/15 text-agent/70'
                          : running
                            ? 'cursor-not-allowed bg-white/[0.04] text-slate-500'
                            : 'bg-agent/15 text-agent ring-1 ring-agent/30 hover:bg-agent/25',
                      ].join(' ')}
                    >
                      {busy ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Play size={13} aria-hidden />}
                      {busy ? 'Running' : 'Run scenario'}
                    </button>
                  ) : (
                    result && (
                      <button
                        type="button"
                        onClick={() => setExpanded(isOpen ? null : s.key)}
                        className="mt-4 flex items-center justify-center gap-2 rounded-xl border border-white/10 px-4 py-2.5 text-[12.5px] font-medium text-slate-300 hover:bg-white/[0.05]"
                      >
                        {isOpen ? 'Hide last result' : 'View last result'}
                      </button>
                    )
                  )}

                  {busy && (
                    <div className="mt-3">
                      <AgentPipeline stage={stage} orientation="horizontal" />
                    </div>
                  )}

                  {isOpen && !busy && result && (
                    <div className="animate-risein mt-4 border-t border-white/[0.07] pt-3">
                      <div className="divide-y divide-white/[0.05]">
                        <Field label="Samples processed" value={result.report.samples_seen} />
                        <Field label="Incidents opened" value={result.report.incidents} />
                        <Field label="Auto-remediation rate" value={fmt.pct(result.report.auto_remediation_rate)} />
                        <Field
                          label="Constraint violations"
                          value={fmt.pct(result.report.constraint_violation_rate)}
                          tone={result.report.constraint_violation_rate > 0 ? 'text-block' : 'text-pass'}
                        />
                        <Field label="Mean loop latency" value={`${fmt.num(result.report.mean_loop_latency_s, 2)} s`} />
                      </div>

                      {result.estate && (
                        <div className="mt-3">
                          <EstateSummary estate={result.estate} />
                        </div>
                      )}

                      {/* GATED OUTPUT — the real per-incident safety decision, not a
                          faked frontend status. */}
                      {result.records.length === 1 && (
                        <div className="mt-3">
                          <p className="label mb-1.5">Safety / gated output</p>
                          <SafetyGate
                            admissibility={result.records[0].act?.admissibility}
                            command={result.records[0].command}
                            compact
                          />
                        </div>
                      )}

                      <ul className="mt-3 space-y-1.5">
                        {result.records.map((r) => (
                          <li key={r.incident.incident_id}>
                            <button
                              type="button"
                              onClick={() => onOpenIncident?.(r.incident.incident_id)}
                              className="glass-quiet flex w-full items-center gap-2.5 px-3 py-2 text-left hover:border-white/20"
                            >
                              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusStyle(r.status).dot}`} aria-hidden />
                              <span className="num min-w-0 flex-1 truncate text-[11.5px] text-slate-200">
                                {r.incident.incident_id}
                              </span>
                              <Badge
                                tone={r.status === 'AUTO-REMEDIATED' ? 'pass' : r.status === 'HUMAN-IN-LOOP' ? 'hold' : 'block'}
                              >
                                {statusStyle(r.status).label}
                              </Badge>
                            </button>
                          </li>
                        ))}
                      </ul>

                      {!result.records.length && (
                        <p className="mt-3 text-[12px] leading-relaxed text-slate-500">
                          The detector absorbed this run without opening an incident — the drift stayed
                          under the chi-square threshold.
                        </p>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        </section>
      ))}

      {last && (
        <Panel title="Last run summary" icon={FlaskConical} meta={last.scenario?.name}>
          <pre className="num overflow-x-auto whitespace-pre text-[11.5px] leading-relaxed text-slate-300">
{last.report.summary}
          </pre>
          <p className="mt-3 text-[11.5px] text-slate-500">
            Devices in this run: <span className="num">{(last.devices ?? []).join(', ')}</span>
          </p>
        </Panel>
      )}
    </div>
  )
}
