import { useCallback, useEffect, useState } from 'react'
import { Activity, ChevronRight, Cpu, Waves } from 'lucide-react'
import { Badge, Field, Notice, Panel, Spinner } from '../components/Primitives'
import { AnomalyChart, FeatureChart } from '../components/TelemetryChart'
import RelatedReports from '../components/RelatedReports'
import { api } from '../services/api'
import { clientReports, deviceState, estate, fmt, statusStyle } from '../data/constants'

const FEATURE_COLORS = {
  aka_fail_rate: '#22D3EE',
  rsrp_dbm: '#F472B6',
  drop_rate: '#34D399',
  latency_ms: '#FBBF24',
  ota_fail_rate: '#8B5CF6',
}

const POLL_MS = 4000

function StateBadge({ state }) {
  const st = deviceState(state)
  return (
    <Badge tone={st.tone}>
      <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} aria-hidden />
      {st.label}
    </Badge>
  )
}

/**
 * The devices the dashboard already tracks — the real-time grid, every eUICC
 * that has streamed telemetry, and every eUICC an incident was opened on —
 * with each one's state, its own telemetry, its incidents and the client
 * reports about it. This is where "View device" on a report lands.
 */
export default function Devices({
  devices = [],
  selectedId,
  focusIncident,
  onSelect,
  onOpenIncident,
  onOpenReport,
  onReportIssue,
  error,
}) {
  // On a wide screen the detail sits beside the list, so start on the first device.
  useEffect(() => {
    if (selectedId || !devices.length) return
    if (typeof window !== 'undefined' && window.matchMedia?.('(min-width: 1280px)').matches) {
      onSelect?.(devices[0].euicc_id)
    }
  }, [selectedId, devices, onSelect])

  if (error) {
    return (
      <Notice tone="block" title="Devices are unavailable">
        The backend is not responding.
      </Notice>
    )
  }

  if (!devices.length) {
    return (
      <Notice title="No devices yet">
        Devices appear once telemetry flows: run self-healing from the sidebar, or start real-time
        data.
      </Notice>
    )
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
      <div className="space-y-3">
        {devices.map((d) => (
          <DeviceCard key={d.euicc_id} device={d} active={d.euicc_id === selectedId} onOpen={onSelect} />
        ))}
      </div>

      <div className="scroll-mt-20">
        {selectedId ? (
          <DeviceDetail
            key={selectedId}
            euiccId={selectedId}
            initialIncident={focusIncident}
            onOpenIncident={onOpenIncident}
            onOpenReport={onOpenReport}
            onReportIssue={onReportIssue}
          />
        ) : (
          <Panel title="Device" icon={Cpu}>
            <p className="text-[13px] text-slate-500">Select a device to see its state, telemetry and reports.</p>
          </Panel>
        )}
      </div>
    </div>
  )
}

function DeviceCard({ device, active, onOpen }) {
  return (
    <button
      type="button"
      onClick={() => onOpen?.(device.euicc_id)}
      aria-current={active ? 'true' : undefined}
      className={[
        'glass w-full p-4 text-left transition-colors',
        active ? 'border-agent/40 bg-agent/[0.06]' : 'hover:border-white/20',
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold text-slate-100" title={device.euicc_id}>
            {estate.device(device.context, device.euicc_id)}
          </p>
          {estate.client(device.context) && (
            <p className="truncate text-[12px] text-slate-400">{estate.client(device.context)}</p>
          )}
          <p className="num mt-0.5 truncate text-[11.5px] text-slate-500">
            {device.euicc_id} · {device.cell_id ?? 'no cell yet'} ·{' '}
            {device.source === 'realtime' ? 'real-time grid' : 'simulation'}
          </p>
        </div>
        <StateBadge state={device.state} />
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-x-4">
        <div>
          <dt className="label">Incidents</dt>
          <dd className="num text-[13px] text-slate-100">
            {device.incident_count}
            {device.open_incidents > 0 && <span className="text-hold"> · {device.open_incidents} open</span>}
          </dd>
        </div>
        <div>
          <dt className="label">Samples</dt>
          <dd className="num text-[13px] text-slate-100">{device.samples}</dd>
        </div>
        <div>
          <dt className="label">Reports</dt>
          <dd className="num text-[13px] text-slate-100">{device.report_count ?? 0}</dd>
        </div>
      </dl>
    </button>
  )
}

function DeviceDetail({ euiccId, initialIncident, onOpenIncident, onOpenReport, onReportIssue }) {
  const [detail, setDetail] = useState(null)
  const [telemetry, setTelemetry] = useState(null)
  const [error, setError] = useState(null)
  // The incident the telemetry window is centred on; cleared by "Show latest".
  const [focus, setFocus] = useState(initialIncident ?? null)

  useEffect(() => {
    setFocus(initialIncident ?? null)
  }, [initialIncident])

  const load = useCallback(async () => {
    try {
      const [d, t] = await Promise.all([
        api.device(euiccId),
        api.deviceTelemetry({ euiccId, incidentId: focus, limit: 120 }).catch(() =>
          // The focused incident may have gone; fall back to the latest window.
          api.deviceTelemetry({ euiccId, limit: 120 }),
        ),
      ])
      setDetail(d)
      setTelemetry(t)
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [euiccId, focus])

  useEffect(() => {
    load()
    // A focused incident window is a fixed slice of history; only the live
    // "latest" view needs to keep moving.
    if (focus) return undefined
    const timer = setInterval(load, POLL_MS)
    return () => clearInterval(timer)
  }, [load, focus])

  if (error && !detail) {
    return (
      <Notice tone="block" title="This device is not available">
        {error.status === 404
          ? 'It is no longer in the running session (the agent session was reset or the server restarted).'
          : error.message}
      </Notice>
    )
  }
  if (!detail) return <Spinner label="Loading device" />

  const samples = telemetry?.samples ?? []
  const latest = detail.latest_reading
  const euicc = detail.euicc

  return (
    <div className="space-y-4">
      <Panel title="Device" icon={Cpu} meta={<StateBadge state={detail.state} />}>
        <p className="text-[15px] font-semibold text-slate-100">
          {estate.device(detail.context, detail.euicc_id)}
        </p>
        <p className="num mt-0.5 break-all text-[12px] text-slate-500">{detail.euicc_id}</p>
        <div className="mt-3 divide-y divide-white/[0.05]">
          {detail.context?.client_name && (
            <Field label="Client" mono={false} value={estate.client(detail.context)} />
          )}
          {detail.context?.iccid && <Field label="ICCID" value={detail.context.iccid} />}
          {detail.context?.profile_name && (
            <Field label="Profile" mono={false} value={detail.context.profile_name} />
          )}
          {detail.context?.network_name && (
            <Field
              label="Network"
              mono={false}
              value={`${detail.context.network_name}${detail.context.plmn ? ` · PLMN ${detail.context.plmn}` : ''}`}
            />
          )}
          <Field label="Cell" value={detail.cell_id ?? '—'} />
          <Field
            label="Source"
            mono={false}
            value={detail.source === 'realtime' ? 'Real-time grid' : 'Simulation'}
          />
          {detail.active_fault_label && <Field label="Fault seen" mono={false} value={detail.active_fault_label} />}
          <Field label="Samples received" value={detail.samples} />
          {latest && (
            <Field
              label="Latest anomaly score"
              value={`${fmt.num(latest.score)} / ${fmt.num(latest.threshold)}`}
              tone={latest.score > latest.threshold ? 'text-block' : ''}
            />
          )}
          {detail.fleet_size > 1 && <Field label="Shares a profile with" value={`${detail.fleet_size} devices`} />}
        </div>
        {onReportIssue && (
          <button
            type="button"
            onClick={() => onReportIssue({ deviceId: detail.euicc_id })}
            className="mt-4 flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium text-agent ring-1 ring-agent/25 hover:bg-agent/10"
          >
            Report an issue with this device
          </button>
        )}
      </Panel>

      {euicc && (
        <Panel title="eUICC state" meta="from the RSP client">
          <div className="divide-y divide-white/[0.05]">
            <Field label="Active ISD-P" value={euicc.active_isdp} />
            <Field label="Installed profiles" value={euicc.installed_isdp.length} />
            <Field
              label="Profile checksum"
              mono={false}
              value={euicc.checksum_ok ? 'OK' : 'Mismatch'}
              tone={euicc.checksum_ok ? 'text-pass' : 'text-block'}
            />
            <Field
              label="Key epoch (eUICC / SM-SR)"
              value={`${euicc.key_epoch} / ${euicc.smsr_key_epoch}`}
              tone={euicc.key_epoch === euicc.smsr_key_epoch ? '' : 'text-block'}
            />
            <Field
              label="SM-DP+ reachable"
              mono={false}
              value={euicc.smdp_reachable ? 'Yes' : 'No'}
              tone={euicc.smdp_reachable ? 'text-pass' : 'text-block'}
            />
            <Field label="Bearer APN" value={euicc.apn} />
            <Field
              label="Radio alarm"
              mono={false}
              value={euicc.radio_alarm ? 'Raised' : 'Clear'}
              tone={euicc.radio_alarm ? 'text-block' : 'text-pass'}
            />
            <Field label="Recent OTA results" value={euicc.last_ota_results.join(' · ')} />
          </div>
        </Panel>
      )}

      <Panel
        title="Telemetry"
        icon={Waves}
        meta={focus && telemetry?.marker_index !== null ? `around ${focus}` : `${samples.length} samples`}
      >
        {samples.length === 0 ? (
          <p className="text-[13px] leading-relaxed text-slate-500">
            No telemetry has been recorded for this device yet. It appears once the device streams
            samples — run a scenario, or start real-time data.
          </p>
        ) : (
          <>
            <AnomalyChart samples={samples} markerIndex={telemetry?.marker_index ?? null} />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>
                Anomaly score g_t against the chi-square threshold
                {focus && telemetry?.marker_index !== null && telemetry?.marker_index !== undefined
                  ? ' · dashed line marks when the incident opened'
                  : ''}
              </span>
              {focus && (
                <button
                  type="button"
                  onClick={() => setFocus(null)}
                  className="text-agent hover:underline"
                >
                  Show latest instead
                </button>
              )}
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {(telemetry?.features ?? []).map((f) => (
                <div key={f.key} className="glass-quiet p-3">
                  <p className="label mb-1">
                    {f.label} <span className="text-slate-600">· {f.unit}</span>
                  </p>
                  <FeatureChart
                    samples={samples}
                    featureKey={f.key}
                    color={FEATURE_COLORS[f.key] ?? '#22D3EE'}
                    height={100}
                    unit={f.unit}
                    band
                  />
                </div>
              ))}
            </div>
          </>
        )}
      </Panel>

      <Panel title="Incidents on this device" icon={Activity} meta={`${detail.incidents.length} total`}>
        {detail.incidents.length === 0 ? (
          <p className="text-[13px] text-slate-500">The agent has not opened an incident on this device.</p>
        ) : (
          <ul className="space-y-2">
            {detail.incidents.map((inc) => {
              const st = statusStyle(inc.status)
              return (
                <li key={inc.incident_id}>
                  <button
                    type="button"
                    onClick={() => onOpenIncident?.(inc.incident_id)}
                    className={[
                      'glass-quiet flex w-full items-center gap-3 px-3.5 py-3 text-left transition-colors hover:border-white/20',
                      inc.incident_id === focus ? 'border-agent/40 bg-agent/[0.06]' : '',
                    ].join(' ')}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="num text-[12.5px] font-semibold text-slate-100">{inc.incident_id}</p>
                      <p className="truncate text-[12px] text-slate-400">
                        {inc.fault_label} · {inc.selected_label ?? 'no feasible action'}
                        {inc.report_count > 0 && (
                          <span className="text-agent"> · {clientReports(inc.report_count)}</span>
                        )}
                      </p>
                    </div>
                    <Badge tone={inc.status === 'AUTO-REMEDIATED' ? 'pass' : inc.status === 'HUMAN-IN-LOOP' ? 'hold' : 'block'}>
                      <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} aria-hidden />
                      {st.label}
                    </Badge>
                    <ChevronRight size={14} className="shrink-0 text-slate-500" aria-hidden />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </Panel>

      <RelatedReports
        reports={detail.reports}
        onOpenReport={onOpenReport}
        onReportIssue={onReportIssue ? () => onReportIssue({ deviceId: detail.euicc_id }) : undefined}
        emptyText="No client reports mention this device."
      />
    </div>
  )
}
