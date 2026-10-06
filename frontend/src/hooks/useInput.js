import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api'

/**
 * The Input / configuration state, held above the router so it survives
 * navigating away and back. The server is the source of truth: every change is
 * sent to it, saved per operator, and the derived view (readiness, provenance,
 * attached uploads) comes back with the response. The same selection is
 * therefore there after a page refresh or a backend restart.
 */
export function useInput(enabled) {
  const [catalog, setCatalog] = useState(null) // { registry, datasets }
  const [view, setView] = useState(null) // { state, resolved, readiness, provenance, uploads }
  const [commit, setCommit] = useState(null) // the last snapshot PROCESS DATA took
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(null)
  const [processing, setProcessing] = useState(false)
  const [error, setError] = useState(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const load = useCallback(async () => {
    try {
      const [cat, v, c] = await Promise.all([api.inputRegistry(), api.inputState(), api.latestInputCommit()])
      if (!alive.current) return
      setCatalog(cat)
      setView(v)
      setCommit(c.commit ?? null)
      setError(null)
    } catch (err) {
      if (alive.current) setError(err)
    }
  }, [])

  useEffect(() => {
    if (enabled) load()
  }, [enabled, load])

  /** Re-read the derived view (e.g. the real-time feed was started elsewhere). */
  const refreshView = useCallback(async () => {
    try {
      const [cat, v] = await Promise.all([api.inputRegistry(), api.inputState()])
      if (!alive.current) return
      setCatalog(cat)
      setView(v)
    } catch {
      /* the next poll will try again */
    }
  }, [])

  /** Change the selection. Resolves to the new view, or null (error is set). */
  const patch = useCallback(async (p) => {
    setBusy(true)
    try {
      const v = await api.patchInput(p)
      if (alive.current) {
        setView(v)
        setError(null)
      }
      return v
    } catch (err) {
      if (alive.current) setError(err)
      return null
    } finally {
      if (alive.current) setBusy(false)
    }
  }, [])

  /** Resolves to { ok: true } or { ok: false, message }. */
  const upload = useCallback(async (file, kind) => {
    setUploading(kind)
    try {
      const res = await api.uploadFile(file, kind)
      if (alive.current) setView(res.input)
      return { ok: true, upload: res.upload }
    } catch (err) {
      return { ok: false, message: err.message }
    } finally {
      if (alive.current) setUploading(null)
    }
  }, [])

  const removeUpload = useCallback(async (id) => {
    try {
      const res = await api.deleteUpload(id)
      if (alive.current) setView(res.input)
      return true
    } catch (err) {
      if (alive.current) setError(err)
      return false
    }
  }, [])

  const process = useCallback(async () => {
    setProcessing(true)
    try {
      // Freeze the input AND run it through the real self-healing loop.
      const res = await api.processInput()
      if (alive.current) {
        setCommit(res.commit)
        setError(null)
      }
      return { ok: true, commit: res.commit, run: res.run }
    } catch (err) {
      return { ok: false, message: err.message }
    } finally {
      if (alive.current) setProcessing(false)
    }
  }, [])

  return { catalog, view, commit, busy, uploading, processing, error, load, refreshView, patch, upload, removeUpload, process }
}
