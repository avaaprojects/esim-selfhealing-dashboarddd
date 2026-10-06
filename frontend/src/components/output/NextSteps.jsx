import { Activity, Brain, Cpu, MessageSquarePlus, Waves } from 'lucide-react'
import { Panel } from '../Primitives'

/**
 * RECOVERY -> REPORT / RESOLUTION, and the rest of the joins.
 *
 * The Output screen is the end of one pass of the loop, not the end of the
 * chain: the same incident, device and telemetry live on the Incidents,
 * Devices and Agent-reasoning screens, and a client report is how the
 * resolution is recorded and closed. These are the existing screens and the
 * existing ids, not copies.
 */
export default function NextSteps({ view, onOpenIncident, onOpenReasoning, onOpenDevice, onReportIssue }) {
  const incidentId = view.selected_incident_id
  const euiccId = view.commit?.resolved?.device?.euicc_id
  const deviceLabel = view.commit?.resolved?.device?.device_label
  const recovered = view.recovery?.verdict === 'RECOVERED'

  const steps = [
    incidentId && {
      key: 'report',
      icon: MessageSquarePlus,
      title: recovered ? 'Record the resolution with the client' : 'Raise this with the client',
      detail: recovered
        ? 'File a client report against this incident so the fix is acknowledged and closed.'
        : 'This incident still needs a person. File a client report so it is tracked to resolution.',
      cta: 'New client report',
      primary: true,
      run: () => onReportIssue?.({ deviceId: euiccId, incidentId }),
    },
    incidentId && {
      key: 'incident',
      icon: Activity,
      title: 'Open this incident',
      detail: 'The same incident on the Incidents screen, with its reports and full trace.',
      cta: incidentId,
      run: () => onOpenIncident?.(incidentId),
    },
    incidentId && {
      key: 'reasoning',
      icon: Brain,
      title: 'Full decision trace',
      detail: 'OBSERVE to LEARN, stage by stage, with latencies and retrieved precedents.',
      cta: 'Agent reasoning',
      run: () => onOpenReasoning?.(incidentId),
    },
    euiccId && {
      key: 'device',
      icon: Cpu,
      title: 'Device history',
      detail: `Every incident, telemetry window and client report for ${deviceLabel ?? 'this device'}.`,
      cta: 'Open device',
      run: () => onOpenDevice?.(euiccId, incidentId),
    },
  ].filter(Boolean)

  if (!steps.length) return null

  return (
    <Panel title="Where this goes next" icon={Waves}>
      <div className="grid gap-3 sm:grid-cols-2">
        {steps.map((s) => {
          const Icon = s.icon
          return (
            <button
              key={s.key}
              type="button"
              onClick={s.run}
              className={[
                'flex h-full items-start gap-3 rounded-xl px-4 py-3.5 text-left ring-1 transition-colors',
                s.primary
                  ? 'bg-agent/10 ring-agent/35 hover:bg-agent/20'
                  : 'bg-white/[0.02] ring-white/10 hover:bg-white/[0.06]',
              ].join(' ')}
            >
              <Icon size={16} className={`mt-0.5 shrink-0 ${s.primary ? 'text-agent' : 'text-slate-400'}`} aria-hidden />
              <span className="min-w-0">
                <span className={`block text-[13.5px] font-semibold ${s.primary ? 'text-agent' : 'text-slate-100'}`}>
                  {s.title}
                </span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-slate-400">{s.detail}</span>
                <span className="num mt-1.5 block truncate text-[11.5px] text-slate-500">{s.cta}</span>
              </span>
            </button>
          )
        })}
      </div>
    </Panel>
  )
}
