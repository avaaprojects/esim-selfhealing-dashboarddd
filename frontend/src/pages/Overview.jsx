import { Activity, CircleGauge, Cpu, Radio, Waves } from 'lucide-react'
import AgentCore from '../components/AgentCore'
import AgentPipeline from '../components/AgentPipeline'
import ActivityFeed from '../components/ActivityFeed'
import StatusCard from '../components/StatusCard'
import { BeliefBar } from '../components/IncidentCard'
import { MiniTelemetry, dynamicWindow } from '../components/TelemetryChart'
import { Badge, Notice, Panel } from '../components/Primitives'
import { fmt, statusStyle } from '../data/constants'

/**
 * The three-column centre of the application.
 *
 * Left: system state, read from the live RSP client, signer and detector.
 * Centre: the agent itself and its pipeline.
 * Right: what the agent is working on and what it just did.
 */
export default function Overview({
  status,
  telemetry,
  incidents,
  activity,
  stage,
  running,
  lastRecord,
  onOpenIncident,
  error,
}) {
  const samples = telemetry?.samples ?? []
  const counters = status?.counters ?? {}
  const health = status?.system_health ?? {}
  const latest = incidents?.[0]

  return (
    <div className="space-y-5">
      {error && (
        <Notice tone="block" title="The backend is not responding">
          Start it from the project root with{' '}
          <code className="num text-slate-300">python -m uvicorn backend.app:app --port 8000</code>,
          then reload. The interface stays on screen so you can see what it will show.
        </Notice>
      )}

      <div className="grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)_320px]">
        {/* LEFT — system state */}
        <div className="space-y-4">
          <Panel title="System health" icon={CircleGauge} meta={health.state}>
            <div className="flex items-baseline gap-2">
              <span className="num text-3xl font-semibold text-slate-100">
                {fmt.num(health.score, 2)}
              </span>
              <span className="text-[12px] text-slate-500">
                g_t vs {fmt.num(health.threshold, 2)}
              </span>
            </div>
            <p className="mt-1 text-[12px] text-slate-400">
              {health.headroom !== null && health.headroom !== undefined
                ? `${fmt.pct(health.headroom)} headroom below the chi-square threshold`
                : 'Waiting for the first telemetry sample.'}
            </p>
            <div className="mt-3">
              <MiniTelemetry samples={samples} featureKey="score" />
            </div>
          </Panel>

          <div className="space-y-2">
            {(status?.panels ?? []).map(({ key, ...panel }) => (
              // `key` names the panel in the API payload; React needs it as a
              // real key, not spread into the props.
              <StatusCard key={key} {...panel} />
            ))}
          </div>

          <Panel title="Live telemetry" icon={Waves} meta={`${samples.length} samples`} dense>
            <div className="space-y-3">
              {/* All five channels, not a selection: the detector scores the
                  joint vector, so a view showing three of them cannot explain
                  why it fired. */}
              <Sparkline label="AKA failure rate" samples={samples} featureKey="aka_fail_rate" color="#22D3EE" digits={2} />
              <Sparkline label="OTA failure rate" samples={samples} featureKey="ota_fail_rate" color="#8B5CF6" digits={3} />
              <Sparkline label="RSRP (dBm)" samples={samples} featureKey="rsrp_dbm" color="#F472B6" digits={1} />
              <Sparkline label="Session drop rate" samples={samples} featureKey="drop_rate" color="#34D399" digits={3} />
              <Sparkline label="OTA latency (ms)" samples={samples} featureKey="latency_ms" color="#FBBF24" digits={1} />
            </div>
          </Panel>
        </div>

        {/* CENTRE — the agent */}
        <div className="space-y-4">
          <Panel className="relative overflow-hidden">
            <div className="flex flex-col items-center py-2">
              <p className="num text-[11px] tracking-[0.22em] text-slate-500">
                AUTONOMOUS RECOVERY AGENT
              </p>
              <div className="mt-5">
                <AgentCore
                  stage={stage}
                  running={running}
                  lastAction={lastRecord?.selected_label}
                  status={
                    status?.last_scenario
                      ? `last run: ${status.last_scenario} · ${fmt.clock(status.last_run_at)}`
                      : null
                  }
                />
              </div>
            </div>

            <div className="mt-5 border-t border-white/[0.07] pt-4">
              <AgentPipeline stage={stage} latencies={lastRecord?.stage_latency_ms ?? {}} />
            </div>
          </Panel>

          <div className="grid gap-4 sm:grid-cols-3">
            <Tile label="Auto-remediated" value={counters.auto_remediated ?? 0} tone="text-pass" />
            <Tile label="Awaiting operator" value={counters.human_in_loop ?? 0} tone="text-hold" />
            <Tile label="Escalated" value={counters.escalations ?? 0} tone="text-block" />
          </div>
        </div>

        {/* RIGHT — what it is working on */}
        <div className="space-y-4">
          <Panel title="Current incident" icon={Activity}>
            {latest ? (
              <button
                type="button"
                onClick={() => onOpenIncident?.(latest.incident_id)}
                className="w-full text-left"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="num text-[13px] font-semibold text-slate-100">
                    {latest.incident_id}
                  </span>
                  <Badge
                    tone={
                      latest.status === 'AUTO-REMEDIATED'
                        ? 'pass'
                        : latest.status === 'HUMAN-IN-LOOP'
                          ? 'hold'
                          : 'block'
                    }
                  >
                    {statusStyle(latest.status).label}
                  </Badge>
                </div>
                <p className="mt-1 text-[12px] text-slate-400">{latest.fault_label}</p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-[12px]">
                  <Cell label="Anomaly" value={fmt.num(latest.anomaly_score)} />
                  <Cell label="Threshold" value={fmt.num(latest.threshold)} />
                  <Cell label="Confidence" value={fmt.pct(latest.confidence)} />
                  <Cell label="Action" value={latest.selected_label ?? 'none'} />
                </dl>
                <div className="mt-3">
                  <BeliefBar belief={latest.belief} />
                </div>
              </button>
            ) : (
              <p className="text-[13px] text-slate-500">
                No incident is open. Run self-healing to put the agent to work.
              </p>
            )}
          </Panel>

          <Panel title="Active incidents" icon={Radio} meta={`${incidents?.length ?? 0} total`}>
            {incidents?.length ? (
              <ul className="space-y-2">
                {incidents.slice(0, 5).map((inc) => (
                  <li key={inc.incident_id}>
                    <button
                      type="button"
                      onClick={() => onOpenIncident?.(inc.incident_id)}
                      className="glass-quiet flex w-full items-center gap-2.5 px-3 py-2 text-left hover:border-white/20"
                    >
                      <span
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusStyle(inc.status).dot}`}
                        aria-hidden
                      />
                      <span className="num min-w-0 flex-1 truncate text-[12px] text-slate-200">
                        {inc.incident_id}
                      </span>
                      <span className="num shrink-0 text-[11px] text-slate-500">
                        {fmt.num(inc.anomaly_score, 1)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-slate-500">The estate is quiet.</p>
            )}
          </Panel>

          <Panel title="Recent agent activity" icon={Cpu}>
            <ActivityFeed items={activity} onSelect={onOpenIncident} />
          </Panel>
        </div>
      </div>
    </div>
  )
}

function Tile({ label, value, tone }) {
  return (
    <div className="glass px-4 py-3.5">
      <p className="label">{label}</p>
      <p className={`num mt-1 text-2xl font-semibold ${tone}`}>{value}</p>
    </div>
  )
}

function Cell({ label, value }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="num mt-0.5 truncate text-slate-200">{value}</dd>
    </div>
  )
}

function Sparkline({ label, samples, featureKey, color, digits }) {
  const last = samples.at(-1)?.features?.[featureKey]
  // Same contracting window as the full charts, so every channel on the
  // screen is showing the same stretch of time.
  const view = samples.slice(-dynamicWindow(samples))
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="label">{label}</span>
        <span className="num text-[12px] text-slate-200">{fmt.num(last, digits)}</span>
      </div>
      <MiniTelemetry samples={view} featureKey={featureKey} color={color} height={38} />
    </div>
  )
}
