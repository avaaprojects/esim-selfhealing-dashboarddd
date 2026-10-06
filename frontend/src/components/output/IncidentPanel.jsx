import { useEffect, useState } from 'react'
import { AlertTriangle, Loader2, Radio, Waves } from 'lucide-react'
import { Badge, Field, Notice, Panel } from '../Primitives'
import MetricCard from '../MetricCard'
import { BeliefBar } from '../IncidentCard'
import { AnomalyChart, FeatureChart } from '../TelemetryChart'
import { Item, SELECT_CLASS, TypeBadge } from '../input/parts'
import { SeverityBadge } from './parts'
import { api } from '../../services/api'
import { FEATURE_LABELS, fmt } from '../../data/constants'

const FEATURE_COLORS = {
  aka_fail_rate: '#22D3EE',
  rsrp_dbm: '#F472B6',
  drop_rate: '#34D399',
  latency_ms: '#FBBF24',
  ota_fail_rate: '#8B5CF6',
}

const STATE_TONE = { pass: 'pass', hold: 'hold', block: 'block' }

/**
 * Stage 3. The current incident (id, client, device / eSIM, severity, detection
 * time, state), what the detector saw, and the device's own telemetry with the
 * moment of detection marked. With no incident it says so plainly: a stored
 * dataset that never fired is a result, and a live feed is still being watched.
 */
export default function IncidentPanel({ view, onSelectIncident, onInject, faultClasses = [], injecting }) {
  const { incident, trace, run, commit } = view
  const live = run.mode === 'realtime'
  const device = commit.resolved.device

  if (!incident) {
    return (
      <div className="space-y-4">
        <Notice title={live ? 'Monitoring: no incident yet' : 'No incident was detected'}>
          {live
            ? 'The live feed is being watched by the same detector the stored datasets use. If a fault appears, the incident opens here.'
            : `The detector processed ${run.samples} samples from this dataset and never crossed its threshold, so the agent had nothing to diagnose or fix.`}
        </Notice>
        {live && onInject && <InjectDemo device={device} faultClasses={faultClasses} injecting={injecting} onInject={onInject} />}
        <TelemetryPanel view={view} incidentId={null} live={live} />
      </div>
    )
  }

  const inc = trace.incident
  return (
    <div className="space-y-4">
      {view.incidents.length > 1 && (
        <label className="block max-w-md">
          <span className="label mb-1 block">This run opened {view.incidents.length} incidents</span>
          <select
            value={view.selected_incident_id}
            onChange={(e) => onSelectIncident(e.target.value)}
            className={SELECT_CLASS}
          >
            {view.incidents.map((i) => (
              <option key={i.incident_id} value={i.incident_id}>
                {i.incident_id} · {i.fault_label} · {i.status}
              </option>
            ))}
          </select>
        </label>
      )}

      <Panel dense>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="label">Incident</p>
            <p className="num text-[22px] font-semibold leading-tight text-slate-50">{incident.incident_id}</p>
            <p className="mt-0.5 text-[13.5px] text-slate-300">{incident.fault_label}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge level={incident.severity.level} />
            <Badge tone={STATE_TONE[incident.state.tone]}>{incident.state.label}</Badge>
          </div>
        </div>

        <dl className="mt-5 grid gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-4">
          <Item label="Client">
            <span className="font-medium">{incident.client?.name ?? 'Built-in scenario'}</span>
            <span className="block text-[12px] text-slate-500">{incident.group?.name}</span>
          </Item>
          <Item label="Device / eSIM">
            {incident.device?.device_label ?? incident.device?.euicc_id ?? 'Unregistered device'}
            <span className="num block break-all text-[12px] text-slate-500">{incident.device?.euicc_id}</span>
            <span className="num block text-[12px] text-slate-500">ICCID {incident.device?.iccid ?? 'n/a'}</span>
          </Item>
          <Item label="Detected at">
            <span className="num">{fmt.datetimeSec(incident.detected_at)}</span>
            <span className="block text-[12px] text-slate-500">
              {incident.detected_basis === 'live' ? 'live clock' : 'time in the dataset (synthetic timeline)'}
            </span>
          </Item>
          <Item label="Cell · source">
            <span className="num">{incident.cell_id}</span>
            <span className="mt-1 block">
              <TypeBadge typeKey={commit.source.mode} label={commit.source.type} />
            </span>
          </Item>
        </dl>

        <p className="mt-4 border-t border-white/[0.07] pt-3 text-[12px] leading-relaxed text-slate-500">
          <span className="text-slate-400">How severity is derived:</span> {incident.severity.basis}. It is computed from the
          detector's own numbers, not assigned by hand.
        </p>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="grid grid-cols-2 gap-3">
          <MetricCard
            label="Anomaly score g_t"
            value={fmt.num(inc.anomaly_score, 2)}
            hint={`detection threshold ${fmt.num(inc.threshold, 2)} (chi-square)`}
            meter={{ value: Math.min(inc.anomaly_score, inc.threshold * 3), max: inc.threshold * 3, limit: inc.threshold }}
            tone="block"
          />
          <MetricCard label="Exceedance" value={`${fmt.num(inc.exceedance, 1)}×`} hint="score against the threshold at onset" tone="hold" />
          <div className="glass-quiet col-span-2 px-3.5 py-3">
            <p className="label mb-2">Signals that moved most at detection</p>
            {view.top_features.length === 0 && <p className="text-[12px] text-slate-500">Not available for this incident.</p>}
            <ul className="space-y-1.5">
              {view.top_features.map((f) => (
                <li key={f.feature} className="flex items-center justify-between gap-3 text-[12.5px]">
                  <span className="text-slate-300">{FEATURE_LABELS[f.feature]?.label ?? f.feature}</span>
                  <span className="num text-slate-400">z = {f.z > 0 ? '+' : ''}{fmt.num(f.z, 1)}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <div className="glass-quiet px-4 py-3">
          <p className="label mb-2">Detector's belief over fault classes</p>
          <BeliefBar belief={inc.belief} />
        </div>
      </div>

      <TelemetryPanel view={view} incidentId={view.selected_incident_id} live={live} />
    </div>
  )
}

function InjectDemo({ device, faultClasses, injecting, onInject }) {
  const [fault, setFault] = useState(faultClasses[0]?.key ?? 'isdp_corruption')
  return (
    <Panel title="Try a fault on this device" icon={AlertTriangle} meta="simulation / demo only">
      <p className="mb-3 max-w-[64ch] text-[12.5px] leading-relaxed text-slate-400">
        The live feed is a prototype generator, so nothing will go wrong on its own. Inject a known synthetic fault into{' '}
        <span className="num text-slate-300">{device?.euicc_id}</span> and the real agent will detect, decide and act on it.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block min-w-[220px]">
          <span className="label mb-1 block">Fault class</span>
          <select value={fault} onChange={(e) => setFault(e.target.value)} className={SELECT_CLASS}>
            {faultClasses.map((f) => (
              <option key={f.key} value={f.key}>{f.label}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={injecting}
          onClick={() => onInject(fault, device?.euicc_id)}
          className="flex items-center gap-2 rounded-xl bg-hold/15 px-4 py-2 text-[13px] font-semibold text-hold ring-1 ring-hold/30 hover:bg-hold/25 disabled:opacity-50"
        >
          {injecting && <Loader2 size={13} className="animate-spin" aria-hidden />}
          Inject anomaly
        </button>
      </div>
    </Panel>
  )
}

/** Fetches this run's telemetry for the device, centred on the incident. Live runs keep refreshing. */
function TelemetryPanel({ view, incidentId, live }) {
  const { run, commit } = view
  const euiccId = commit.resolved.device?.euicc_id
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .deviceTelemetry({ euiccId, incidentId, runId: run.id, limit: 120 })
        .then((d) => !cancelled && (setData(d), setError(null)))
        .catch((e) => !cancelled && setError(e))
    load()
    const timer = live ? setInterval(load, 2000) : null
    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
    }
  }, [euiccId, incidentId, run.id, live])

  return <TelemetryView data={data} error={error} live={live} device={commit.resolved.device} source={commit.source} />
}

/** The charts and their labels. Pure: everything comes in through props. */
export function TelemetryView({ data, error, live, device, source }) {
  const samples = data?.samples ?? []
  const latest = samples.at(-1)

  return (
    <Panel
      title="Telemetry"
      icon={live ? Radio : Waves}
      meta={
        <span className="flex items-center gap-2">
          {live && <Badge tone="agent">Live · refreshing</Badge>}
          <TypeBadge typeKey={source.mode} label={source.type} />
        </span>
      }
    >
      <p className="mb-3 text-[12px] text-slate-500">
        eUICC <span className="num text-slate-400">{device?.euicc_id}</span> · {device?.device_label}
        {data?.source ? <> · {data.source}</> : null}
      </p>
      {error && <p className="text-[12.5px] text-block">Telemetry could not be loaded: {error.message}</p>}
      {!error && !data && (
        <p className="flex items-center gap-2 py-6 text-[12.5px] text-slate-500">
          <Loader2 size={13} className="animate-spin" aria-hidden /> Loading telemetry
        </p>
      )}
      {data && samples.length === 0 && (
        <p className="text-[13px] leading-relaxed text-slate-500">
          No samples have arrived for this device yet. They appear here as the feed delivers them.
        </p>
      )}
      {samples.length > 0 && (
        <>
          <p className="label mb-1">Anomaly score g_t against the detection threshold</p>
          <AnomalyChart samples={samples} markerIndex={data.marker_index ?? null} />
          <p className="mt-1 text-[11px] text-slate-500">
            Red dashed line: the chi-square threshold that opens an incident.
            {data.marker_index !== null && data.marker_index !== undefined ? ' Amber dashed line: the sample that opened this incident.' : ''}{' '}
            {samples.length} samples.
          </p>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(data.features ?? []).map((f) => (
              <div key={f.key} className="glass-quiet p-3">
                <div className="mb-1 flex items-baseline justify-between gap-2">
                  <p className="label">{f.label}</p>
                  <p className="num text-[12px] text-slate-200">
                    {fmt.num(latest?.features?.[f.key], f.precision ?? 2)}{' '}
                    <span className="text-[10.5px] text-slate-500">{f.unit}</span>
                  </p>
                </div>
                <FeatureChart samples={samples} featureKey={f.key}
                              color={FEATURE_COLORS[f.key] ?? '#22D3EE'} height={92}
                              unit={f.unit} band />
                <p className="mt-0.5 text-[10.5px] text-slate-600">{f.worse === 'down' ? 'lower is worse' : 'higher is worse'}</p>
              </div>
            ))}
            <div className="glass-quiet p-3">
              <p className="label mb-1">Latest sample</p>
              <dl className="divide-y divide-white/[0.05]">
                <Field label="Time" value={fmt.clock(latest?.ts)} />
                <Field label="Score g_t" value={fmt.num(latest?.score, 2)} tone={latest?.score > latest?.threshold ? 'text-block' : ''} />
                <Field label="Threshold" value={fmt.num(latest?.threshold, 2)} />
              </dl>
            </div>
          </div>
        </>
      )}
    </Panel>
  )
}
