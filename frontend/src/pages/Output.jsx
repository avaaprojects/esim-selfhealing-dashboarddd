import { useEffect } from 'react'
import { ArrowLeft, LayoutDashboard } from 'lucide-react'
import { Badge, Notice, Panel, Spinner } from '../components/Primitives'
import AgentPipeline from '../components/AgentPipeline'
import { TypeBadge } from '../components/input/parts'
import FlowStepper from '../components/output/FlowStepper'
import { Stage } from '../components/output/parts'
import { InputSummary, ProcessingSummary } from '../components/output/InputSummary'
import IncidentPanel from '../components/output/IncidentPanel'
import AgentPanel from '../components/output/AgentPanel'
import GatePanel from '../components/output/GatePanel'
import RemediationPanel from '../components/output/RemediationPanel'
import RecoveryPanel from '../components/output/RecoveryPanel'
import NextSteps from '../components/output/NextSteps'

/** A live run keeps moving, so the screen re-reads it while it is open. */
const LIVE_POLL_MS = 2000

/**
 * OUTPUT — what the system detected, decided and did.
 *
 *   INPUT -> PROCESSING -> INCIDENT -> SELF-HEALING -> SAFETY GATE -> REMEDIATION -> RECOVERY
 *
 * A separate full screen: no sidebar, and never shown beside or below the
 * Input screen. Everything on it is read from the backend's record of the run
 * (the real loop's incident, trace, safety report, act result, audit entry and
 * eUICC state); this component computes nothing about outcomes itself.
 * "Back to input" returns to the configuration exactly as the operator left it.
 */
export default function Output({
  output,
  pending,
  onBack,
  onOpenDashboard,
  faultClasses,
  onInject,
  injecting,
  onOpenIncident,
  onOpenReasoning,
  onOpenDevice,
  onReportIssue,
}) {
  const { phase, view, stage, error, incidentId, load, select } = output
  const live = phase === 'ready' && view?.run?.mode === 'realtime'

  useEffect(() => {
    if (phase === 'idle') load()
  }, [phase, load])

  useEffect(() => {
    if (!live) return undefined
    const timer = setInterval(() => load(incidentId), LIVE_POLL_MS)
    return () => clearInterval(timer)
  }, [live, incidentId, load])

  const ctx = phase === 'ready' ? view.commit.resolved : (view?.commit?.resolved ?? pending?.resolved)
  const mode = phase === 'ready' ? view.commit.source : null
  // Where "back" goes: a scenario run (Run self-healing) has no operator input
  // behind it, so there is no configuration to return to.
  const runMode = view?.run?.mode ?? null
  const backLabel = runMode === 'scenario' ? 'Back to dashboard' : 'Back to input'

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-white/[0.07] bg-void/85 backdrop-blur-xl">
        <div className="mx-auto max-w-[1360px] px-4 sm:px-6">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
            <button
              type="button"
              onClick={() => onBack(runMode)}
              className="flex shrink-0 items-center gap-2 rounded-xl bg-agent/10 px-4 py-2 text-[13px] font-semibold text-agent ring-1 ring-agent/35 transition-colors hover:bg-agent/20"
            >
              <ArrowLeft size={15} aria-hidden />
              {backLabel}
            </button>
            <div className="min-w-0">
              <h1 className="text-[16px] font-semibold text-slate-50">Self-healing output</h1>
              <p className="truncate text-[12px] text-slate-400">
                {ctx?.client ? (
                  <>
                    <span className="text-slate-200">{ctx.client?.name ?? "Unregistered device"}</span>
                    {ctx.device ? <> · {ctx.device.device_label} · <span className="num">{ctx.device.euicc_id}</span></> : null}
                  </>
                ) : (
                  'No input has been processed yet'
                )}
              </p>
            </div>
            <div className="ml-auto flex flex-wrap items-center gap-2.5">
              {mode && <TypeBadge typeKey={mode.mode} label={mode.type} />}
              {live && <Badge tone="agent">Live · updating</Badge>}
              <button
                type="button"
                onClick={onOpenDashboard}
                className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] text-slate-400 ring-1 ring-white/10 hover:bg-white/5 hover:text-slate-200"
              >
                <LayoutDashboard size={13} aria-hidden />
                Full dashboard
              </button>
            </div>
          </div>
          {phase === 'ready' && <FlowStepper flow={view.flow} />}
        </div>
      </header>

      <main className="mx-auto max-w-[1360px] px-4 pb-24 pt-6 sm:px-6">
        {(phase === 'idle' || phase === 'loading') && <Spinner label="Loading the latest result" />}

        {phase === 'processing' && <ProcessingScreen stage={stage} pending={pending} />}

        {phase === 'error' && (
          <Notice
            tone="block"
            title="Processing did not complete"
            action={<BackButton onBack={onBack} label={backLabel} mode={runMode} />}
          >
            {error?.message}. Your input is unchanged; go back to fix it and process again.
          </Notice>
        )}

        {phase === 'unavailable' && (
          <Notice title="There is no result to show" action={<BackButton onBack={onBack} label={backLabel} mode={runMode} />}>
            {view?.message}
          </Notice>
        )}

        {phase === 'ready' && (
          <Ready
            view={view}
            onSelectIncident={select}
            onInject={onInject}
            faultClasses={faultClasses}
            injecting={injecting}
            onBack={onBack}
            onOpenIncident={onOpenIncident}
            onOpenReasoning={onOpenReasoning}
            onOpenDevice={onOpenDevice}
            onReportIssue={onReportIssue}
          />
        )}
      </main>
    </div>
  )
}

function BackButton({ onBack, label = 'Back to input', mode }) {
  return (
    <button
      type="button"
      onClick={() => onBack(mode)}
      className="flex items-center gap-2 rounded-xl bg-agent/10 px-4 py-2 text-[13px] font-semibold text-agent ring-1 ring-agent/35 hover:bg-agent/20"
    >
      <ArrowLeft size={15} aria-hidden />
      {label}
    </button>
  )
}

/** INPUT -> PROCESSING: the loop is running. The stage walk is a minimum display time; see useOutput. */
function ProcessingScreen({ stage, pending }) {
  const r = pending?.resolved
  return (
    <div className="mx-auto max-w-4xl pt-8">
      <Panel>
        <p className="label">Processing</p>
        <h2 className="mt-1 text-[20px] font-semibold text-slate-50">Running your input through the self-healing loop</h2>
        {r?.client && (
          <p className="mt-1.5 text-[13px] text-slate-400">
            {r.client?.name ?? "Unregistered device"} · {r.device?.device_label} · {pending.source}
          </p>
        )}
        <div className="mt-6">
          <AgentPipeline stage={stage} orientation="horizontal" />
        </div>
        <p className="mt-5 text-[12px] leading-relaxed text-slate-500">
          MONITOR watches the telemetry, REASON diagnoses, PLAN picks an action, SAFETY gates it, ACT dispatches it and VERIFY
          checks the outcome. The result appears here when the loop finishes.
        </p>
      </Panel>
    </div>
  )
}

function Ready({ view, onSelectIncident, onInject, faultClasses, injecting, onBack, ...links }) {
  // A scenario run (Run self-healing) has no operator input behind it, so
  // there is no configuration to go back to.
  const runMode = view?.run?.mode
  const backLabel = runMode === 'scenario' ? 'Back to dashboard' : 'Back to input'
  const flow = Object.fromEntries(view.flow.map((f) => [f.key, f]))
  const F = (key) => ({ state: flow[key].state, headline: flow[key].headline })
  const skipped = (text) => <Notice title={text} />

  return (
    <div className="space-y-10">
      <Stage id="input" index={1} title="Input" {...F('input')}>
        <InputSummary commit={view.commit} run={view.run} />
      </Stage>

      <Stage id="processing" index={2} title="Processing" {...F('processing')}>
        <ProcessingSummary run={view.run} incidents={view.incidents} />
      </Stage>

      <Stage id="incident" index={3} title="Incident" {...F('incident')}>
        <IncidentPanel
          view={view}
          onSelectIncident={onSelectIncident}
          onInject={onInject}
          faultClasses={faultClasses}
          injecting={injecting}
        />
      </Stage>

      <Stage id="self_healing" index={4} title="Self-healing agent" {...F('self_healing')}>
        {view.trace ? <AgentPanel view={view} /> : skipped('Nothing to diagnose without an incident')}
      </Stage>

      <Stage id="safety_gate" index={5} title="Safety gate" frame={view.gate?.tone ?? 'neutral'} {...F('safety_gate')}>
        {view.gate ? <GatePanel view={view} /> : skipped('Nothing reached the gate')}
      </Stage>

      <Stage id="remediation" index={6} title="Remediation" {...F('remediation')}>
        {view.remediation ? <RemediationPanel view={view} /> : skipped('No remedial action was needed')}
      </Stage>

      <Stage id="recovery" index={7} title="Recovery" {...F('recovery')}>
        <RecoveryPanel view={view} />
      </Stage>

      <NextSteps view={view} {...links} />

      <div className="flex justify-start border-t border-white/[0.07] pt-6">
        <BackButton onBack={onBack} label={backLabel} mode={runMode} />
      </div>
    </div>
  )
}
