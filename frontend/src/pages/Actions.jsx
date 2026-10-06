import { CircuitBoard, Crosshair } from 'lucide-react'
import ActionCard from '../components/ActionCard'
import { Badge, Field, Meter, Notice, Panel, Spinner } from '../components/Primitives'
import { fmt } from '../data/constants'

/**
 * The remediation action space A, its static profile from config.ACTION_PROFILE,
 * the RSP endpoint each one maps to, and — once the agent has dispatched
 * anything — what actually happened this session.
 */
export default function Actions({ actions, trace, loading, error }) {
  if (loading) return <Spinner label="Loading action space" />
  if (error) {
    return <Notice tone="block" title="The action space is unavailable">The backend is not responding.</Notice>
  }

  const space = actions?.actions ?? []
  const endpoints = actions?.endpoints ?? []
  const endpointFor = (a) => endpoints.find((e) => e.action === a)
  const plan = trace?.plan

  return (
    <div className="space-y-5">
      {plan && (
        <Panel title="Most recent remediation plan" icon={Crosshair} meta={trace.incident.incident_id}>
          <p className="mb-3 max-w-[72ch] text-[12.5px] leading-relaxed text-slate-400">
            Candidates are filtered against the constraint set first and only then ranked on
            utility, so an attractive but infeasible action is never selected on utility grounds.
            {plan.fleet_size > 1 && ` This profile is shared across ${plan.fleet_size} devices, which multiplies every blast radius below.`}
          </p>
          <div className="space-y-2">
            {plan.ranked.map((c) => <ActionCard key={c.action} candidate={c} />)}
            {plan.infeasible.map((c) => <ActionCard key={c.action} candidate={c} />)}
          </div>
        </Panel>
      )}

      <Panel title="Action space" icon={CircuitBoard} meta={`${space.length} actions`}>
        <div className="grid gap-3 lg:grid-cols-2">
          {space.map((a) => {
            const ep = endpointFor(a.action)
            return (
              <div key={a.action} className="glass-quiet px-4 py-3.5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[13.5px] font-semibold text-slate-100">{a.label}</p>
                    <p className="num mt-0.5 truncate text-[11px] text-slate-500">{ep?.endpoint}</p>
                  </div>
                  <Badge tone={a.reversible ? 'pass' : 'block'}>
                    {a.reversible ? 'reversible' : 'irreversible'}
                  </Badge>
                </div>

                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
                  <div>
                    <dt className="label">Risk</dt>
                    <dd className="num text-[12.5px] text-slate-200">{fmt.num(a.risk)}</dd>
                    <Meter className="mt-1" value={a.risk} max={1} limit={0.3} tone={a.risk > 0.3 ? 'block' : 'pass'} />
                  </div>
                  <div>
                    <dt className="label">Blast radius</dt>
                    <dd className="num text-[12.5px] text-slate-200">{a.blast_radius}</dd>
                    <Meter className="mt-1" value={Math.min(a.blast_radius, 4)} max={4} limit={1} tone={a.blast_radius > 1 ? 'block' : 'agent'} />
                  </div>
                </dl>

                <div className="mt-2.5 divide-y divide-white/[0.05] border-t border-white/[0.06] pt-1">
                  <Field label="Cost" value={fmt.num(a.cost)} />
                  <Field label="SLA cost" value={`${fmt.num(a.sla_cost, 0)} s`} />
                  <Field label="Security cost" value={fmt.num(a.sec_cost)} />
                  <Field
                    label="Rollback window"
                    value={a.rollback_seconds !== null ? `~${fmt.num(a.rollback_seconds, 0)} s` : 'no inverse'}
                    tone={a.rollback_seconds === null ? 'text-block' : ''}
                  />
                  <Field
                    label="This session"
                    value={`${a.observed.succeeded}/${a.observed.dispatched} succeeded`}
                  />
                </div>
              </div>
            )
          })}
        </div>
      </Panel>
    </div>
  )
}
