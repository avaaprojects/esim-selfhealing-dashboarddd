import { useMemo, useState } from 'react'
import { Loader2, Play, Radio, Square, Zap } from 'lucide-react'
import AgentPipeline from '../components/AgentPipeline'
import ActivityFeed from '../components/ActivityFeed'
import { AnomalyChart, FeatureChart } from '../components/TelemetryChart'
import { Badge, Field, Notice, Panel } from '../components/Primitives'
import { fmt, statusStyle } from '../data/constants'

/**
 * REAL-TIME DATA.
 *
 * `realtime` is polled from /api/realtime/status (the feed's own counters);
 * `telemetry`/`status`/`incidents` are the same session-wide reads Overview
 * uses, because a live sample goes through the identical `orch.monitor` /
 * `orch.actuator` objects a simulation run does — there is no separate
 * "real-time" copy of the agent state to keep in sync.
 */
export default function RealTime({
  realtime,
  status,
  telemetry,
  incidents,
  activity,
  isOwner,
  onStart,
  onStop,
  faultClasses,
  onInject,
  error,
}) {
  const [busy, setBusy] = useState(false)
  const [injectFault, setInjectFault] = useState(faultClasses?.[0]?.key ?? '')
  const [injectDevice, setInjectDevice] = useState('')
  const [injecting, setInjecting] = useState(false)

  const running = Boolean(realtime?.running)
  const samples = telemetry?.samples ?? []
  const liveIncidents = useMemo(
    () => (incidents ?? []).filter((i) => i.source === 'realtime'),
    [incidents],
  )
  const liveActivity = useMemo(
    () => (activity ?? []).filter((a) => ['realtime', 'telemetry', 'incident'].includes(a.kind)),
    [activity],
  )

  const toggle = async () => {
    setBusy(true)
    try {
      if (running) await onStop()
      else await onStart()
    } finally {
      setBusy(false)
    }
  }

  const inject = async () => {
    if (!injectFault) return
    setInjecting(true)
    try {
      await onInject(injectFault, injectDevice || undefined)
    } finally {
      setInjecting(false)
    }
  }

  return (
    <div className="space-y-5">
      {error && (
        <Notice tone="block" title="The backend is not responding">
          Start it from the project root with{' '}
          <code className="num text-slate-300">python -m uvicorn backend.app:app --port 8000</code>.
        </Notice>
      )}

      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        {/* LEFT — connection + controls */}
        <div className="space-y-4">
          <Panel title="Real-Time Data" icon={Radio}>
            <div className="flex items-center gap-2.5">
              <span
                className={`h-2 w-2 rounded-full ${running ? 'animate-corepulse bg-pass' : 'bg-slate-600'}`}
                aria-hidden
              />
              <p className={`num text-[12.5px] font-semibold ${running ? 'text-pass' : 'text-slate-400'}`}>
                {running ? 'SERVER CONNECTED' : 'STOPPED'}
              </p>
            </div>
            <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500">
              Source: {realtime?.source_label ?? 'Prototype Server / Grid Telemetry'} — a genuine
              backend generator feeding the real agent loop, not a telecom operator connection.
            </p>

            <div className="mt-4 divide-y divide-white/[0.05]">
              <Field label="Samples received" value={realtime?.samples_received ?? 0} />
              <Field label="Latest update" value={fmt.clock(realtime?.last_ts)} />
              <Field label="Live devices" value={(realtime?.devices ?? []).length} />
              <Field label="Tick interval" value={`${fmt.num(realtime?.tick_seconds, 1)} s`} />
              <Field
                label="Current state"
                value={status?.system_health?.state ?? '—'}
                tone={status?.system_health?.state === 'HEALTHY' ? 'text-pass' : 'text-hold'}
              />
              <Field label="Agent" value={running ? 'MONITORING' : 'IDLE'} />
            </div>

            {isOwner ? (
              <button
                type="button"
                onClick={toggle}
                disabled={busy}
                className={[
                  'mt-4 flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-[12.5px] font-semibold transition-all',
                  running
                    ? 'bg-block/15 text-block ring-1 ring-block/30 hover:bg-block/25'
                    : 'bg-agent/15 text-agent ring-1 ring-agent/30 hover:bg-agent/25',
                ].join(' ')}
              >
                {busy ? (
                  <Loader2 size={14} className="animate-spin" aria-hidden />
                ) : running ? (
                  <Square size={13} aria-hidden />
                ) : (
                  <Play size={13} aria-hidden />
                )}
                {running ? 'Stop real-time data' : 'Start real-time data'}
              </button>
            ) : (
              <p className="mt-4 text-[11.5px] text-slate-500">
                Viewer access — starting or stopping the feed requires an OWNER session.
              </p>
            )}
          </Panel>

          {isOwner && (
            <Panel title="Inject fault" meta="Simulation / demo only" icon={Zap}>
              <p className="mb-3 text-[11.5px] leading-relaxed text-slate-500">
                Feeds a known synthetic fault signature into the live stream and lets the real
                agent detect and remediate it. Labelled clearly because this is the one place the
                real-time feed is deliberately perturbed rather than left to drift naturally.
              </p>
              <div className="space-y-2.5">
                <label className="block">
                  <span className="label mb-1 block">Fault class</span>
                  <select
                    value={injectFault}
                    onChange={(e) => setInjectFault(e.target.value)}
                    className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[12.5px] text-slate-100 outline-none focus:border-agent/40"
                  >
                    {(faultClasses ?? []).map((f) => (
                      <option key={f.key} value={f.key}>{f.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="label mb-1 block">Target device (optional)</span>
                  <select
                    value={injectDevice}
                    onChange={(e) => setInjectDevice(e.target.value)}
                    className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[12.5px] text-slate-100 outline-none focus:border-agent/40"
                  >
                    <option value="">first live device</option>
                    {(realtime?.devices ?? []).map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  onClick={inject}
                  disabled={!running || injecting || !injectFault}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-hold/15 px-4 py-2.5 text-[12.5px] font-semibold text-hold ring-1 ring-hold/30 transition-colors hover:bg-hold/25 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {injecting && <Loader2 size={13} className="animate-spin" aria-hidden />}
                  Inject anomaly
                </button>
                {!running && (
                  <p className="text-[11px] text-slate-600">Start real-time data first.</p>
                )}
              </div>
            </Panel>
          )}
        </div>

        {/* RIGHT — the feed actually flowing through the agent */}
        <div className="space-y-4">
          <Panel title="Anomaly score vs threshold" icon={Radio} meta={`${samples.length} samples`}>
            <AnomalyChart samples={samples} />
          </Panel>

          <div className="grid gap-4 md:grid-cols-2">
            <Panel title="RSRP" dense>
              <FeatureChart samples={samples} featureKey="rsrp_dbm" color="#F472B6" unit="dBm" />
            </Panel>
            <Panel title="OTA failure rate" dense>
              <FeatureChart samples={samples} featureKey="ota_fail_rate" color="#8B5CF6" />
            </Panel>
          </div>

          <Panel title="Live incidents (real-time source)" meta={`${liveIncidents.length} total`}>
            {liveIncidents.length ? (
              <ul className="space-y-2">
                {liveIncidents.slice(0, 6).map((inc) => (
                  <li key={inc.incident_id} className="glass-quiet flex items-center gap-2.5 px-3 py-2">
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusStyle(inc.status).dot}`} aria-hidden />
                    <span className="num min-w-0 flex-1 truncate text-[11.5px] text-slate-200">
                      {inc.incident_id}
                    </span>
                    <Badge tone={inc.status === 'AUTO-REMEDIATED' ? 'pass' : inc.status === 'HUMAN-IN-LOOP' ? 'hold' : 'block'}>
                      {statusStyle(inc.status).label}
                    </Badge>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-slate-500">
                {running
                  ? 'Streaming nominal telemetry — no anomaly has crossed the detector threshold yet.'
                  : 'No real-time incidents this session.'}
              </p>
            )}
          </Panel>

          <Panel title="Agent pipeline" meta="OBSERVE → LEARN">
            <AgentPipeline stage={null} orientation="horizontal" />
          </Panel>

          <Panel title="Live event feed">
            <ActivityFeed items={liveActivity} limit={12} />
          </Panel>
        </div>
      </div>
    </div>
  )
}
