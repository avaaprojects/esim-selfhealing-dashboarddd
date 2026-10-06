import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api'
import { STAGES } from '../data/constants'

const STAGE_MS = 340
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * The Output screen's state, held above the router like the Input state so
 * going Input -> Output -> Input -> Output loses nothing.
 *
 *   idle        nothing asked for yet (Output was opened from the sidebar)
 *   loading     a result is being fetched
 *   processing  PROCESS DATA was pressed; the run is in flight
 *   ready       a processed input, with its incident, gate and recovery
 *   unavailable nothing processed yet, or the agent session it belonged to is gone
 *   error       processing failed
 *
 * Honesty note on the animation: a stored dataset runs through the loop in a
 * few tens of milliseconds, so the stage walk is a deliberate minimum display
 * time (same 340 ms per stage as the Run self-healing button), not a progress
 * bar for missing work. The result on screen is whatever the backend returned.
 */
export function useOutput() {
  const [phase, setPhase] = useState('idle')
  const [view, setView] = useState(null)
  const [stage, setStage] = useState(null)
  const [error, setError] = useState(null)
  const [incidentId, setIncidentId] = useState(null)
  const timers = useRef([])
  const startedAt = useRef(0)
  const alive = useRef(true)
  // Only the newest request may write. Opening the Output for a particular
  // incident (from the Incidents screen) starts a load while the screen is
  // mounting, and the mount would otherwise start a second, unfocused one:
  // whichever landed last would win, which could show a different run.
  const request = useRef(0)

  const clearTimers = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      clearTimers()
    }
  }, [])

  const beginProcessing = useCallback(() => {
    clearTimers()
    setPhase('processing')
    setError(null)
    setView(null)
    setIncidentId(null)
    startedAt.current = Date.now()
    setStage(STAGES[0].key)
    STAGES.forEach((s, i) => timers.current.push(setTimeout(() => setStage(s.key), i * STAGE_MS)))
  }, [])

  /** Read the current output. `id` picks one of the run's incidents. */
  const load = useCallback(async (id) => {
    const ticket = ++request.current
    const stale = () => !alive.current || ticket !== request.current
    try {
      const v = await api.outputCurrent(id ?? undefined)
      if (stale()) return null
      setView(v)
      setPhase(v.available ? 'ready' : 'unavailable')
      if (v.available) setIncidentId(v.selected_incident_id)
      setError(null)
      return v
    } catch (err) {
      if (stale()) return null
      setError(err)
      setPhase((p) => (p === 'processing' ? p : 'error'))
      return null
    }
  }, [])

  const finishProcessing = useCallback(async () => {
    await sleep(Math.max(0, STAGES.length * STAGE_MS - (Date.now() - startedAt.current)))
    clearTimers()
    if (alive.current) setStage(null)
    return load()
  }, [load])

  const failProcessing = useCallback((message) => {
    clearTimers()
    setStage(null)
    setError(new Error(message))
    setPhase('error')
  }, [])

  /** Show one incident of a run — including one opened from the Incidents
   * screen, which may belong to an earlier input than the latest. */
  /** Forget the current result. The view belongs to one agent session and one
   * signed-in operator, so signing out, signing in and resetting the session
   * must all drop it rather than leave a result on screen that no longer
   * exists. The next visit to the screen loads afresh. */
  const reset = useCallback(() => {
    request.current += 1 // any in-flight load is now stale
    clearTimers()
    setPhase('idle')
    setView(null)
    setStage(null)
    setError(null)
    setIncidentId(null)
  }, [])

  const select = useCallback(
    (id) => {
      setIncidentId(id)
      // Leave 'idle' synchronously so the screen's own first-load effect does
      // not fire a second, unfocused request alongside this one.
      setPhase((p) => (p === 'idle' ? 'loading' : p))
      return load(id)
    },
    [load],
  )

  return {
    phase,
    view,
    stage,
    error,
    incidentId,
    beginProcessing,
    finishProcessing,
    failProcessing,
    load,
    select,
    reset,
  }
}
