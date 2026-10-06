import { Panel } from '../Primitives'
import { ScrollText } from 'lucide-react'
import { fmt } from '../../data/constants'
import { TypeBadge } from './parts'

/**
 * DATA PROVENANCE: one row per piece of input — where it came from, which
 * file, what kind it is, whose it is, which fields it carries and when.
 * The four questions an operator needs answered are at the top.
 */
export default function ProvenanceTable({ view }) {
  const rows = view?.provenance ?? []
  const r = view?.resolved ?? {}
  const source = rows.find((x) => x.role === 'Data source')

  return (
    <Panel title="Data provenance" icon={ScrollText} meta="what is entering, and where it came from">
      <dl className="grid gap-x-6 gap-y-3 md:grid-cols-2 xl:grid-cols-4">
        <Glance q="What is the input?">
          {source ? (
            <>
              {source.fields.length} telemetry channels
              <span className="block text-[11.5px] text-slate-500">{source.dataset}</span>
            </>
          ) : null}
        </Glance>
        <Glance q="Where did it come from?">
          {source ? <TypeBadge typeKey={source.type_key} label={source.type} /> : null}
        </Glance>
        <Glance q="Which client does it belong to?">
          {r.client ? (
            <>
              {r.client.name}
              <span className="block text-[11.5px] text-slate-500">{r.group?.name ?? 'no group chosen'}</span>
            </>
          ) : null}
        </Glance>
        <Glance q="Which device or network?">
          {r.device ? (
            <>
              {r.device.device_label}
              <span className="num block text-[11.5px] text-slate-500">
                {r.device.euicc_id} · {r.network?.name ?? 'no network chosen'}
              </span>
            </>
          ) : null}
        </Glance>
      </dl>

      {rows.length === 0 ? (
        <p className="mt-5 text-[13px] text-slate-500">
          Nothing is selected yet. Choose a client, a device and a data source and each piece of input will be listed here.
        </p>
      ) : (
        <div className="mt-5 overflow-x-auto rounded-lg border border-white/[0.07]">
          <table className="w-full min-w-[860px] text-left text-[12px]">
            <thead className="bg-white/[0.03] text-slate-400">
              <tr>
                {['Input', 'Source', 'Dataset or file', 'Type', 'Client / group', 'Relevant fields', 'Time range'].map((h) => (
                  <th key={h} className="px-3 py-2 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.05]">
              {rows.map((p, i) => (
                <tr key={`${p.role}-${p.upload_id ?? i}`} className="align-top text-slate-300">
                  <td className="px-3 py-2.5 font-medium text-slate-100">{p.role}</td>
                  <td className="px-3 py-2.5">{p.source}</td>
                  <td className="num max-w-[240px] break-all px-3 py-2.5 text-[11.5px]">{p.dataset}</td>
                  <td className="px-3 py-2.5"><TypeBadge typeKey={p.type_key} label={p.type} /></td>
                  <td className="px-3 py-2.5">
                    {p.client}
                    <span className="block text-[11.5px] text-slate-500">{p.group}</span>
                  </td>
                  <td className="num max-w-[220px] px-3 py-2.5 text-[11.5px] text-slate-400">
                    {(p.fields ?? []).slice(0, 8).join(', ')}
                    {(p.fields?.length ?? 0) > 8 ? ` +${p.fields.length - 8}` : ''}
                  </td>
                  <td className="px-3 py-2.5 text-[11.5px]">{fmt.range(p.time_range)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.map((p, i) => p.note && (
        <p key={`n${i}`} className="mt-2 text-[11.5px] leading-relaxed text-slate-500">
          <span className="text-slate-400">{p.role}:</span> {p.note}
        </p>
      ))}
    </Panel>
  )
}

function Glance({ q, children }) {
  return (
    <div className="min-w-0">
      <dt className="label">{q}</dt>
      <dd className={`mt-1 text-[13px] ${children ? 'text-slate-100' : 'text-slate-600'}`}>{children ?? 'Not decided yet'}</dd>
    </div>
  )
}
