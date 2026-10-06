import { Building2, Cpu, Fingerprint, Network, Server } from 'lucide-react'
import { Badge, Panel } from '../Primitives'
import { CLIENT_ACCENTS } from '../../data/constants'
import { Item, SELECT_CLASS, TypeBadge } from './parts'

/**
 * CLIENT / CUSTOMER: who the input is for, and the context the agent will
 * reason in. Client -> group -> device, then the device's eSIM identity, RSP
 * environment and network/authentication context. Choosing a device fills in
 * its group, RSP environment and network; both can still be overridden.
 */
export default function ClientSection({ catalog, view, patch, incidents = [], busy }) {
  const reg = catalog?.registry
  if (!reg) return null
  const state = view?.state ?? {}
  const r = view?.resolved ?? {}

  const groups = reg.groups.filter((g) => g.client_id === state.client_id)
  const devices = reg.devices.filter(
    (d) => d.client_id === state.client_id && (!state.group_id || d.group_id === state.group_id),
  )
  const deviceIncidents = incidents.filter((i) => i.observation?.euicc_id === state.device_id)
  const countFor = (cid) => reg.devices.filter((d) => d.client_id === cid).length

  return (
    <Panel title="Client / customer" icon={Building2} meta="who this input is for">
      <div role="radiogroup" aria-label="Client" className="grid gap-3 md:grid-cols-3">
        {reg.clients.map((c, i) => {
          const on = c.client_id === state.client_id
          const accent = CLIENT_ACCENTS[i % CLIENT_ACCENTS.length]
          return (
            <button
              key={c.client_id}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={busy}
              onClick={() => !on && patch({ client_id: c.client_id })}
              className={[
                'rounded-xl border p-4 text-left transition-colors',
                on ? 'bg-white/[0.06]' : 'border-white/10 hover:border-white/25',
              ].join(' ')}
              style={on ? { borderColor: accent, boxShadow: `0 0 0 1px ${accent}55` } : undefined}
            >
              <p className="text-[14px] font-semibold" style={{ color: on ? accent : undefined }}>
                {c.name}
              </p>
              <p className="mt-0.5 text-[12px] text-slate-400">{c.sector}</p>
              <p className="num mt-2 text-[11px] text-slate-500">
                {c.client_id} · {countFor(c.client_id)} devices
              </p>
            </button>
          )
        })}
      </div>

      {state.client_id && (
        <>
          <div className="mt-6">
            <p className="label mb-2">Network group</p>
            <div role="radiogroup" aria-label="Network group" className="flex flex-wrap gap-2">
              {groups.map((g) => {
                const on = g.group_id === state.group_id
                return (
                  <button
                    key={g.group_id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    disabled={busy}
                    title={g.description}
                    onClick={() => !on && patch({ group_id: g.group_id })}
                    className={[
                      'rounded-full px-3.5 py-1.5 text-[12.5px] ring-1 transition-colors',
                      on ? 'bg-agent/10 text-agent ring-agent/30' : 'text-slate-300 ring-white/10 hover:bg-white/5',
                    ].join(' ')}
                  >
                    {g.name}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="mt-6">
            <p className="label mb-2">Device</p>
            <div role="radiogroup" aria-label="Device" className="grid gap-2.5 lg:grid-cols-2">
              {devices.map((d) => {
                const on = d.euicc_id === state.device_id
                return (
                  <button
                    key={d.euicc_id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    disabled={busy}
                    onClick={() => !on && patch({ device_id: d.euicc_id })}
                    className={[
                      'glass-quiet flex items-start gap-3 px-3.5 py-3 text-left transition-colors',
                      on ? 'border-agent/40 bg-agent/[0.06]' : 'hover:border-white/20',
                    ].join(' ')}
                  >
                    <Cpu size={15} className={`mt-0.5 shrink-0 ${on ? 'text-agent' : 'text-slate-500'}`} aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium text-slate-100">{d.device_label}</p>
                      <p className="num truncate text-[11.5px] text-slate-500" title={d.euicc_id}>
                        {d.euicc_id} · {d.cell_id}
                      </p>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {d.in_realtime_grid ? (
                          <Badge tone="agent">In the real-time grid</Badge>
                        ) : (
                          <Badge tone="neutral">Stored dataset only</Badge>
                        )}
                        {d.fleet_size > 1 && <Badge tone="hold">Shared by {d.fleet_size} devices</Badge>}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        </>
      )}

      {r.device && (
        <div className="mt-6 grid gap-4 xl:grid-cols-3">
          <div className="glass-quiet p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <p className="flex items-center gap-2 text-[13px] font-semibold text-slate-100">
                <Fingerprint size={14} className="text-agent" aria-hidden /> eSIM identity
              </p>
              <TypeBadge typeKey="synthetic" />
            </div>
            <dl className="space-y-2.5">
              <Item label="eUICC id" mono>{r.device.euicc_id}</Item>
              <Item label="EID" mono>{r.device.eid}</Item>
              <Item label="ICCID" mono>{r.device.iccid}</Item>
              <Item label="Profile">{r.device.profile_name}</Item>
            </dl>
          </div>

          <div className="glass-quiet p-4">
            <p className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-slate-100">
              <Server size={14} className="text-agent" aria-hidden /> RSP environment
            </p>
            <select
              aria-label="RSP environment"
              value={state.rsp_env_id ?? ''}
              disabled={busy}
              onChange={(e) => patch({ rsp_env_id: e.target.value || null })}
              className={SELECT_CLASS}
            >
              <option value="">Choose an RSP environment</option>
              {reg.rsp_environments.map((e) => (
                <option key={e.rsp_env_id} value={e.rsp_env_id}>{e.name}</option>
              ))}
            </select>
            {r.rsp_environment && (
              <dl className="mt-3 space-y-2.5">
                <Item label="SM-DP+" mono>{r.rsp_environment.smdp_endpoint}</Item>
                <Item label="SM-SR" mono>{r.rsp_environment.smsr_endpoint}</Item>
                <Item label="Interfaces">{r.rsp_environment.interfaces.join(' · ')}</Item>
                <Item label="Mode">{r.rsp_environment.mode}</Item>
              </dl>
            )}
          </div>

          <div className="glass-quiet p-4">
            <p className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-slate-100">
              <Network size={14} className="text-agent" aria-hidden /> Network and authentication
            </p>
            <select
              aria-label="Network and authentication context"
              value={state.network_id ?? ''}
              disabled={busy}
              onChange={(e) => patch({ network_id: e.target.value || null })}
              className={SELECT_CLASS}
            >
              <option value="">Choose a network context</option>
              {reg.networks.map((n) => (
                <option key={n.network_id} value={n.network_id}>{n.name}</option>
              ))}
            </select>
            {r.network && (
              <dl className="mt-3 space-y-2.5">
                <Item label="PLMN · radio" mono>{`${r.network.plmn} · ${r.network.rat}`}</Item>
                <Item label="Region">{r.network.region}</Item>
                <Item label="Cells" mono>{r.network.cells.join(', ')}</Item>
                {r.network.auth && (
                  <Item label="Authentication">
                    {r.network.auth.method}
                    <span className="num text-slate-500">
                      {' '}· key epoch {r.network.auth.key_epoch} / SM-SR {r.network.auth.smsr_key_epoch}
                    </span>
                  </Item>
                )}
              </dl>
            )}
          </div>
        </div>
      )}

      {state.device_id && (
        <div className="mt-5 max-w-xl">
          <label className="block">
            <span className="label mb-1 block">Related incident (optional)</span>
            <select
              value={state.incident_id ?? ''}
              disabled={busy}
              onChange={(e) => patch({ incident_id: e.target.value || null })}
              className={SELECT_CLASS}
            >
              <option value="">{deviceIncidents.length ? 'No specific incident' : 'No incidents on this device'}</option>
              {deviceIncidents.map((i) => (
                <option key={i.incident_id} value={i.incident_id}>
                  {i.incident_id} · {i.fault_label} · {i.status}
                </option>
              ))}
            </select>
          </label>
          <p className="mt-1 text-[11.5px] text-slate-500">
            Files you upload stay with the incident chosen here as well as the client and device.
          </p>
        </div>
      )}
    </Panel>
  )
}
