import { useEffect, useState } from 'react'
import { AlertTriangle, ChevronDown, Database, FileSpreadsheet, Loader2, Play, Radio, Square } from 'lucide-react'
import { Badge, Panel } from '../Primitives'
import { api } from '../../services/api'
import { SOURCE_TYPE, fmt } from '../../data/constants'
import { CsvPreview, IntegrityBadge, TypeBadge } from './parts'

/**
 * DATA SOURCE: exactly one of three, kept visibly apart.
 *
 *   REAL-TIME DATA         cyan   the server's live grid feed (no file)
 *   PREFETCHED / SYNTHETIC violet stored CSVs from ./data, always labelled
 *                                 SYNTHETIC / SIMULATED
 *   YOUR OWN CSV           amber  a telemetry CSV the operator uploaded, labelled
 *                                 OPERATOR UPLOAD and never mixed into the others
 *
 * The panels share no list, no colour and no wording. Picking one dims the
 * others; nothing from one ever appears inside another.
 */
export default function DataSourceSection({ catalog, view, realtime, patch, busy, isOwner, onStartRealtime, onStopRealtime }) {
  const state = view?.state ?? {}
  const mode = state.data_source?.mode ?? null
  const dataset = state.data_source?.dataset_id ?? null

  return (
    <Panel title="Data source" icon={Database} meta="choose one">
      <div className="grid gap-4 xl:grid-cols-2">
        <RealtimePanel
          active={mode === 'realtime'}
          realtime={realtime}
          catalog={catalog}
          state={state}
          busy={busy}
          isOwner={isOwner}
          onSelect={() => patch({ data_source: { mode: 'realtime' } })}
          onStart={onStartRealtime}
          onStop={onStopRealtime}
        />
        <SyntheticPanel
          active={mode === 'synthetic'}
          catalog={catalog}
          state={state}
          dataset={dataset}
          busy={busy}
          onSelectMode={() => patch({ data_source: { mode: 'synthetic' } })}
          onPick={(id) => patch({ data_source: { mode: 'synthetic', dataset_id: id } })}
        />
        <div className="xl:col-span-2">
          <UploadPanel
            active={mode === 'upload'}
            view={view}
            state={state}
            busy={busy}
            onPick={(id) => patch({ data_source: { upload_id: id } })}
          />
        </div>
      </div>
    </Panel>
  )
}

function Frame({ typeKey, active, children }) {
  const t = SOURCE_TYPE[typeKey]
  return (
    <div
      className={[
        'rounded-2xl border p-4 transition-opacity',
        active ? `${t.ring} ${t.bg}` : 'border-white/10 opacity-70 hover:opacity-100',
      ].join(' ')}
    >
      {children}
    </div>
  )
}

function PanelHeader({ typeKey, icon: Icon, title, active, onSelect, disabled, tag }) {
  const t = SOURCE_TYPE[typeKey]
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className={`flex items-center gap-2 text-[14px] font-semibold ${t.text}`}>
          <Icon size={15} aria-hidden /> {title}
        </p>
        <div className="mt-1.5">{tag}</div>
      </div>
      <button
        type="button"
        role="radio"
        aria-checked={active}
        disabled={disabled}
        onClick={onSelect}
        className={[
          'shrink-0 rounded-full px-3 py-1 text-[12px] font-medium ring-1 transition-colors',
          active ? `${t.bg} ${t.text} ${t.ringOn}` : 'text-slate-300 ring-white/15 hover:bg-white/5',
        ].join(' ')}
      >
        {active ? 'In use' : 'Use this'}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
function RealtimePanel({ active, realtime, catalog, state, busy, isOwner, onSelect, onStart, onStop }) {
  const running = Boolean(realtime?.running)
  const grid = realtime?.devices ?? []
  const clientDevices = (catalog?.registry?.devices ?? []).filter((d) => d.client_id === state.client_id)
  const gridOfClient = clientDevices.filter((d) => grid.includes(d.euicc_id))
  const selectedInGrid = grid.includes(state.device_id)

  return (
    <Frame typeKey="realtime" active={active}>
      <PanelHeader
        typeKey="realtime"
        icon={Radio}
        title="Real-time data"
        active={active}
        disabled={busy}
        onSelect={onSelect}
        tag={<TypeBadge typeKey="realtime" />}
      />
      <p className="mt-3 text-[12.5px] leading-relaxed text-slate-400">
        Samples arriving now from <span className="text-slate-200">{realtime?.source_label ?? 'Prototype Server / Grid Telemetry'}</span>,
        pushed through the same agent as a stored file. This is the server's own prototype generator, not a connection to
        live telecom infrastructure.
      </p>

      <dl className="mt-4 grid grid-cols-3 gap-3">
        <div>
          <dt className="label">Feed</dt>
          <dd className={`mt-0.5 flex items-center gap-1.5 text-[13px] ${running ? 'text-pass' : 'text-slate-400'}`}>
            <span className={`h-2 w-2 rounded-full ${running ? 'animate-corepulse bg-pass' : 'bg-slate-500'}`} aria-hidden />
            {running ? 'Running' : 'Stopped'}
          </dd>
        </div>
        <div>
          <dt className="label">Samples</dt>
          <dd className="num mt-0.5 text-[13px] text-slate-100">{realtime?.samples_received ?? 0}</dd>
        </div>
        <div>
          <dt className="label">Last sample</dt>
          <dd className="num mt-0.5 text-[13px] text-slate-100">{fmt.clock(realtime?.last_ts)}</dd>
        </div>
      </dl>

      <div className="mt-4">
        <p className="label mb-1.5">Grid devices for this client</p>
        {!state.client_id && <p className="text-[12.5px] text-slate-500">Choose a client to see its grid devices.</p>}
        {state.client_id && gridOfClient.length === 0 && (
          <p className="text-[12.5px] text-slate-500">None of this client's devices are in the real-time grid.</p>
        )}
        <ul className="space-y-1.5">
          {gridOfClient.map((d) => (
            <li key={d.euicc_id} className="flex items-center justify-between gap-2 text-[12.5px]">
              <span className="min-w-0 truncate text-slate-300">
                {d.device_label} <span className="num text-slate-500">{d.cell_id}</span>
              </span>
              {d.euicc_id === state.device_id && <Badge tone="agent">Selected</Badge>}
            </li>
          ))}
        </ul>
      </div>

      {active && state.device_id && !selectedInGrid && (
        <p className="mt-3 flex items-start gap-2 text-[12.5px] text-block" role="alert">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          The selected device is not in the real-time grid. Pick one of the grid devices above, or use a stored dataset.
        </p>
      )}

      {isOwner && (
        <div className="mt-4">
          {running ? (
            <button
              type="button"
              onClick={onStop}
              className="flex items-center gap-2 rounded-xl px-3.5 py-2 text-[12.5px] font-medium text-slate-200 ring-1 ring-white/15 hover:bg-white/5"
            >
              <Square size={12} aria-hidden /> Stop real-time data
            </button>
          ) : (
            <button
              type="button"
              onClick={onStart}
              className="flex items-center gap-2 rounded-xl bg-agent/10 px-3.5 py-2 text-[12.5px] font-semibold text-agent ring-1 ring-agent/30 hover:bg-agent/20"
            >
              <Play size={12} aria-hidden /> Start real-time data
            </button>
          )}
        </div>
      )}
    </Frame>
  )
}

// ---------------------------------------------------------------------------
function SyntheticPanel({ active, catalog, state, dataset, busy, onSelectMode, onPick }) {
  const [openId, setOpenId] = useState(null)
  const list = (catalog?.datasets ?? []).filter((d) => d.client_id === state.client_id)

  return (
    <Frame typeKey="synthetic" active={active}>
      <PanelHeader
        typeKey="synthetic"
        icon={Database}
        title="Prefetched / synthetic data"
        active={active}
        disabled={busy}
        onSelect={onSelectMode}
        tag={<TypeBadge typeKey="synthetic" />}
      />
      <p className="mt-3 text-[12.5px] leading-relaxed text-slate-400">
        Stored CSV files from the project's <span className="num text-slate-200">data/</span> folder. They were generated
        from the built-in simulator with fixed seeds. <span className="text-policy">Nothing in them describes a real customer, device or network.</span>
      </p>

      <div className="mt-4 space-y-2.5">
        {!state.client_id && <p className="text-[12.5px] text-slate-500">Choose a client to see its datasets.</p>}
        {state.client_id && list.length === 0 && <p className="text-[12.5px] text-slate-500">This client has no stored datasets.</p>}
        {list.map((d) => {
          const on = dataset === d.id
          const open = openId === d.id
          return (
            <div key={d.id} className={`rounded-xl border p-3 ${on ? 'border-policy/40 bg-policy/[0.06]' : 'border-white/[0.08]'}`}>
              <div className="flex items-start justify-between gap-3">
                <button
                  type="button"
                  role="radio"
                  aria-checked={on}
                  disabled={busy}
                  onClick={() => !on && onPick(d.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <p className="text-[13px] font-medium text-slate-100">{d.title}</p>
                  <p className="num mt-0.5 break-all text-[11px] text-slate-500">{d.path}</p>
                  <p className="mt-1 text-[12px] text-slate-400">{d.description}</p>
                </button>
                <span
                  className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full ring-1 ${on ? 'bg-policy/30 ring-policy/60' : 'ring-white/20'}`}
                  aria-hidden
                >
                  {on && <span className="h-1.5 w-1.5 rounded-full bg-policy" />}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11.5px] text-slate-400">
                <span className="num">{d.rows} rows</span>
                <span className="num">{fmt.range(d.time_range)}</span>
                <IntegrityBadge integrity={d.integrity} />
                <button
                  type="button"
                  onClick={() => setOpenId(open ? null : d.id)}
                  aria-expanded={open}
                  className="ml-auto flex items-center gap-1 text-policy hover:underline"
                >
                  Preview rows <ChevronDown size={12} className={open ? 'rotate-180' : ''} aria-hidden />
                </button>
              </div>
              {open && <DatasetPreview id={d.id} />}
            </div>
          )
        })}
      </div>
    </Frame>
  )
}

function DatasetPreview({ id }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    api
      .inputDataset(id, 8)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e))
    return () => {
      cancelled = true
    }
  }, [id])

  if (error) return <p className="mt-3 text-[12px] text-block">The preview could not be loaded: {error.message}</p>
  if (!data)
    return (
      <p className="mt-3 flex items-center gap-2 text-[12px] text-slate-500">
        <Loader2 size={12} className="animate-spin" aria-hidden /> Loading rows from the file
      </p>
    )

  const cols = ['ts_iso', 'cell_id', ...data.fields, 'sim_fault_label']
  return (
    <div className="mt-3 space-y-3">
      <CsvPreview
        columns={cols}
        rows={data.preview}
        caption={`First ${data.preview.length} of ${data.rows} rows. Generated by ${data.generator}.`}
      />
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {data.fields.map((f) => (
          <div key={f} className="glass-quiet px-2.5 py-2">
            <dt className="label truncate">{f}</dt>
            <dd className="num mt-0.5 text-[11px] text-slate-300">
              {fmt.num(data.stats[f]?.min)} / {fmt.num(data.stats[f]?.mean)} / {fmt.num(data.stats[f]?.max)}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-[11px] text-slate-500">
        Statistics are min / mean / max. The <span className="num">sim_fault_label</span> column is the simulator's own
        label and is never shown to the agent.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// YOUR OWN CSV
// ---------------------------------------------------------------------------
function UploadPanel({ active, view, state, busy, onPick }) {
  const t = SOURCE_TYPE.upload
  const csvs = (view?.uploads ?? []).filter((u) => u.kind === 'csv')
  const usable = csvs.filter((u) => u.meta?.telemetry_compatible)
  const others = csvs.length - usable.length
  const chosen = state.data_source?.upload_id ?? null
  const snap = state.data_source?.upload
  const ready = Boolean(state.client_id && state.device_id)

  return (
    <Frame typeKey="upload" active={active}>
      <div className="min-w-0">
        <p className={`flex items-center gap-2 text-[14px] font-semibold ${t.text}`}>
          <FileSpreadsheet size={15} aria-hidden /> Your own CSV
        </p>
        <div className="mt-1.5">
          <TypeBadge typeKey="upload" />
        </div>
        <p className="mt-2 max-w-[80ch] text-[12.5px] leading-relaxed text-slate-400">
          Run the agent on a telemetry CSV you uploaded below. It needs the five telemetry columns and at least 100
          rows, and it is checked against the device selected above. Nothing in it is verified beyond its format.
        </p>
      </div>

      {!ready && (
        <p className="mt-3 text-[12.5px] text-slate-500">Choose a client and a device first, so the file can be checked against it.</p>
      )}
      {ready && usable.length === 0 && (
        <p className="mt-3 text-[12.5px] text-slate-500">
          No usable CSV yet. Upload one with the five telemetry columns in the Upload area below, then choose it here.
        </p>
      )}

      {usable.length > 0 && (
        <ul className="mt-3 divide-y divide-white/[0.06] rounded-xl border border-white/10" role="radiogroup" aria-label="Your CSV files">
          {usable.map((u) => {
            const on = active && chosen === u.id
            return (
              <li key={u.id} className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-[13px] text-slate-100">{u.filename}</p>
                  <p className="num text-[11.5px] text-slate-500">
                    {u.meta?.rows} rows · {u.meta?.columns?.length} columns
                    {on && snap ? ` · ${snap.time_from} to ${snap.time_to}` : ''}
                  </p>
                </div>
                <button
                  type="button"
                  role="radio"
                  aria-checked={on}
                  aria-label={`Use ${u.filename} as the data source`}
                  disabled={busy || !ready}
                  onClick={() => onPick(u.id)}
                  className={[
                    'shrink-0 rounded-full px-3 py-1 text-[12px] font-medium ring-1 transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                    on ? `${t.bg} ${t.text} ${t.ringOn}` : 'text-slate-300 ring-white/15 hover:bg-white/5',
                  ].join(' ')}
                >
                  {on ? 'In use' : 'Use this file'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {active && snap && (
        <p className="mt-3 text-[12px] leading-relaxed text-hold">
          {snap.assumed_time
            ? 'This file has no time column, so the readings are assumed to be one second apart. '
            : ''}
          {snap.has_fault_labels
            ? 'It carries simulator fault labels, so the simulated RSP server reacts to the fault.'
            : 'It has no simulator fault labels: the agent can detect and diagnose the fault, but the simulated RSP server is not told about it, so the fix cannot show as recovered.'}
        </p>
      )}
      {others > 0 && (
        <p className="mt-2 text-[11.5px] text-slate-600">
          {others} other CSV upload{others === 1 ? '' : 's'} do{others === 1 ? 'es' : ''} not have all five telemetry columns and cannot be used here.
        </p>
      )}
    </Frame>
  )
}
