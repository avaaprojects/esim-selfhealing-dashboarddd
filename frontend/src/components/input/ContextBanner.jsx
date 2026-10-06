import { CLIENT_ACCENTS } from '../../data/constants'
import { TypeBadge } from './parts'

/**
 * The first thing on the screen: whose input this is. The selected client is
 * unmistakable (large name, its own accent colour); everything else the input
 * depends on sits beside it.
 */
export default function ContextBanner({ view, catalog }) {
  const r = view?.resolved ?? {}
  const clients = catalog?.registry?.clients ?? []
  const idx = Math.max(0, clients.findIndex((c) => c.client_id === r.client?.client_id))
  const accent = CLIENT_ACCENTS[idx % CLIENT_ACCENTS.length]
  const mode = view?.state?.data_source?.mode

  if (!r.client) {
    return (
      <div className="glass border-dashed p-5">
        <p className="text-[15px] font-semibold text-slate-100">No client selected</p>
        <p className="mt-1 text-[13px] text-slate-400">
          Choose the client this input belongs to below. Every device, dataset and file is kept with it.
        </p>
      </div>
    )
  }

  return (
    <section
      className="glass overflow-hidden p-0"
      style={{ borderLeft: `4px solid ${accent}` }}
      aria-label="Selected client and context"
    >
      <div className="grid gap-x-8 gap-y-4 p-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)]">
        <div className="min-w-0">
          <p className="label">Selected client</p>
          <p className="mt-0.5 truncate text-[22px] font-semibold leading-tight text-slate-50" style={{ color: accent }}>
            {r.client.name}
          </p>
          <p className="mt-1 text-[13px] text-slate-400">
            {r.group ? <>Group: <span className="text-slate-200">{r.group.name}</span></> : 'No group chosen yet'}
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            {mode === 'realtime' && <TypeBadge typeKey="realtime" />}
            {mode === 'synthetic' && <TypeBadge typeKey="synthetic" />}
            {mode === 'upload' && <TypeBadge typeKey="upload" />}
            {!mode && <span className="text-[12px] text-slate-500">No data source chosen yet</span>}
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
          <Cell label="Device" value={r.device?.device_label} sub={r.device?.euicc_id} />
          <Cell label="eSIM (ICCID)" value={r.device?.iccid} mono sub={r.device?.profile_name} />
          <Cell label="RSP environment" value={r.rsp_environment?.name} sub={r.rsp_environment?.smdp_endpoint} />
          <Cell
            label="Network and authentication"
            value={r.network?.name}
            sub={r.network?.auth ? `${r.network.auth.method} · PLMN ${r.network.plmn}` : null}
          />
        </dl>
      </div>
    </section>
  )
}

function Cell({ label, value, sub, mono = false }) {
  return (
    <div className="min-w-0">
      <dt className="label">{label}</dt>
      <dd className={`mt-0.5 break-words text-[13px] ${value ? 'text-slate-100' : 'text-slate-600'} ${mono ? 'num' : ''}`}>
        {value ?? 'Not chosen'}
      </dd>
      {sub && <dd className="num mt-0.5 break-all text-[11px] text-slate-500">{sub}</dd>}
    </div>
  )
}
