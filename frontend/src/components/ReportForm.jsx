import { useMemo, useState } from 'react'
import { Loader2, Send } from 'lucide-react'
import { api } from '../services/api'
import { deviceState, fmt, statusStyle } from '../data/constants'

const FIELD =
  'w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[13px] text-slate-100 outline-none placeholder:text-slate-600 focus:border-agent/40'

const MAX_MESSAGE = 2000

/**
 * INPUT: write a report and, optionally, tie it to a device and an incident.
 *
 * The device and incident lists are the dashboard's own — `devices` comes from
 * the RSP client / monitor / real-time grid and `incidents` from the agent's
 * incident list — so a report can only point at things that exist. Picking an
 * incident fills in its device; picking a device narrows the incident list to
 * that device.
 */
export default function ReportForm({ username, devices = [], incidents = [], initial, onSent }) {
  const [customer, setCustomer] = useState('')
  const [deviceId, setDeviceId] = useState(initial?.deviceId ?? '')
  const [incidentId, setIncidentId] = useState(initial?.incidentId ?? '')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const deviceOf = (inc) => inc.observation?.euicc_id

  const { open, remediated } = useMemo(() => {
    const scoped = incidents.filter((i) => !deviceId || deviceOf(i) === deviceId)
    return {
      open: scoped.filter((i) => i.status !== 'AUTO-REMEDIATED'),
      remediated: scoped.filter((i) => i.status === 'AUTO-REMEDIATED'),
    }
  }, [incidents, deviceId])

  const chooseDevice = (id) => {
    setDeviceId(id)
    // Keep the incident only if it belongs to the newly chosen device.
    const current = incidents.find((i) => i.incident_id === incidentId)
    if (current && id && deviceOf(current) !== id) setIncidentId('')
  }

  const chooseIncident = (id) => {
    setIncidentId(id)
    const inc = incidents.find((i) => i.incident_id === id)
    if (inc) setDeviceId(deviceOf(inc))
  }

  const incidentLabel = (i) => `${i.incident_id} · ${i.fault_label} · ${statusStyle(i.status).label}`

  const submit = async (e) => {
    e.preventDefault()
    if (!message.trim()) return
    setBusy(true)
    setError(null)
    try {
      const report = await api.submitReport({
        message: message.trim(),
        customer: customer.trim(),
        deviceId,
        incidentId,
      })
      onSent?.(report)
    } catch (err) {
      setError(err.message || 'The report could not be sent')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <span className="label mb-1 block">Client</span>
          <p className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-[13px] text-slate-300">
            {username}
          </p>
        </div>
        <label className="block">
          <span className="label mb-1 block">Customer or organisation (optional)</span>
          <input
            type="text"
            value={customer}
            maxLength={120}
            onChange={(e) => setCustomer(e.target.value)}
            placeholder="e.g. the site or account this is about"
            className={FIELD}
          />
        </label>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="label mb-1 block">Device (optional)</span>
          <select value={deviceId} onChange={(e) => chooseDevice(e.target.value)} className={FIELD}>
            <option value="">No specific device</option>
            {devices.map((d) => (
              <option key={d.euicc_id} value={d.euicc_id}>
                {d.euicc_id} · {d.cell_id ?? 'no cell'} · {deviceState(d.state).label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="label mb-1 block">Incident (optional)</span>
          <select
            value={incidentId}
            onChange={(e) => chooseIncident(e.target.value)}
            className={FIELD}
            disabled={!open.length && !remediated.length}
          >
            <option value="">
              {open.length || remediated.length ? 'No specific incident' : 'No incidents on this device'}
            </option>
            {open.length > 0 && (
              <optgroup label="Open — needs attention">
                {open.map((i) => (
                  <option key={i.incident_id} value={i.incident_id}>{incidentLabel(i)}</option>
                ))}
              </optgroup>
            )}
            {remediated.length > 0 && (
              <optgroup label="Auto-remediated">
                {remediated.map((i) => (
                  <option key={i.incident_id} value={i.incident_id}>{incidentLabel(i)}</option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
      </div>

      {!devices.length && (
        <p className="text-[11.5px] text-slate-500">
          No devices are being tracked yet. You can still send a report without one.
        </p>
      )}

      <label className="block">
        <span className="label mb-1 block">What is happening?</span>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={5}
          maxLength={MAX_MESSAGE}
          required
          placeholder="Describe what you are seeing, when it started, and what you expected instead."
          className={`${FIELD} resize-y leading-relaxed`}
        />
      </label>

      <div className="flex items-center justify-between gap-3">
        <span className="num text-[11px] text-slate-600">
          {message.length}/{MAX_MESSAGE}
        </span>
        <button
          type="submit"
          disabled={busy || !message.trim()}
          className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-agent to-cyan-300 px-4 py-2 text-[13px] font-semibold text-slate-950 transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Send size={14} aria-hidden />}
          Send report
        </button>
      </div>

      {error && (
        <p role="alert" className="text-[12.5px] text-block">
          {error}
        </p>
      )}
      {deviceId && !error && (
        <p className="text-[11px] text-slate-600">
          Linked to device <span className="num">{fmt.shortId(deviceId)}</span>
          {incidentId ? (
            <>
              {' '}
              and incident <span className="num">{incidentId}</span>
            </>
          ) : null}
          .
        </p>
      )}
    </form>
  )
}
