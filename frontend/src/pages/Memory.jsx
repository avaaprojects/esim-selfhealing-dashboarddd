import { Database, Search } from 'lucide-react'
import MemoryCard from '../components/MemoryCard'
import { Field, Meter, Notice, Panel, Spinner } from '../components/Primitives'
import { fmt } from '../data/constants'

/**
 * Incident memory D. Retrieval is cosine similarity over a hashed-feature
 * encoding of telemetry direction plus diagnosis text, filtered by a similarity
 * floor — so a precedent only counts when it genuinely resembles the incident.
 */
export default function Memory({ memory, loading, error, selectedIncident }) {
  if (loading) return <Spinner label="Loading incident memory" />
  if (error) {
    return <Notice tone="block" title="Memory is unavailable">The backend is not responding.</Notice>
  }

  const enc = memory?.encoder ?? {}

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Records in D" value={memory?.total ?? 0} />
        <Tile label="Seeded corpus" value={memory?.seeded ?? 0} hint="prior knowledge before the agent ran" />
        <Tile label="Learned this session" value={memory?.learned_this_session ?? 0} hint="written back after each incident" />
        <Tile label="Similarity floor" value={fmt.num(enc.min_similarity)} hint={`top-${enc.top_n} retrieval, ${enc.dim}-dim encoder`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Panel title="Stored incidents" icon={Database} meta={`newest first`}>
          <div className="grid gap-2.5 lg:grid-cols-2">
            {(memory?.records ?? []).map((r) => (
              <MemoryCard key={r.record_id} record={r} />
            ))}
          </div>
          {!memory?.records?.length && (
            <p className="text-[13px] text-slate-500">Memory is empty.</p>
          )}
        </Panel>

        <div className="space-y-4">
          <Panel title="Retrieved for this incident" icon={Search} meta={selectedIncident ?? '—'}>
            {memory?.retrieved?.length ? (
              <div className="space-y-2">
                {memory.retrieved.map((r) => (
                  <MemoryCard key={r.record_id} record={r} compact />
                ))}
              </div>
            ) : (
              <p className="text-[13px] leading-relaxed text-slate-500">
                {selectedIncident
                  ? 'No precedent cleared the similarity floor for this incident — the agent worked from a cold start.'
                  : 'Open an incident to see which precedents the agent pulled.'}
              </p>
            )}
          </Panel>

          <Panel title="Outcomes by action">
            <p className="mb-3 text-[12px] leading-relaxed text-slate-400">
              These empirical rates are what PLAN recovers as P_success(a) when it ranks candidates.
            </p>
            <div className="space-y-3">
              {(memory?.outcome_by_action ?? []).map((o) => (
                <div key={o.action}>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[12.5px] text-slate-300">{o.label}</span>
                    <span className="num text-[12px] text-slate-400">
                      {fmt.pct(o.success_rate)} <span className="text-slate-600">({o.success}/{o.total})</span>
                    </span>
                  </div>
                  <Meter className="mt-1.5" value={o.success_rate ?? 0} max={1} tone="policy" />
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Encoder">
            <div className="divide-y divide-white/[0.05]">
              <Field label="Type" value={enc.type} />
              <Field label="Dimension p" value={enc.dim} />
              <Field label="Top-N" value={enc.top_n} />
              <Field label="Min cosine" value={fmt.num(enc.min_similarity)} />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}

function Tile({ label, value, hint }) {
  return (
    <div className="glass px-4 py-3.5">
      <p className="label">{label}</p>
      <p className="num mt-1 text-2xl font-semibold text-slate-100">{value}</p>
      {hint && <p className="mt-1.5 text-[11px] leading-snug text-slate-500">{hint}</p>}
    </div>
  )
}
