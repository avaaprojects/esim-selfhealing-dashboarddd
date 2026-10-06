/**
 * Shapes used only when the backend is unreachable.
 *
 * These are NOT a parallel implementation of the agent. They exist so the UI
 * renders a readable, clearly-labelled empty state instead of a blank screen
 * while uvicorn is still starting. Every field name matches what
 * backend/services/serialization.py emits, so swapping real data in requires no
 * component changes. Anything derived from them is banner-flagged as offline.
 */

export const OFFLINE_STATUS = {
  mode: 'DEMO / SIMULATION',
  system_health: { state: 'UNKNOWN', headroom: null, score: null, threshold: null },
  panels: [
    { key: 'euicc', label: 'eUICC state', state: 'UNKNOWN', value: '—', detail: 'waiting for backend' },
    { key: 'network', label: 'Network', state: 'UNKNOWN', value: '—', detail: 'waiting for backend' },
    { key: 'auth', label: 'Authentication', state: 'UNKNOWN', value: '—', detail: 'waiting for backend' },
    { key: 'rsp', label: 'RSP / eSIM', state: 'UNKNOWN', value: '—', detail: 'waiting for backend' },
    { key: 'security', label: 'Security', state: 'UNKNOWN', value: '—', detail: 'waiting for backend' },
  ],
  counters: {
    incidents_total: 0,
    open_incidents: 0,
    auto_remediated: 0,
    human_in_loop: 0,
    escalations: 0,
    memory_records: 0,
    audit_entries: 0,
    policy_updates: 0,
  },
  last_scenario: null,
  last_run_at: null,
  latest_reading: null,
}

export const OFFLINE_TELEMETRY = {
  features: [
    { key: 'aka_fail_rate', label: 'AKA failure rate', unit: 'per 100 attach', precision: 2, worse: 'up' },
    { key: 'rsrp_dbm', label: 'RSRP', unit: 'dBm', precision: 1, worse: 'down' },
    { key: 'drop_rate', label: 'Session drop rate', unit: 'fraction', precision: 3, worse: 'up' },
    { key: 'latency_ms', label: 'OTA latency', unit: 'ms', precision: 0, worse: 'up' },
    { key: 'ota_fail_rate', label: 'OTA failure rate', unit: 'fraction', precision: 3, worse: 'up' },
  ],
  threshold: null,
  source: 'offline',
  samples: [],
}
