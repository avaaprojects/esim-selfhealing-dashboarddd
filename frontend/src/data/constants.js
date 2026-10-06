/**
 * Presentation-side mirrors of values the backend also emits.
 *
 * These are colour and copy decisions only. Every number shown in the UI comes
 * from the API; nothing here substitutes for backend data.
 */

export const STAGES = [
  { key: 'observe', label: 'OBSERVE', blurb: 'EWMA baseline, chi-square change point' },
  { key: 'reason', label: 'REASON', blurb: 'ReAct loop over read-only RSP tools' },
  { key: 'plan', label: 'PLAN', blurb: 'Constrained multi-criteria ranking' },
  { key: 'safety', label: 'SAFETY', blurb: 'Four clauses of the envelope' },
  { key: 'act', label: 'ACT', blurb: 'Signed dispatch to the RSP endpoint' },
  { key: 'verify', label: 'VERIFY', blurb: 'Outcome check and rollback' },
  { key: 'learn', label: 'LEARN', blurb: 'PPO-Lagrangian, off the critical path' },
]

/** Terminal branch of one loop iteration — schemas.Status. */
export const STATUS_STYLE = {
  'AUTO-REMEDIATED': {
    text: 'text-pass',
    bg: 'bg-pass/10',
    ring: 'ring-pass/30',
    dot: 'bg-pass',
    label: 'Auto-remediated',
  },
  'HUMAN-IN-LOOP': {
    text: 'text-hold',
    bg: 'bg-hold/10',
    ring: 'ring-hold/30',
    dot: 'bg-hold',
    label: 'Awaiting operator',
  },
  ESCALATION: {
    text: 'text-block',
    bg: 'bg-block/10',
    ring: 'ring-block/30',
    dot: 'bg-block',
    label: 'Escalated to NOC',
  },
}

export const statusStyle = (s) =>
  STATUS_STYLE[s] ?? {
    text: 'text-slate-300',
    bg: 'bg-white/5',
    ring: 'ring-white/10',
    dot: 'bg-slate-400',
    label: s ?? 'Unknown',
  }

/** Fault classes carry a colour so belief charts stay legible across screens. */
export const FAULT_COLOR = {
  nominal: '#64748B',
  isdp_corruption: '#22D3EE',
  smdp_session_outage: '#8B5CF6',
  radio_degradation: '#F472B6',
  key_desync: '#FBBF24',
}

export const PANEL_STATE_STYLE = {
  NOMINAL: 'text-pass',
  HEALTHY: 'text-pass',
  'POST-QUANTUM': 'text-pass',
  DEGRADED: 'text-hold',
  'STUB SIGNER': 'text-hold',
  FAULT: 'text-block',
}

export const fmt = {
  pct: (v, digits = 0) =>
    v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`,
  num: (v, digits = 2) =>
    v === null || v === undefined ? '—' : Number(v).toFixed(digits),
  ms: (v) => {
    if (v === null || v === undefined) return '—'
    if (v >= 1000) return `${(v / 1000).toFixed(2)} s`
    if (v < 1) return `${v.toFixed(2)} ms`
    return `${v.toFixed(1)} ms`
  },
  clock: (ts) =>
    ts ? new Date(ts * 1000).toLocaleTimeString([], { hour12: false }) : '—',
  datetime: (ts) =>
    ts
      ? new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
      : '—',
  bytes: (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`),
  range: (r) =>
    r?.from
      ? `${fmt.datetime(r.from)} to ${r.to ? fmt.datetime(r.to) : 'now'}`
      : '—',
  datetimeSec: (ts) =>
    ts
      ? new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' })
      : '—',
  /** eUICC ids are 17 digits; the rest of the UI shows the last 7. */
  shortId: (id) => (id ? id.slice(-7) : '—'),
}

/**
 * Client report lifecycle — mirrors backend/services/reports.py.
 *   SENT -> ACKNOWLEDGED -> RESOLVED
 * `done` styles the completed step marker in the progress strip.
 */
export const REPORT_STEPS = ['SENT', 'ACKNOWLEDGED', 'RESOLVED']

export const REPORT_STATUS = {
  SENT: {
    label: 'Sent',
    tone: 'hold',
    dot: 'bg-hold',
    done: 'bg-hold/15 text-hold ring-hold/30',
    waiting: 'Waiting for the owner to acknowledge',
  },
  ACKNOWLEDGED: {
    label: 'Acknowledged',
    tone: 'agent',
    dot: 'bg-agent',
    done: 'bg-agent/15 text-agent ring-agent/30',
    waiting: 'Not acknowledged yet',
  },
  RESOLVED: {
    label: 'Resolved',
    tone: 'pass',
    dot: 'bg-pass',
    done: 'bg-pass/15 text-pass ring-pass/30',
    waiting: 'Not resolved yet',
  },
}

export const reportStatus = (s) =>
  REPORT_STATUS[s] ?? { label: s ?? 'Unknown', tone: 'neutral', dot: 'bg-slate-400', done: '', waiting: '' }

/** Device state as computed by AgentSession.devices(). */
export const DEVICE_STATE = {
  FAULT: { label: 'Fault', tone: 'block', dot: 'bg-block' },
  RECOVERED: { label: 'Recovered', tone: 'agent', dot: 'bg-agent' },
  NOMINAL: { label: 'Nominal', tone: 'pass', dot: 'bg-pass' },
}

export const deviceState = (s) =>
  DEVICE_STATE[s] ?? { label: s ?? 'Unknown', tone: 'neutral', dot: 'bg-slate-400' }

/** "1 Client Report" / "2 Client Reports". */
export const clientReports = (n) => `${n} Client Report${n === 1 ? '' : 's'}`

/**
 * Where a piece of input came from. Three kinds, three colours, never mixed:
 * cyan = the live feed, violet = stored synthetic data, amber = an operator's
 * own file (unverified).
 */
export const SOURCE_TYPE = {
  realtime: { label: 'REAL-TIME', tone: 'agent', ring: 'border-agent/40', ringOn: 'ring-agent/40', text: 'text-agent', bg: 'bg-agent/[0.05]' },
  synthetic: { label: 'SYNTHETIC / SIMULATED', tone: 'policy', ring: 'border-policy/40', ringOn: 'ring-policy/40', text: 'text-policy', bg: 'bg-policy/[0.05]' },
  upload: { label: 'OPERATOR UPLOAD', tone: 'hold', ring: 'border-hold/40', ringOn: 'ring-hold/40', text: 'text-hold', bg: 'bg-hold/[0.05]' },
}
/** A stored dataset the operator imported with tools/import_dataset.py is shown
 *  as operator-supplied, not as synthetic: it carries its own provenance label. */
export const IMPORT_LABEL = 'OPERATOR IMPORT'

/** A stable accent per client so the selected one is unmistakable. */
export const CLIENT_ACCENTS = ['#22D3EE', '#F472B6', '#FBBF24', '#34D399']

export const UPLOAD_KINDS = [
  { kind: 'screenshot', label: 'Screenshot / image', hint: 'PNG, JPEG, GIF or WebP of a network or device problem', accept: 'image/png,image/jpeg,image/gif,image/webp,.png,.jpg,.jpeg,.gif,.webp' },
  { kind: 'csv', label: 'CSV', hint: 'A table with a header row, e.g. counters or KPIs', accept: '.csv,text/csv' },
  { kind: 'log', label: 'Text / log', hint: 'A .txt, .log or .out file, e.g. a modem or agent log', accept: '.txt,.log,.out,text/plain' },
]
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024

/** Presentation copy for the five telemetry channels (units come from schemas.FEATURE_NAMES). */
export const FEATURE_LABELS = {
  aka_fail_rate: { label: 'AKA failure rate', unit: 'per 100 attach' },
  rsrp_dbm: { label: 'RSRP', unit: 'dBm' },
  drop_rate: { label: 'Session drop rate', unit: 'fraction' },
  latency_ms: { label: 'OTA latency', unit: 'ms' },
  ota_fail_rate: { label: 'OTA failure rate', unit: 'fraction' },
}

/** The three ways a gated action can leave the loop. */
export const GATE_ROUTE = {
  'auto-dispatch': 'Auto-dispatched',
  'operator-queue': 'Operator approval queue',
  'noc-escalation': 'Escalated to the NOC',
}

/** State of one stage in the INPUT -> ... -> RECOVERY flow. */
export const FLOW_STATE = {
  done: { dot: 'bg-pass', ring: 'ring-pass/40', text: 'text-pass', bg: 'bg-pass/10', label: 'Done' },
  active: { dot: 'bg-agent animate-corepulse', ring: 'ring-agent/50', text: 'text-agent', bg: 'bg-agent/10', label: 'In progress' },
  attention: { dot: 'bg-hold', ring: 'ring-hold/40', text: 'text-hold', bg: 'bg-hold/10', label: 'Needs attention' },
  blocked: { dot: 'bg-block', ring: 'ring-block/40', text: 'text-block', bg: 'bg-block/10', label: 'Blocked' },
  skipped: { dot: 'bg-slate-600', ring: 'ring-white/10', text: 'text-slate-500', bg: 'bg-white/[0.03]', label: 'Not needed' },
  pending: { dot: 'bg-slate-500', ring: 'ring-white/10', text: 'text-slate-400', bg: 'bg-white/[0.03]', label: 'Pending' },
}

/**
 * ESTATE NAMING. Every screen names a device the same way, from the registry
 * context the backend joins onto devices, incidents and reports (`context`).
 * A device that is not in the registry keeps its bare eUICC id rather than
 * being given a made-up client.
 */
export const estate = {
  /** "Yard tracker Y-48213", else the short eUICC id. */
  device: (context, euiccId) => context?.device_label ?? fmt.shortId(euiccId) ?? 'Unknown device',
  /** "Meridian Logistics · Depot trackers", else null. */
  client: (context) =>
    context?.client_name
      ? [context.client_name, context.group_name].filter(Boolean).join(' · ')
      : null,
  /** One line: "Meridian Logistics · Yard tracker Y-48213". */
  line: (context, euiccId) =>
    [context?.client_name, estate.device(context, euiccId)].filter(Boolean).join(' · '),
}
