import { useEffect, useRef, useState } from 'react'
import { FileSpreadsheet, FileText, Image as ImageIcon, Loader2, Trash2, Upload } from 'lucide-react'
import { Badge, Panel } from '../Primitives'
import { api } from '../../services/api'
import { MAX_UPLOAD_BYTES, UPLOAD_KINDS, fmt } from '../../data/constants'
import { CsvPreview, TypeBadge } from './parts'

const ICONS = { screenshot: ImageIcon, csv: FileSpreadsheet, log: FileText }

/**
 * UPLOAD AREA: operator files, kept apart from the stored datasets.
 * Screenshots, CSVs and text/log files are saved under ./uploads (never ./data)
 * and stay with the client, device and incident chosen when they were added.
 */
export default function UploadSection({ view, resolved, uploading, onUpload, onRemove }) {
  const hasClient = Boolean(view?.state?.client_id)
  const [errors, setErrors] = useState({})
  const list = view?.uploads ?? []

  const send = async (kind, files) => {
    const file = files?.[0]
    if (!file) return
    setErrors((e) => ({ ...e, [kind]: null }))
    if (file.size > MAX_UPLOAD_BYTES) {
      setErrors((e) => ({ ...e, [kind]: `${file.name} is ${fmt.bytes(file.size)}. The limit is 10 MB.` }))
      return
    }
    const res = await onUpload(file, kind)
    if (!res.ok) setErrors((e) => ({ ...e, [kind]: res.message }))
  }

  return (
    <Panel title="Upload area" icon={Upload} meta="operator files, kept in uploads/">
      <p className="mb-4 max-w-[70ch] text-[12.5px] leading-relaxed text-slate-400">
        Add evidence for this input: a screenshot of the problem, a CSV of counters, or a log. Files are kept with{' '}
        {hasClient ? (
          <span className="text-slate-200">
            {resolved?.client?.name}
            {resolved?.device ? `, device ${fmt.shortId(resolved.device.euicc_id)}` : ''}
          </span>
        ) : (
          'the selected client'
        )}
        . They are not part of the stored datasets and are marked as unverified.
      </p>

      {!hasClient && <p className="mb-3 text-[12.5px] text-hold">Choose a client first so the files have somewhere to belong.</p>}

      <div className="grid gap-3 lg:grid-cols-3">
        {UPLOAD_KINDS.map((k) => (
          <Dropzone
            key={k.kind}
            spec={k}
            disabled={!hasClient || uploading !== null}
            busy={uploading === k.kind}
            error={errors[k.kind]}
            onFiles={(files) => send(k.kind, files)}
          />
        ))}
      </div>

      <div className="mt-6">
        <p className="label mb-2">Files kept for this client ({list.length})</p>
        {list.length === 0 && <p className="text-[12.5px] text-slate-500">Nothing uploaded yet.</p>}
        <ul className="space-y-2.5">
          {list.map((u) => (
            <UploadRow key={u.id} upload={u} onRemove={onRemove} />
          ))}
        </ul>
      </div>
    </Panel>
  )
}

function Dropzone({ spec, disabled, busy, error, onFiles }) {
  const inputRef = useRef(null)
  const [over, setOver] = useState(false)
  const Icon = ICONS[spec.kind]

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          if (!disabled) onFiles(e.dataTransfer.files)
        }}
        className={[
          'flex h-full flex-col items-start rounded-xl border border-dashed p-4 transition-colors',
          over ? 'border-hold/60 bg-hold/[0.06]' : 'border-white/15',
          disabled && !busy ? 'opacity-50' : '',
        ].join(' ')}
      >
        <p className="flex items-center gap-2 text-[13px] font-semibold text-slate-100">
          {busy ? <Loader2 size={15} className="animate-spin text-hold" aria-hidden /> : <Icon size={15} className="text-hold" aria-hidden />}
          {spec.label}
        </p>
        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">{spec.hint}</p>
        <button
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          className="mt-3 rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-hold ring-1 ring-hold/30 hover:bg-hold/10 disabled:cursor-not-allowed"
        >
          {busy ? 'Uploading' : 'Choose file'}
        </button>
        <p className="mt-2 text-[11px] text-slate-600">or drop it here · up to 10 MB</p>
        <input
          ref={inputRef}
          type="file"
          accept={spec.accept}
          className="sr-only"
          aria-label={`Upload ${spec.label}`}
          onChange={(e) => {
            onFiles(e.target.files)
            e.target.value = '' // allow choosing the same file again
          }}
        />
      </div>
      {error && (
        <p role="alert" className="mt-1.5 text-[12px] text-block">
          {error}
        </p>
      )}
    </div>
  )
}

function UploadRow({ upload: u, onRemove }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const Icon = ICONS[u.kind]
  const m = u.meta ?? {}

  return (
    <li className={`glass-quiet p-3 ${u.attached ? '' : 'opacity-60'}`}>
      <div className="flex items-start gap-3">
        {u.kind === 'screenshot' ? <Thumb id={u.id} name={u.filename} /> : (
          <span className="grid h-14 w-14 shrink-0 place-items-center rounded-lg bg-white/[0.04] text-slate-400">
            <Icon size={20} aria-hidden />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <p className="truncate text-[13px] font-medium text-slate-100" title={u.filename}>{u.filename}</p>
            <TypeBadge typeKey="upload" />
            {u.attached ? <Badge tone="pass">Part of this input</Badge> : <Badge tone="neutral">For another device or incident</Badge>}
          </div>
          <p className="num mt-1 text-[11px] text-slate-500">
            {u.id} · {fmt.bytes(u.size)} · {fmt.datetime(u.uploaded_at)} · {u.folder}/
          </p>
          <p className="num mt-0.5 text-[11px] text-slate-500">
            {u.client_id}
            {u.device_id ? ` · device ${fmt.shortId(u.device_id)}` : ' · no specific device'}
            {u.incident_id ? ` · ${u.incident_id}` : ''}
          </p>
          <p className="mt-1 text-[12px] text-slate-400">
            {u.kind === 'screenshot' && m.size_px && `${m.size_px.width} × ${m.size_px.height} px`}
            {u.kind === 'csv' && `${m.rows} rows · ${m.columns?.length} columns${m.telemetry_compatible ? ' · has all five telemetry columns' : ''}`}
            {u.kind === 'log' && `${m.lines} lines`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {u.kind !== 'screenshot' && (
            <button
              type="button"
              onClick={() => setOpen(!open)}
              aria-expanded={open}
              className="rounded-lg px-2.5 py-1 text-[12px] text-slate-300 ring-1 ring-white/10 hover:bg-white/5"
            >
              {open ? 'Hide' : 'Preview'}
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              await onRemove(u.id)
              setBusy(false)
            }}
            aria-label={`Remove ${u.filename}`}
            title="Remove this file"
            className="rounded-lg p-1.5 text-slate-500 hover:bg-block/10 hover:text-block disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Trash2 size={14} aria-hidden />}
          </button>
        </div>
      </div>

      {open && u.kind === 'csv' && (
        <div className="mt-3">
          <CsvPreview columns={m.columns} rows={m.preview} caption={`First ${m.preview?.length ?? 0} of ${m.rows} rows`} />
        </div>
      )}
      {open && u.kind === 'log' && (
        <pre className="num mt-3 max-h-56 overflow-auto rounded-lg border border-white/[0.07] bg-black/30 p-3 text-[11.5px] leading-relaxed text-slate-300">
          {(m.preview ?? []).join('\n')}
          {m.lines > (m.preview?.length ?? 0) ? `\n… ${m.lines - m.preview.length} more lines` : ''}
        </pre>
      )}
    </li>
  )
}

/** Screenshots are fetched with the session token, then shown from a blob URL. */
function Thumb({ id, name }) {
  const [url, setUrl] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    let made = null
    api
      .uploadBlobUrl(id)
      .then((u) => {
        made = u
        if (cancelled) URL.revokeObjectURL(u)
        else setUrl(u)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
      if (made) URL.revokeObjectURL(made)
    }
  }, [id])

  if (url) {
    return (
      <a href={url} target="_blank" rel="noreferrer" title="Open full size" className="shrink-0">
        <img src={url} alt={`Screenshot ${name}`} className="h-14 w-14 rounded-lg object-cover ring-1 ring-white/10" />
      </a>
    )
  }
  return (
    <span className="grid h-14 w-14 shrink-0 place-items-center rounded-lg bg-white/[0.04] text-slate-500">
      {failed ? <ImageIcon size={18} aria-hidden /> : <Loader2 size={16} className="animate-spin" aria-hidden />}
    </span>
  )
}
