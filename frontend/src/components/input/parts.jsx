import { Badge } from '../Primitives'
import { SOURCE_TYPE } from '../../data/constants'

export const SELECT_CLASS =
  'w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[13px] text-slate-100 outline-none focus:border-agent/40 disabled:opacity-50'

/** REAL-TIME / SYNTHETIC / SIMULATED / OPERATOR UPLOAD — always spelled out. */
export function TypeBadge({ typeKey, label }) {
  // An imported dataset is operator-supplied: show it amber, like an upload,
  // never violet like the project's own synthetic data.
  const key = label === 'OPERATOR IMPORT' ? 'upload' : typeKey
  const t = SOURCE_TYPE[key] ?? SOURCE_TYPE.synthetic
  return <Badge tone={t.tone}>{label ?? t.label}</Badge>
}

/** Did the file on disk still match the checksum recorded when it was generated? */
export function IntegrityBadge({ integrity }) {
  const map = {
    verified: { tone: 'pass', text: 'Checksum verified' },
    modified: { tone: 'block', text: 'Edited since generation' },
    missing: { tone: 'block', text: 'File missing' },
    unchecked: { tone: 'neutral', text: 'Unchecked' },
  }
  const m = map[integrity] ?? map.unchecked
  return <Badge tone={m.tone}>{m.text}</Badge>
}

/** Small label/value pair used in the detail cards. */
export function Item({ label, children, mono = false }) {
  return (
    <div className="min-w-0">
      <dt className="label">{label}</dt>
      <dd className={`mt-0.5 break-words text-[13px] text-slate-100 ${mono ? 'num' : ''}`}>{children ?? '—'}</dd>
    </div>
  )
}

/** A table of rows (first columns of a CSV, or dataset preview rows). */
export function CsvPreview({ columns = [], rows = [], caption }) {
  if (!columns.length) return null
  return (
    <div>
      <div className="overflow-x-auto rounded-lg border border-white/[0.07]">
        <table className="w-full text-left text-[11.5px]">
          <thead className="bg-white/[0.03] text-slate-400">
            <tr>
              {columns.map((c) => (
                <th key={c} className="whitespace-nowrap px-2.5 py-1.5 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.05]">
            {rows.map((r, i) => (
              <tr key={i} className="num text-slate-300">
                {columns.map((c) => (
                  <td key={c} className="whitespace-nowrap px-2.5 py-1.5">
                    {r[c] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {caption && <p className="mt-1.5 text-[11px] text-slate-500">{caption}</p>}
    </div>
  )
}
