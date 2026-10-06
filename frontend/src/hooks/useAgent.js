import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api'
import { STAGES } from '../data/constants'

/** Generic fetch-with-state hook. `deps` re-runs it; `reloadKey` forces it. */
export function useResource(fetcher, deps = [], fallback = null) {
  const [data, setData] = useState(fallback)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetcher()
      if (alive.current) {
        setData(result)
        setError(null)
      }
    } catch (err) {
      if (alive.current) setError(err)
    } finally {
      if (alive.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    load()
  }, [load])

  return { data, error, loading, reload: load }
}

/**
 * Drives the RUN SELF-HEALING button and the agent-core animation.
 *
 * The backend run is synchronous and fast (tens of milliseconds for a single
 * scenario), so the stage walk here is a deliberate ~350 ms-per-stage reveal of
 * a run that has *already happened* — it is not a fake progress bar standing in
 * for missing work. Once the response lands, each stage's badge switches to the
 * real per-stage latency the orchestrator measured.
 */
export function useAgentRun({ onComplete } = {}) {
  const [stage, setStage] = useState(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const timers = useRef([])

  const clearTimers = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }

  useEffect(() => clearTimers, [])

  const run = useCallback(
    async (scenario, enableLearning = true) => {
      clearTimers()
      setRunning(true)
      setError(null)
      setStage(STAGES[0].key)

      STAGES.forEach((s, i) => {
        timers.current.push(setTimeout(() => setStage(s.key), i * 340))
      })
      const walkMs = STAGES.length * 340

      const started = Date.now()
      try {
        const payload = await api.runAgent(scenario, enableLearning)
        // Let the stage walk finish so the pipeline does not flash past.
        const remaining = Math.max(0, walkMs - (Date.now() - started))
        await new Promise((r) => setTimeout(r, remaining))
        setResult(payload)
        setStage(null)
        setRunning(false)
        onComplete?.(payload)
        return payload
      } catch (err) {
        clearTimers()
        setError(err)
        setStage(null)
        setRunning(false)
        throw err
      }
    },
    [onComplete],
  )

  return { run, running, stage, result, error }
}
