import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, Inbox, MessageSquarePlus, RefreshCw, X } from 'lucide-react'
import { Notice, Panel, Spinner } from '../components/Primitives'
import { ReportRow } from '../components/ReportParts'
import ReportForm from '../components/ReportForm'
import ReportDetail from '../components/ReportDetail'
import { REPORT_STEPS, reportStatus } from '../data/constants'
import { api } from '../services/api'

/** How often an open Reports screen looks for a reply or a new report. */
const POLL_MS = 6000

/**
 * REPORTS — one screen, two roles, one clear split between what goes in and
 * what comes back.
 *
 *   INPUT   "New report": describe an issue, optionally tie it to a device and
 *           an incident that already exist in the dashboard.
 *   OUTPUT  CLIENT  "My reports": every report they have ever sent, each with its
 *                   status, the owner's acknowledgement and the owner's reply.
 *           OWNER   "Report inbox": every client's reports, with acknowledge,
 *                   resolve and links to the device, incident and telemetry.
 *
 * SENT -> ACKNOWLEDGED -> RESOLVED. The server decides who sees what; a client
 * is only ever sent their own reports.
 */
export default function Reports({
  isOwner,
  username,
  devices,
  incidents,
  draft,
  onDraftConsumed,
  focusId,
  onFocusConsumed,
  onOpenDevice,
  onOpenIncident,
  onOpenReasoning,
  onChanged,
}) {
  const [tab, setTab] = useState('list') // 'list' | 'new'
  const [reports, setReports] = useState(null)
  const [error, setError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [filter, setFilter] = useState('ALL')
  const [justSent, setJustSent] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [formKey, setFormKey] = useState(0)
  const [prefill, setPrefill] = useState(null)
  const detailRef = useRef(null)

  const load = useCallback(
    async (manual = false) => {
      if (manual) setRefreshing(true)
      try {
        const res = await (isOwner ? api.reports() : api.myReports())
        setReports(res.reports ?? [])
        setError(null)
      } catch (err) {
        setError(err)
      } finally {
        if (manual) setRefreshing(false)
      }
    },
    [isOwner],
  )

  // Load on open, then keep checking so a reply appears without a reload.
  useEffect(() => {
    load()
    const timer = setInterval(load, POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  // Arriving from "Report an issue" on an incident or device: open the form
  // with that device / incident already chosen.
  useEffect(() => {
    if (!draft) return
    setPrefill(draft)
    setFormKey((k) => k + 1)
    setTab('new')
    onDraftConsumed?.()
  }, [draft, onDraftConsumed])

  // Arriving from a report chip on an incident or device: open that report.
  useEffect(() => {
    if (!focusId) return
    setTab('list')
    setFilter('ALL')
    setSelectedId(focusId)
    load()
    onFocusConsumed?.()
  }, [focusId, onFocusConsumed, load])

  // On a wide screen the detail sits beside the list, so show the newest one.
  useEffect(() => {
    if (tab !== 'list' || selectedId || !reports?.length) return
    if (typeof window !== 'undefined' && window.matchMedia?.('(min-width: 1280px)').matches) {
      setSelectedId(reports[0].id)
    }
  }, [tab, selectedId, reports])

  const counts = useMemo(() => {
    const c = { ALL: reports?.length ?? 0 }
    REPORT_STEPS.forEach((s) => {
      c[s] = (reports ?? []).filter((r) => r.status === s).length
    })
    return c
  }, [reports])

  const visible = useMemo(
    () => (reports ?? []).filter((r) => filter === 'ALL' || r.status === filter),
    [reports, filter],
  )
  const selected = (reports ?? []).find((r) => r.id === selectedId) ?? null

  const open = (id) => {
    setSelectedId(id)
    if (typeof window !== 'undefined' && window.innerWidth < 1280) {
      requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    }
  }

  const handleSent = (report) => {
    setReports((prev) => [report, ...(prev ?? []).filter((r) => r.id !== report.id)])
    setFilter('ALL')
    setSelectedId(report.id)
    setJustSent(report.id)
    setTab('list')
    setPrefill(null)
    onChanged?.()
  }

  const handleChanged = (updated) => {
    setReports((prev) => (prev ?? []).map((r) => (r.id === updated.id ? updated : r)))
    onChanged?.()
  }

  const listLabel = isOwner ? 'Report inbox' : 'My reports'

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label="Reports"
          className="flex gap-1 rounded-xl bg-white/[0.03] p-1 ring-1 ring-white/10"
        >
          <TabButton active={tab === 'list'} onClick={() => setTab('list')} icon={Inbox}>
            {listLabel}
            {reports && <span className="num ml-1.5 text-[11px] text-slate-500">{counts.ALL}</span>}
          </TabButton>
          <TabButton active={tab === 'new'} onClick={() => setTab('new')} icon={MessageSquarePlus}>
            New report
          </TabButton>
        </div>

        {tab === 'list' && (
          <button
            type="button"
            onClick={() => load(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] text-slate-400 ring-1 ring-white/10 hover:bg-white/5 hover:text-slate-200 disabled:opacity-60"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} aria-hidden />
            Refresh
          </button>
        )}
      </div>

      {tab === 'new' && (
        <Panel title="New report" icon={MessageSquarePlus} meta={`as ${username}`}>
          <p className="mb-4 max-w-[64ch] text-[12.5px] leading-relaxed text-slate-400">
            Tell the owner what is wrong. Link it to a device or an incident so they can see exactly
            what the agent saw. Every report you send stays in{' '}
            <span className="text-slate-200">{listLabel}</span>, with the owner's reply.
          </p>
          <ReportForm
            key={formKey}
            username={username}
            devices={devices}
            incidents={incidents}
            initial={prefill}
            onSent={handleSent}
          />
        </Panel>
      )}

      {tab === 'list' && (
        <>
          {error && !reports && (
            <Notice tone="block" title="Reports are unavailable">
              {error.message}
            </Notice>
          )}
          {!error && reports === null && <Spinner label="Loading reports" />}

          {reports && reports.length === 0 && (
            <Notice
              title={isOwner ? 'No reports yet' : 'You have not sent any reports yet'}
              action={
                <button
                  type="button"
                  onClick={() => setTab('new')}
                  className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-agent ring-1 ring-agent/25 hover:bg-agent/10"
                >
                  <MessageSquarePlus size={13} aria-hidden />
                  {isOwner ? 'Write a report' : 'Send your first report'}
                </button>
              }
            >
              {isOwner
                ? 'When a client sends one it appears here, ready to acknowledge and resolve.'
                : 'Anything you send will stay here, so you can see when the owner acknowledges it and what they said.'}
            </Notice>
          )}

          {reports && reports.length > 0 && (
            <div className="grid gap-5 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
              <div className="space-y-3">
                <FilterBar filter={filter} onChange={setFilter} counts={counts} />

                {justSent && (
                  <div className="glass-quiet flex items-start gap-2.5 border border-pass/30 px-3.5 py-3">
                    <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-pass" aria-hidden />
                    <p className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-slate-300">
                      <span className="num text-slate-100">{justSent}</span> was sent.{' '}
                      {isOwner
                        ? 'It is in the inbox below.'
                        : 'It stays here, and its status updates as the owner responds.'}
                    </p>
                    <button
                      type="button"
                      onClick={() => setJustSent(null)}
                      className="rounded p-0.5 text-slate-500 hover:text-slate-300"
                      aria-label="Dismiss"
                    >
                      <X size={13} />
                    </button>
                  </div>
                )}

                {visible.length === 0 && (
                  <p className="px-1 py-6 text-center text-[13px] text-slate-500">
                    No {reportStatus(filter).label.toLowerCase()} reports.
                  </p>
                )}
                {visible.map((r) => (
                  <ReportRow
                    key={r.id}
                    report={r}
                    active={r.id === selectedId}
                    onOpen={open}
                    showClient={isOwner}
                  />
                ))}
              </div>

              <div ref={detailRef} className="scroll-mt-20">
                {selected ? (
                  <ReportDetail
                    report={selected}
                    isOwner={isOwner}
                    onChanged={handleChanged}
                    onOpenDevice={onOpenDevice}
                    onOpenIncident={onOpenIncident}
                    onOpenReasoning={onOpenReasoning}
                  />
                ) : (
                  <Panel title="Report">
                    <p className="text-[13px] text-slate-500">
                      {isOwner
                        ? 'Select a report to read it, respond, and follow it to the device and incident.'
                        : 'Select a report to see its status and what the owner said.'}
                    </p>
                  </Panel>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function TabButton({ active, onClick, icon: Icon, children }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={[
        'flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-[13px] font-medium transition-colors',
        active ? 'bg-agent/10 text-agent ring-1 ring-agent/25' : 'text-slate-400 hover:text-slate-200',
      ].join(' ')}
    >
      <Icon size={14} aria-hidden />
      <span className="flex items-center">{children}</span>
    </button>
  )
}

function FilterBar({ filter, onChange, counts }) {
  const options = [{ key: 'ALL', label: 'All' }, ...REPORT_STEPS.map((s) => ({ key: s, label: reportStatus(s).label }))]
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
      {options.map((o) => {
        const active = filter === o.key
        return (
          <button
            key={o.key}
            type="button"
            onClick={() => onChange(o.key)}
            aria-pressed={active}
            className={[
              'rounded-full px-3 py-1 text-[12px] transition-colors ring-1',
              active
                ? 'bg-agent/10 text-agent ring-agent/25'
                : 'text-slate-400 ring-white/10 hover:bg-white/5 hover:text-slate-200',
            ].join(' ')}
          >
            {o.label} <span className="num ml-0.5 text-[11px] opacity-70">{counts[o.key] ?? 0}</span>
          </button>
        )
      })}
    </div>
  )
}
