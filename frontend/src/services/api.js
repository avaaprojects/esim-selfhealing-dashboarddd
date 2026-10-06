/**
 * The only module in the frontend that knows about HTTP.
 *
 * Every component reads data through the hooks in src/hooks, which call these
 * functions. In dev, Vite proxies /api to FastAPI on port 8000 (see
 * vite.config.js), so the base URL is a bare relative path in both dev and in
 * the built app served by uvicorn.
 */

const BASE = import.meta.env.VITE_API_BASE ?? ''

//: DEMO ACCESS token, kept in memory + sessionStorage so a reload does not
//: force a re-login. Never sent anywhere except this origin's /api routes.
const TOKEN_KEY = 'esim_dashboard_token'
let _token = null
try {
  _token = sessionStorage.getItem(TOKEN_KEY)
} catch {
  /* sessionStorage unavailable (privacy mode, SSR) - session just won't persist */
}

export function setToken(token) {
  _token = token
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export function getToken() {
  return _token
}

//: Called once when the server rejects the session (the token expired, or the
//: backend restarted), so the app can return to the sign-in screen instead of
//: leaving every screen showing "unauthorized". Sign-in failures never route
//: here: those belong on the login form.
let onSessionLost = null
export function setSessionLostHandler(fn) {
  onSessionLost = fn
}

function noteRejectedSession(status, path) {
  if (status !== 401 || path.startsWith('/api/auth/')) return
  setToken(null)
  onSessionLost?.()
}

class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(path, options = {}) {
  let res
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }
  if (_token) headers.Authorization = `Bearer ${_token}`
  try {
    res = await fetch(`${BASE}${path}`, {
      ...options,
      headers,
    })
  } catch {
    throw new ApiError('Backend unreachable. Start it with: python -m uvicorn backend.app:app --port 8000', 0)
  }
  noteRejectedSession(res.status, path)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* response had no JSON body; the status line is the best message */
    }
    throw new ApiError(detail, res.status)
  }
  return res.json()
}

const get = (path) => request(path)
const post = (path, body) =>
  request(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

export const api = {
  health: () => get('/api/health'),
  status: () => get('/api/status'),
  telemetry: (limit = 120) => get(`/api/telemetry?limit=${limit}`),
  /** One device's telemetry; with `incidentId`, centred on the reading that
   * opened that incident (the response carries `marker_index`). */
  deviceTelemetry: ({ euiccId, incidentId, runId, limit = 120 } = {}) => {
    const q = new URLSearchParams({ limit })
    if (euiccId) q.set('euicc_id', euiccId)
    if (incidentId) q.set('incident_id', incidentId)
    if (runId) q.set('run_id', runId)
    return get(`/api/telemetry?${q}`)
  },
  incidents: () => get('/api/incidents'),
  incident: (id) => get(`/api/incidents/${encodeURIComponent(id)}`),
  incidentReports: (id) => get(`/api/incidents/${encodeURIComponent(id)}/reports`),
  devices: () => get('/api/devices'),
  device: (id) => get(`/api/devices/${encodeURIComponent(id)}`),
  trace: (id) => get(`/api/agent/trace${id ? `?incident_id=${encodeURIComponent(id)}` : ''}`),
  pipeline: () => get('/api/agent/pipeline'),
  memory: (opts = {}) => {
    const q = new URLSearchParams()
    if (opts.limit) q.set('limit', opts.limit)
    if (opts.incidentId) q.set('incident_id', opts.incidentId)
    const s = q.toString()
    return get(`/api/memory${s ? `?${s}` : ''}`)
  },
  learning: () => get('/api/learning'),
  safety: () => get('/api/safety'),
  actions: () => get('/api/actions'),
  simulation: () => get('/api/simulation'),

  runAgent: (scenario, enableLearning = true) =>
    post('/api/agent/run', { scenario, enable_learning: enableLearning }),
  runScenario: (scenario, enableLearning = true) =>
    post(`/api/simulation/${encodeURIComponent(scenario)}/run`, {
      scenario,
      enable_learning: enableLearning,
    }),
  reset: (enableLearning = true) =>
    post('/api/agent/reset', { enable_learning: enableLearning }),

  // -- auth --------------------------------------------------------------
  login: (username, password) => post('/api/auth/login', { username, password }),
  logout: () => post('/api/auth/logout'),
  me: () => get('/api/auth/me'),

  // -- REAL-TIME DATA ------------------------------------------------------
  realtimeStatus: () => get('/api/realtime/status'),
  realtimeStart: () => post('/api/realtime/start'),
  realtimeStop: () => post('/api/realtime/stop'),
  realtimeInject: (faultClass, euiccId, duration = 40) =>
    post('/api/realtime/inject', { fault_class: faultClass, euicc_id: euiccId, duration }),

  // -- CLIENT REPORTS ------------------------------------------------------
  // SENT -> ACKNOWLEDGED -> RESOLVED. A client only ever sees their own
  // reports (server-enforced); acknowledge / resolve / the full inbox are
  // owner-only.
  submitReport: ({ message, customer, deviceId, incidentId }) =>
    post('/api/reports', {
      message,
      customer: customer || null,
      device_id: deviceId || null,
      incident_id: incidentId || null,
    }),
  myReports: () => get('/api/reports/mine'),
  reports: (status) => get(`/api/reports${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  report: (id) => get(`/api/reports/${encodeURIComponent(id)}`),
  reportsSummary: () => get('/api/reports/summary'),
  acknowledgeReport: (id, note) =>
    post(`/api/reports/${encodeURIComponent(id)}/acknowledge`, { note: note || null }),
  resolveReport: (id, note) =>
    post(`/api/reports/${encodeURIComponent(id)}/resolve`, { note: note || null }),
}

// -- INPUT / CONFIGURATION ---------------------------------------------------
// Owner only. DATA (stored synthetic datasets) and UPLOADS (operator files)
// are separate on the server; the UI only ever talks to these endpoints.
async function parse(res) {
  try {
    noteRejectedSession(res.status, new URL(res.url).pathname)
  } catch {
    /* res.url may be relative in a test environment; the status check below still applies */
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* no JSON body */
    }
    throw new ApiError(detail, res.status)
  }
  return res.json()
}

const authHeaders = () => (_token ? { Authorization: `Bearer ${_token}` } : {})

/** The file's bytes are the request body; kind and name travel in the query. */
async function uploadFile(file, kind) {
  const q = new URLSearchParams({ kind, filename: file.name })
  let res
  try {
    res = await fetch(`${BASE}/api/uploads?${q}`, {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'application/octet-stream', ...authHeaders() },
      body: file,
    })
  } catch {
    throw new ApiError('Backend unreachable. Start it with: python -m uvicorn backend.app:app --port 8000', 0)
  }
  return parse(res)
}

/** Fetch an uploaded file with the session token and hand back a blob URL
 * (an <img> tag cannot send an Authorization header itself). */
async function uploadBlobUrl(id) {
  const res = await fetch(`${BASE}/api/uploads/${encodeURIComponent(id)}/file`, { headers: authHeaders() })
  if (!res.ok) throw new ApiError(`${res.status} ${res.statusText}`, res.status)
  return URL.createObjectURL(await res.blob())
}

Object.assign(api, {
  inputRegistry: () => get('/api/input/registry'),
  inputDataset: (id, rows = 12) => get(`/api/input/datasets/${encodeURIComponent(id)}?rows=${rows}`),
  inputState: () => get('/api/input/state'),
  patchInput: (patch) => request('/api/input/state', { method: 'PUT', body: JSON.stringify(patch) }),
  commitInput: () => post('/api/input/commit'),
  /** PROCESS DATA: freeze the input and run it through the real self-healing loop. */
  processInput: () => post('/api/input/process'),
  /** What was detected, decided and done for the latest processed input. */
  outputCurrent: (incidentId) =>
    get(`/api/output/current${incidentId ? `?incident_id=${encodeURIComponent(incidentId)}` : ''}`),
  latestInputCommit: () => get('/api/input/commits/latest'),
  deleteUpload: (id) => request(`/api/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  uploadFile,
  uploadBlobUrl,
})

export { ApiError }
