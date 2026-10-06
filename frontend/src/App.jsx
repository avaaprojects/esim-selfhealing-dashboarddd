import { useCallback, useEffect, useRef, useState } from 'react'
import DashboardLayout from './layouts/DashboardLayout'
import Login from './pages/Login'
import Overview from './pages/Overview'
import LiveMonitor from './pages/LiveMonitor'
import RealTime from './pages/RealTime'
import Incidents from './pages/Incidents'
import Reasoning from './pages/Reasoning'
import Actions from './pages/Actions'
import Safety from './pages/Safety'
import Memory from './pages/Memory'
import Learning from './pages/Learning'
import Simulation from './pages/Simulation'
import Reports from './pages/Reports'
import Devices from './pages/Devices'
import Input from './pages/Input'
import Output from './pages/Output'
import { Spinner } from './components/Primitives'
import DataSourceSwitch from './components/DataSourceSwitch'
import { api, getToken, setSessionLostHandler, setToken } from './services/api'
import { useAgentRun } from './hooks/useAgent'
import { useInput } from './hooks/useInput'
import { useOutput } from './hooks/useOutput'
import { OFFLINE_STATUS, OFFLINE_TELEMETRY } from './data/fallback'

/**
 * Single-page shell.
 *
 * State lives here rather than in a router so a run can refresh every screen at
 * once: the agent changes status, telemetry, incidents, memory, safety and the
 * policy in one pass, and they should all move together.
 *
 * The whole dashboard sits behind a DEMO ACCESS gate (`auth`, below): nothing
 * renders until a CLIENT or OWNER session token has been issued by the
 * backend, and every mutating call still enforces the role server-side —
 * hiding a button here is a UX convenience, not the actual boundary.
 */
export default function App() {
  // Everything the dashboard holds - incidents, devices, the input catalog, the
  // last result - belongs to one signed-in account. Signing out, or losing the
  // session, remounts the whole dashboard, so the next account starts from
  // nothing instead of briefly seeing the last account's data (which matters on
  // a shared computer: an owner's catalog names every company).
  const [generation, setGeneration] = useState(0)
  const [sessionEnded, setSessionEnded] = useState(false)
  const handleSignedOut = useCallback((ended) => {
    setSessionEnded(Boolean(ended))
    setGeneration((g) => g + 1)
  }, [])
  return <Dashboard key={generation} sessionEnded={sessionEnded} onSignedOut={handleSignedOut} />
}

function Dashboard({ sessionEnded, onSignedOut }) {
  const [auth, setAuth] = useState(null) // { token, role, username } | null
  const [authChecked, setAuthChecked] = useState(false)
  const [sessionLost, setSessionLost] = useState(Boolean(sessionEnded))
  const authRef = useRef(null)
  authRef.current = auth

  const [page, setPage] = useState('overview')

  const [status, setStatus] = useState(OFFLINE_STATUS)
  const [telemetry, setTelemetry] = useState(OFFLINE_TELEMETRY)
  const [incidents, setIncidents] = useState([])
  const [activity, setActivity] = useState([])
  const [memory, setMemory] = useState(null)
  const [learning, setLearning] = useState(null)
  const [safety, setSafety] = useState(null)
  const [actions, setActions] = useState(null)
  const [simulation, setSimulation] = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [realtime, setRealtime] = useState(null)
  const [devices, setDevices] = useState([])
  const [reportSummary, setReportSummary] = useState(null)
  // Cross-screen navigation for reports <-> incidents <-> devices.
  const [selectedDevice, setSelectedDevice] = useState(null)
  const [deviceFocusIncident, setDeviceFocusIncident] = useState(null)
  const [reportDraft, setReportDraft] = useState(null)
  const [focusReport, setFocusReport] = useState(null)

  const [selected, setSelected] = useState(null)
  const [trace, setTrace] = useState(null)
  const [loadingTrace, setLoadingTrace] = useState(false)

  const [error, setError] = useState(null)
  const [booting, setBooting] = useState(true)
  const [scenario, setScenario] = useState('isdp_corruption')
  const [runningKey, setRunningKey] = useState(null)

  const isOwner = auth?.role === 'owner'

  // INPUT / CONFIGURATION lives above the router so the selection survives
  // moving between screens; the server keeps it across refreshes and restarts.
  // Both roles use the Input and Output screens. The server limits a client to
  // their own company's record and results (services/tenancy.py).
  const input = useInput(Boolean(auth))
  // OUTPUT / self-healing result. Also above the router, so Back to input and
  // forward again lose nothing on either screen.
  const output = useOutput()
  const [injecting, setInjecting] = useState(false)

  // Restore a session from sessionStorage on first load, so a page reload
  // does not force a fresh sign-in for the 12h life of the demo token.
  useEffect(() => {
    const existing = getToken()
    if (!existing) {
      setAuthChecked(true)
      return
    }
    api
      .me()
      .then((me) => setAuth({ token: existing, ...me }))
      .catch(() => setToken(null))
      .finally(() => setAuthChecked(true))
  }, [])

  // A rejected session (the 12h token expired, or the backend restarted) goes back
  // to the sign-in screen, with a note, rather than leaving every screen erroring.
  useEffect(() => {
    setSessionLostHandler(() => {
      if (authRef.current) onSignedOut(true)
    })
    return () => setSessionLostHandler(null)
  }, [onSignedOut])

  const handleLogout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* token may already be gone server-side; still clear it locally */
    }
    setToken(null)
    onSignedOut(false)
  }, [onSignedOut])

  /** Pull everything the screens read. Called on boot and after every run. */
  const refresh = useCallback(async () => {
    try {
      const [s, t, inc, mem, learn, safe, act, sim, pipe, rt, dev, rsum] = await Promise.all([
        api.status(),
        api.telemetry(160),
        api.incidents(),
        api.memory({ limit: 40 }),
        api.learning(),
        api.safety(),
        api.actions(),
        api.simulation(),
        api.pipeline(),
        api.realtimeStatus(),
        api.devices().catch(() => ({ devices: [] })),
        api.reportsSummary().catch(() => null),
      ])
      setStatus(s)
      setTelemetry(t)
      setIncidents(inc.incidents ?? [])
      setMemory(mem)
      setLearning(learn)
      setSafety(safe)
      setActions(act)
      setSimulation(sim)
      setPipeline(pipe)
      setRealtime(rt)
      setDevices(dev.devices ?? [])
      setReportSummary(rsum)
      setError(null)
      return inc.incidents ?? []
    } catch (err) {
      setError(err)
      return []
    } finally {
      setBooting(false)
    }
  }, [])

  // Only start pulling data once a session exists — every route but /auth/*
  // 401s otherwise.
  useEffect(() => {
    if (auth) refresh()
  }, [auth, refresh])

  // REAL-TIME DATA: while the feed is running, poll so the dashboard visibly
  // shows samples arriving continuously rather than only updating on demand.
  // A plain interval rather than SSE/WebSocket — simpler and stable, which
  // the brief explicitly allows ("a reliable polling implementation is also
  // acceptable if it is simpler and more stable").
  const pollRef = useRef(null)
  useEffect(() => {
    if (!auth) return undefined
    clearInterval(pollRef.current)
    if (realtime?.running) {
      pollRef.current = setInterval(refresh, 1500)
    }
    return () => clearInterval(pollRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, realtime?.running, refresh])

  useEffect(() => {
    // Derive the feed from the incident list so it stays in step with it.
    setActivity(
      incidents.map((inc) => ({
        ts: inc.resolved_at ?? inc.opened_at,
        kind: 'incident',
        title: `${inc.incident_id} → ${inc.status}`,
        detail: `${inc.fault_label} · ${inc.selected_label ?? 'no feasible action'}`,
        incident_id: inc.incident_id,
      })),
    )
  }, [incidents])

  /** Load one incident's full decision trace. */
  const loadTrace = useCallback(async (incidentId) => {
    if (!incidentId) return
    setLoadingTrace(true)
    try {
      const [t, mem] = await Promise.all([
        api.incident(incidentId),
        api.memory({ limit: 40, incidentId }),
      ])
      setTrace(t)
      setMemory(mem)
      setError(null)
    } catch (err) {
      setError(err)
      setTrace(null)
    } finally {
      setLoadingTrace(false)
    }
  }, [])

  const openIncident = useCallback(
    (incidentId) => {
      setSelected(incidentId)
      loadTrace(incidentId)
      setPage((p) => (p === 'incidents' || p === 'reasoning' ? p : 'incidents'))
    },
    [loadTrace],
  )

  /** Cheap refresh after a report changes: counts on incidents, devices, sidebar. */
  const refreshLinks = useCallback(async () => {
    try {
      const [inc, dev, rsum] = await Promise.all([api.incidents(), api.devices(), api.reportsSummary()])
      setIncidents(inc.incidents ?? [])
      setDevices(dev.devices ?? [])
      setReportSummary(rsum)
    } catch {
      /* the next full refresh will catch up */
    }
  }, [])

  const openDevice = useCallback((euiccId, incidentId = null) => {
    if (!euiccId) return
    setSelectedDevice(euiccId)
    setDeviceFocusIncident(incidentId)
    setPage('devices')
  }, [])

  const openReasoning = useCallback(
    (incidentId) => {
      setSelected(incidentId)
      loadTrace(incidentId)
      setPage('reasoning')
    },
    [loadTrace],
  )

  /** Open the Output screen, optionally on a particular incident of that run. */
  const openOutput = useCallback(
    (incidentId = null) => {
      setPage('output')
      if (incidentId) output.select(incidentId)
    },
    [output],
  )

  const openReport = useCallback((reportId) => {
    setFocusReport(reportId)
    setPage('reports')
  }, [])

  const reportIssue = useCallback(({ deviceId, incidentId } = {}) => {
    setReportDraft({ deviceId: deviceId ?? '', incidentId: incidentId ?? '' })
    setPage('reports')
  }, [])

  const clearDraft = useCallback(() => setReportDraft(null), [])
  const clearFocus = useCallback(() => setFocusReport(null), [])

  const { run, running, stage } = useAgentRun({
    onComplete: async (payload) => {
      const list = await refresh()
      const first = payload?.records?.[0]?.incident?.incident_id ?? list[0]?.incident_id
      if (first) {
        setSelected(first)
        loadTrace(first)
      }
      setRunningKey(null)
    },
  })

  /** Run a built-in scenario and show it on the Output screen, the same way
   *  Process data does: the stage animation, then the full result. */
  const handleRun = useCallback(
    async (key) => {
      const target = key ?? scenario
      setScenario(target)
      setRunningKey(target)
      output.beginProcessing()
      setPage('output')
      try {
        await run(target, true)
      } catch (err) {
        setRunningKey(null)
        output.failProcessing(err?.message || 'The scenario could not be run.')
        // useAgentRun surfaces the error; refresh so the UI reflects reality.
        refresh()
        return
      }
      await output.finishProcessing()
      refresh()
    },
    [run, scenario, refresh, output],
  )

  const handleReset = useCallback(async () => {
    try {
      await api.reset(true)
      setSelected(null)
      setTrace(null)
      // The agent session is new: incidents, devices and the last self-healing
      // output it produced are all gone, so nothing stale is left on screen.
      output.reset()
      await refresh()
    } catch (err) {
      setError(err)
    }
  }, [refresh, output])

  const handleRealtimeStart = useCallback(async () => {
    try {
      await api.realtimeStart()
      await refresh()
    } catch (err) {
      setError(err)
    }
  }, [refresh])

  const handleRealtimeStop = useCallback(async () => {
    try {
      await api.realtimeStop()
      await refresh()
    } catch (err) {
      setError(err)
    }
  }, [refresh])

  const handleRealtimeInject = useCallback(
    async (faultClass, euiccId) => {
      try {
        await api.realtimeInject(faultClass, euiccId)
        await refresh()
      } catch (err) {
        setError(err)
      }
    },
    [refresh],
  )

  /**
   * PROCESS DATA. Switch to the full-screen Output page straight away (the
   * INPUT -> PROCESSING step), then run the committed input through the real
   * loop and load what it produced. The Input selection is not touched, so
   * Back to input finds it exactly as it was.
   */
  const handleProcess = useCallback(async () => {
    output.beginProcessing()
    setPage('output')
    const res = await input.process()
    if (!res.ok) {
      output.failProcessing(res.message)
      return
    }
    await output.finishProcessing()
    refresh() // incidents, devices, counters: the other screens now know about this run
  }, [input, output, refresh])

  /** Demo only: inject a known synthetic fault into the live feed for this input's device. */
  const handleOutputInject = useCallback(
    async (faultClass, euiccId) => {
      setInjecting(true)
      try {
        await api.realtimeInject(faultClass, euiccId)
        await output.load(output.incidentId)
      } catch (err) {
        setError(err)
      } finally {
        setInjecting(false)
      }
    },
    [output],
  )

  const lastRecord = trace ?? null

  const pages = {
    input: (
      <Input
        input={input}
        isOwner={isOwner}
        realtime={realtime}
        incidents={incidents}
        onStartRealtime={handleRealtimeStart}
        onStopRealtime={handleRealtimeStop}
        onProcess={handleProcess}
        onOpenOutput={() => openOutput()}
      />
    ),
    overview: (
      <Overview
        status={status}
        telemetry={telemetry}
        incidents={incidents}
        activity={activity}
        stage={stage}
        running={running}
        lastRecord={lastRecord}
        onOpenIncident={openIncident}
        error={error}
      />
    ),
    monitor: <LiveMonitor telemetry={telemetry} error={error} />,
    realtime: (
      <RealTime
        realtime={realtime}
        status={status}
        telemetry={telemetry}
        incidents={incidents}
        activity={activity}
        isOwner={isOwner}
        onStart={handleRealtimeStart}
        onStop={handleRealtimeStop}
        onInject={handleRealtimeInject}
        faultClasses={pipeline?.fault_classes ?? []}
        error={error}
      />
    ),
    incidents: (
      <Incidents
        incidents={incidents}
        selected={selected}
        trace={trace}
        loadingTrace={loadingTrace}
        onSelect={openIncident}
        error={error}
        onOpenReport={openReport}
        onOpenDevice={openDevice}
        onReportIssue={reportIssue}
        onOpenOutput={openOutput}
        isOwner={isOwner}
      />
    ),
    reasoning: (
      <Reasoning
        trace={trace}
        loading={loadingTrace}
        incidents={incidents}
        selected={selected}
        onSelect={openIncident}
      />
    ),
    actions: <Actions actions={actions} trace={trace} loading={booting} error={error} />,
    safety: <Safety safety={safety} loading={booting} error={error} />,
    memory: <Memory memory={memory} loading={booting} error={error} selectedIncident={selected} />,
    learning: <Learning learning={learning} loading={booting} error={error} />,
    devices: (
      <Devices
        devices={devices}
        selectedId={selectedDevice}
        focusIncident={deviceFocusIncident}
        onSelect={(id) => {
          setSelectedDevice(id)
          setDeviceFocusIncident(null)
        }}
        onOpenIncident={openIncident}
        onOpenReport={openReport}
        onReportIssue={reportIssue}
        error={error}
      />
    ),
    reports: (
      <Reports
        isOwner={isOwner}
        username={auth?.username}
        devices={devices}
        incidents={incidents}
        draft={reportDraft}
        onDraftConsumed={clearDraft}
        focusId={focusReport}
        onFocusConsumed={clearFocus}
        onOpenDevice={openDevice}
        onOpenIncident={openIncident}
        onOpenReasoning={openReasoning}
        onChanged={refreshLinks}
      />
    ),
    simulation: (
      <Simulation
        simulation={simulation}
        loading={booting}
        error={error}
        onRun={handleRun}
        running={running}
        runningKey={runningKey}
        stage={stage}
        onOpenIncident={openIncident}
        onReset={handleReset}
        isOwner={isOwner}
      />
    ),
  }

  if (!authChecked) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner label="Checking session" />
      </div>
    )
  }

  if (!auth) {
    return (
      <Login
        notice={sessionLost ? 'Your session ended. Please sign in again.' : null}
        onAuthenticated={(result) => {
          setSessionLost(false)
          setAuth(result)
        }}
      />
    )
  }

  // OUTPUT is its own full screen: no sidebar, and never beside or below Input.
  if (page === 'output') {
    return (
      <Output
        output={output}
        pending={{
          resolved: input.view?.resolved,
          source:
            { realtime: 'REAL-TIME', upload: 'OPERATOR UPLOAD' }[input.view?.state?.data_source?.mode] ?? 'SYNTHETIC / SIMULATED',
        }}
        onBack={(runMode) => setPage(runMode === 'scenario' ? 'overview' : 'input')}
        onOpenDashboard={() => setPage('overview')}
        faultClasses={pipeline?.fault_classes ?? []}
        onInject={isOwner ? handleOutputInject : undefined}
        injecting={injecting}
        onOpenIncident={openIncident}
        onOpenReasoning={openReasoning}
        onOpenDevice={openDevice}
        onReportIssue={reportIssue}
      />
    )
  }

  return (
    <DashboardLayout
      page={page}
      onNavigate={setPage}
      onRun={() => handleRun()}
      running={running}
      counters={status?.counters}
      role={auth.role}
      username={auth.username}
      onLogout={handleLogout}
      reportSummary={reportSummary}
    >
      {page === 'overview' ? (
        <OverviewWithSource>{pages[page]}</OverviewWithSource>
      ) : (
        pages[page]
      )}
    </DashboardLayout>
  )

  /** Injects the DATA SOURCE switch above Overview without threading extra
   * props through the `pages` map for every other screen. */
  function OverviewWithSource({ children }) {
    return (
      <div className="space-y-5">
        <DataSourceSwitchBar
          active={realtime?.running ? 'realtime' : 'simulation'}
          onSelect={(mode) => setPage(mode === 'realtime' ? 'realtime' : 'simulation')}
        />
        {children}
      </div>
    )
  }
}

function DataSourceSwitchBar({ active, onSelect }) {
  return (
    <div className="glass p-4">
      <p className="label mb-3">Data source</p>
      <DataSourceSwitch active={active} onSelect={onSelect} />
    </div>
  )
}
