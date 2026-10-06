import { useEffect, useState } from 'react'
import {
  Activity,
  Brain,
  ChevronRight,
  CircuitBoard,
  Cpu,
  Loader2,
  MessageSquareText,
  User,
  Waves,
} from 'lucide-react'
import { Badge, Field, Notice, Panel } from './Primitives'
import { ReportProgress, ReportStatusBadge } from './ReportParts'
import { AnomalyChart } from './TelemetryChart'
import { api } from '../services/api'
import { deviceState, fmt, statusStyle } from '../data/constants'

/**
 * One report, in full. The same component serves both roles:
 *
 *   CLIENT  reads the status, the owner's acknowledgement, and the owner's
 *           response / resolution note.
 *   OWNER   sees the same thing plus the response form, and can jump to the
 *           device, the incident and its telemetry.
 */
export default function ReportDetail({
  report,
  isOwner,
  onChanged,
  onOpenDevice,
  onOpenIncident,
  onOpenReasoning,
}) {
  if (!report) return null

  return (
    <div className="space-y-4">
      <Panel
        title={report.id}
        meta={<ReportStatusBadge status={report.status} />}
      >
        <ReportProgress report={report} />

        <div className="mt-5 border-t border-white/[0.07] pt-4">
          <p className="label mb-1.5">Message</p>
          <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-slate-200">{report.message}</p>
        </div>

        <div className="mt-4 divide-y divide-white/[0.05] border-t border-white/[0.07] pt-2">
          <Field label="Client" mono={false} value={report.client} />
          {report.customer && <Field label="Customer" mono={false} value={report.customer} />}
          <Field label="Submitted" mono={false} value={fmt.datetime(report.created_at)} />
        </div>
      </Panel>

      <OwnerResponse report={report} isOwner={isOwner} />

      <Panel title="Connected to" meta="report → agent → action">
        <ReportChain
          report={report}
          onOpenDevice={onOpenDevice}
          onOpenIncident={onOpenIncident}
          onOpenReasoning={onOpenReasoning}
        />
      </Panel>

      {report.device && <ReportTelemetry report={report} onOpenDevice={onOpenDevice} />}

      {isOwner && report.status !== 'RESOLVED' && <OwnerActions report={report} onChanged={onChanged} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// What the owner wrote back — the half of the loop the client used to never see
// ---------------------------------------------------------------------------
function OwnerResponse({ report, isOwner }) {
  const acknowledged = report.acknowledged_at !== null && report.acknowledged_at !== undefined
  const resolved = report.status === 'RESOLVED'

  return (
    <Panel title="Owner response" icon={MessageSquareText}>
      {!acknowledged && (
        <p className="text-[13px] leading-relaxed text-slate-400">
          {isOwner
            ? 'Not acknowledged yet. Acknowledge it below to let the client know it has been seen.'
            : 'The owner has not seen this yet. This page checks for a reply every few seconds, so you can leave it open or come back to My Reports later.'}
        </p>
      )}

      <div className="space-y-4">
        {acknowledged && (
          <ResponseBlock
            tone="agent"
            title="Acknowledged"
            by={report.acknowledged_by}
            at={report.acknowledged_at}
            note={report.response_note}
            emptyNote="No note was added."
          />
        )}
        {resolved && (
          <ResponseBlock
            tone="pass"
            title="Resolved"
            by={report.resolved_by}
            at={report.resolved_at}
            note={report.resolution_note}
            emptyNote="No resolution note was added."
          />
        )}
        {acknowledged && !resolved && (
          <p className="text-[12px] text-slate-500">
            {isOwner ? 'Resolve it when the work is done.' : 'The owner is working on it. A resolution note will appear here.'}
          </p>
        )}
      </div>
    </Panel>
  )
}

function ResponseBlock({ tone, title, by, at, note, emptyNote }) {
  const bar = tone === 'pass' ? 'border-pass/40' : 'border-agent/40'
  return (
    <div className={`border-l-2 ${bar} pl-3.5`}>
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <Badge tone={tone}>{title}</Badge>
        <span className="text-[12px] text-slate-400">
          by {by ?? 'owner'} · <span className="num">{fmt.datetime(at)}</span>
        </span>
      </div>
      {note ? (
        <p className="mt-2 whitespace-pre-wrap text-[13.5px] leading-relaxed text-slate-200">{note}</p>
      ) : (
        <p className="mt-2 text-[12.5px] text-slate-500">{emptyNote}</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// CLIENT REPORT → CLIENT → DEVICE → INCIDENT → TELEMETRY → AGENT → ACTION
// ---------------------------------------------------------------------------
function ReportChain({ report, onOpenDevice, onOpenIncident, onOpenReasoning }) {
  const { device, incident } = report
  const st = incident ? statusStyle(incident.status) : null
  const dev = device ? deviceState(device.state) : null
  const liveIncident = incident?.live

  const outcome = !incident
    ? null
    : incident.dispatched && incident.success
      ? 'dispatched and verified'
      : incident.dispatched
        ? 'dispatched, fault not cleared'
        : (st?.label ?? '').toLowerCase()

  const steps = [
    {
      key: 'client',
      icon: User,
      title: 'Client',
      active: true,
      body: report.customer ? `${report.client} · ${report.customer}` : report.client,
    },
    {
      key: 'device',
      icon: Cpu,
      title: 'Device',
      active: Boolean(device),
      body: device ? (
        <>
          <span className="num">{device.euicc_id}</span>
          {device.live && dev && <span className="text-slate-400"> · {dev.label}</span>}
        </>
      ) : (
        'No device selected'
      ),
      detail: device ? (device.cell_id ? `cell ${device.cell_id}` : null) : null,
      action: device && device.live && {
        label: 'View device',
        run: () => onOpenDevice?.(device.euicc_id, report.incident_id),
      },
    },
    {
      key: 'incident',
      icon: Activity,
      title: 'Incident',
      active: Boolean(incident),
      body: incident ? (
        <>
          <span className="num">{incident.incident_id}</span>
          {incident.fault_label && <span className="text-slate-400"> · {incident.fault_label}</span>}
        </>
      ) : (
        'No incident selected'
      ),
      detail: incident ? st?.label : null,
      action: incident && liveIncident && {
        label: 'View incident',
        run: () => onOpenIncident?.(incident.incident_id),
      },
    },
    {
      key: 'telemetry',
      icon: Waves,
      title: 'Telemetry',
      active: Boolean(device?.live),
      body: device?.live
        ? incident?.live
          ? 'Anomaly score around the moment the incident opened'
          : 'Recent anomaly score for this device'
        : 'Available while the device is in the running session',
      action: device?.live && {
        label: 'View telemetry',
        run: () => onOpenDevice?.(device.euicc_id, report.incident_id),
      },
    },
    {
      key: 'agent',
      icon: Brain,
      title: 'Self-healing agent',
      active: Boolean(incident),
      body: incident
        ? incident.fault_label
          ? `Diagnosed ${incident.fault_label}${
              incident.confidence !== null && incident.confidence !== undefined
                ? ` at ${fmt.pct(incident.confidence)} confidence`
                : ''
            }`
          : 'Diagnosis recorded'
        : 'Nothing to show without an incident',
      action: incident && liveIncident && {
        label: 'View reasoning',
        run: () => onOpenReasoning?.(incident.incident_id),
      },
    },
    {
      key: 'action',
      icon: CircuitBoard,
      title: 'Remedial action',
      active: Boolean(incident),
      body: incident ? (
        <>
          {incident.selected_label ?? 'No feasible action'}
          {outcome && <span className="text-slate-400"> · {outcome}</span>}
        </>
      ) : (
        'Nothing to show without an incident'
      ),
    },
  ]

  return (
    <div>
      {!device && !incident && (
        <p className="mb-3 text-[12.5px] text-slate-500">
          This report was not linked to a device or an incident, so there is nothing further along the
          chain.
        </p>
      )}
      {(device && !device.live) || (incident && !incident.live) ? (
        <div className="mb-4">
          <Notice title="Showing what was recorded when this report was sent">
            The {incident && !incident.live ? 'incident' : 'device'} is no longer in the running
            session (the agent session was reset or the server restarted), so live state and links
            are unavailable.
          </Notice>
        </div>
      ) : null}

      <ol>
        {steps.map((step, i) => {
          const Icon = step.icon
          return (
            <li key={step.key} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span
                  className={[
                    'grid h-7 w-7 shrink-0 place-items-center rounded-full ring-1',
                    step.active
                      ? 'bg-agent/10 text-agent ring-agent/25'
                      : 'bg-white/[0.03] text-slate-600 ring-white/10',
                  ].join(' ')}
                >
                  <Icon size={13} aria-hidden />
                </span>
                {i < steps.length - 1 && <span className="my-1 w-px flex-1 bg-white/10" aria-hidden />}
              </div>
              <div className="min-w-0 flex-1 pb-4">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <p className="label">{step.title}</p>
                  {step.action && (
                    <button
                      type="button"
                      onClick={step.action.run}
                      className="flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11.5px] font-medium text-agent ring-1 ring-agent/25 hover:bg-agent/10"
                    >
                      {step.action.label}
                      <ChevronRight size={12} aria-hidden />
                    </button>
                  )}
                </div>
                <p className={`mt-0.5 break-words text-[13px] ${step.active ? 'text-slate-100' : 'text-slate-500'}`}>
                  {step.body}
                </p>
                {step.detail && <p className="mt-0.5 text-[11.5px] text-slate-500">{step.detail}</p>}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

// ---------------------------------------------------------------------------
// The device's own telemetry, centred on the incident when there is one
// ---------------------------------------------------------------------------
function ReportTelemetry({ report, onOpenDevice }) {
  const deviceId = report.device?.euicc_id
  const live = Boolean(report.device?.live)
  const incidentId = report.incident?.live ? report.incident.incident_id : null

  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!live) return undefined
    let cancelled = false
    setData(null)
    setError(null)
    api
      .deviceTelemetry({ euiccId: deviceId, incidentId, limit: 90 })
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err))
    return () => {
      cancelled = true
    }
  }, [live, deviceId, incidentId])

  if (!live) return null

  const samples = data?.samples ?? []

  return (
    <Panel
      title="Related telemetry"
      icon={Waves}
      meta={
        <button
          type="button"
          onClick={() => onOpenDevice?.(deviceId, report.incident_id)}
          className="flex items-center gap-0.5 text-agent hover:underline"
        >
          Open device <ChevronRight size={12} aria-hidden />
        </button>
      }
    >
      {error && <p className="text-[12.5px] text-block">Telemetry could not be loaded: {error.message}</p>}
      {!error && !data && (
        <p className="flex items-center gap-2 py-4 text-[12.5px] text-slate-500">
          <Loader2 size={13} className="animate-spin" aria-hidden /> Loading telemetry
        </p>
      )}
      {data && samples.length === 0 && (
        <p className="text-[12.5px] leading-relaxed text-slate-500">
          No telemetry has been recorded for this device yet. It appears once the device streams
          samples — run a scenario, or start real-time data.
        </p>
      )}
      {samples.length > 0 && (
        <>
          <AnomalyChart samples={samples} height={170} markerIndex={data.marker_index} />
          <p className="mt-2 text-[11px] text-slate-500">
            Anomaly score g_t against the chi-square threshold · {samples.length} samples
            {data.marker_index !== null && data.marker_index !== undefined ? ' · dashed line marks the incident' : ''}
          </p>
        </>
      )}
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// OWNER: acknowledge / resolve, with an optional note the client will read
// ---------------------------------------------------------------------------
function OwnerActions({ report, onChanged }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(null) // 'acknowledge' | 'resolve'
  const [error, setError] = useState(null)

  useEffect(() => {
    setNote('')
    setError(null)
  }, [report.id])

  const act = async (kind) => {
    setBusy(kind)
    setError(null)
    try {
      const updated =
        kind === 'acknowledge'
          ? await api.acknowledgeReport(report.id, note.trim())
          : await api.resolveReport(report.id, note.trim())
      setNote('')
      onChanged?.(updated)
    } catch (err) {
      setError(err.message || 'That did not go through')
    } finally {
      setBusy(null)
    }
  }

  const inc = report.incident
  const remediated = inc?.live && inc.status === 'AUTO-REMEDIATED' && inc.success

  return (
    <Panel title="Respond to the client" icon={MessageSquareText}>
      {remediated && (
        <p className="mb-3 text-[12.5px] leading-relaxed text-slate-400">
          The agent already handled <span className="num text-slate-300">{inc.incident_id}</span> with{' '}
          {inc.selected_label?.toLowerCase()}. Check the device is behaving before you resolve.
        </p>
      )}
      <label className="block">
        <span className="label mb-1 block">
          {report.status === 'SENT' ? 'Response note (optional)' : 'Resolution note (optional)'}
        </span>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="The client reads this in My Reports."
          className="w-full resize-y rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 text-[13px] leading-relaxed text-slate-100 outline-none placeholder:text-slate-600 focus:border-agent/40"
        />
      </label>

      <div className="mt-3 flex flex-wrap items-center gap-2.5">
        {report.status === 'SENT' && (
          <button
            type="button"
            onClick={() => act('acknowledge')}
            disabled={busy !== null}
            className="flex items-center gap-2 rounded-xl bg-agent/10 px-4 py-2 text-[13px] font-semibold text-agent ring-1 ring-agent/30 transition-colors hover:bg-agent/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === 'acknowledge' && <Loader2 size={13} className="animate-spin" aria-hidden />}
            Acknowledge
          </button>
        )}
        <button
          type="button"
          onClick={() => act('resolve')}
          disabled={busy !== null}
          className="flex items-center gap-2 rounded-xl bg-pass/15 px-4 py-2 text-[13px] font-semibold text-pass ring-1 ring-pass/30 transition-colors hover:bg-pass/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy === 'resolve' && <Loader2 size={13} className="animate-spin" aria-hidden />}
          Resolve report
        </button>
      </div>
      {report.status === 'SENT' && (
        <p className="mt-2 text-[11.5px] text-slate-600">
          Resolving a report that has not been acknowledged acknowledges it at the same moment.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-[12.5px] text-block">
          {error}
        </p>
      )}
    </Panel>
  )
}
