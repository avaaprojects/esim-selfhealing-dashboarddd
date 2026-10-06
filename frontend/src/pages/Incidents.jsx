import { useEffect, useState } from 'react'
import { Activity, Cpu, Workflow } from 'lucide-react'
import IncidentCard from '../components/IncidentCard'
import DecisionTrace from '../components/DecisionTrace'
import RelatedReports from '../components/RelatedReports'
import { Notice, Panel, Spinner } from '../components/Primitives'
import { api } from '../services/api'
import { estate } from '../data/constants'

/**
 * The incident list on the left, the full decision trace for the selected one
 * on the right. Selecting an incident is what loads its trace.
 */
export default function Incidents({
  incidents = [],
  selected,
  trace,
  loadingTrace,
  onSelect,
  error,
  onOpenReport,
  onOpenDevice,
  onReportIssue,
  onOpenOutput,
  isOwner,
}) {
  // Client reports linked to the selected incident (a client sees only their own).
  const [related, setRelated] = useState(null)
  const selectedCount = incidents.find((i) => i.incident_id === selected)?.report_count ?? 0

  useEffect(() => {
    if (!selected) {
      setRelated(null)
      return undefined
    }
    let cancelled = false
    setRelated(null)
    api
      .incidentReports(selected)
      .then((res) => !cancelled && setRelated(res.reports ?? []))
      .catch(() => !cancelled && setRelated([]))
    return () => {
      cancelled = true
    }
  }, [selected, selectedCount])

  const selectedIncident = incidents.find((i) => i.incident_id === selected) ?? null

  const showReports = (id) => {
    onSelect?.(id)
    setTimeout(() => document.getElementById('incident-reports')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
  }

  if (error) {
    return (
      <Notice tone="block" title="Incidents are unavailable">
        The backend is not responding.
      </Notice>
    )
  }

  if (!incidents.length) {
    return (
      <Notice title="No incident has opened yet">
        Run self-healing from the sidebar, or pick a scenario on the Simulation screen. Each run
        streams telemetry past the detector and opens an incident when it crosses the threshold.
      </Notice>
    )
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,400px)_minmax(0,1fr)]">
      <div className="space-y-3">
        {incidents.map((inc) => (
          <IncidentCard
            key={inc.incident_id}
            incident={inc}
            onOpen={onSelect}
            active={inc.incident_id === selected}
            onOpenReports={showReports}
          />
        ))}
      </div>

      <div className="space-y-4">
        {selected && (
          <RelatedReports
            id="incident-reports"
            title="Related reports"
            reports={related}
            onOpenReport={onOpenReport}
            onReportIssue={
              onReportIssue
                ? () =>
                    onReportIssue({
                      incidentId: selected,
                      deviceId: selectedIncident?.observation?.euicc_id,
                    })
                : undefined
            }
            reportLabel="Report an issue with this incident"
            emptyText="No client reports are linked to this incident."
          />
        )}
        {selectedIncident && (
          <IncidentContextBar
            incident={selectedIncident}
            onOpenDevice={onOpenDevice}
            onOpenOutput={isOwner ? onOpenOutput : undefined}
          />
        )}
        {loadingTrace && <Spinner label="Loading decision trace" />}
        {!loadingTrace && trace && <DecisionTrace trace={trace} />}
        {!loadingTrace && !trace && (
          <Panel title="Decision trace" icon={Activity}>
            <p className="text-[13px] text-slate-500">Select an incident to see how the agent handled it.</p>
          </Panel>
        )}
      </div>
    </div>
  )
}

/**
 * Who and what this incident belongs to, and the screens that carry it on.
 * The client, device and processed input all come from the incident itself —
 * the same joined record the Input, Output, Devices and Reports screens read.
 */
function IncidentContextBar({ incident, onOpenDevice, onOpenOutput }) {
  const euiccId = incident.observation?.euicc_id
  const client = estate.client(incident.context)
  const run = incident.run

  return (
    <div className="glass-quiet flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-slate-200">
          {estate.device(incident.context, euiccId)}
          {client && <span className="text-slate-500"> · {client}</span>}
        </p>
        <p className="num text-[11.5px] text-slate-500">
          {euiccId}
          {run && (
            <>
              {' · '}
              opened by input {run.run_id}
              {run.dataset_id ? ` (${run.dataset_id})` : ''}
            </>
          )}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {onOpenDevice && (
          <LinkButton icon={Cpu} onClick={() => onOpenDevice(euiccId, incident.incident_id)}>
            View device
          </LinkButton>
        )}
        {run && onOpenOutput && (
          <LinkButton icon={Workflow} onClick={() => onOpenOutput(incident.incident_id)}>
            View self-healing output
          </LinkButton>
        )}
      </div>
    </div>
  )
}

function LinkButton({ icon: Icon, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium text-agent ring-1 ring-agent/25 hover:bg-agent/10"
    >
      <Icon size={12} aria-hidden />
      {children}
    </button>
  )
}
