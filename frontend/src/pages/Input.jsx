import { useEffect } from 'react'
import { Notice, Spinner } from '../components/Primitives'
import ContextBanner from '../components/input/ContextBanner'
import ClientSection from '../components/input/ClientSection'
import DataSourceSection from '../components/input/DataSourceSection'
import UploadSection from '../components/input/UploadSection'
import ProvenanceTable from '../components/input/ProvenanceTable'
import ProcessBar from '../components/input/ProcessBar'

/** How often an open Input screen re-reads readiness (the real-time feed can be started from anywhere). */
const POLL_MS = 5000

/**
 * INPUT / CONFIGURATION — what data is entering the self-healing system.
 *
 * One screen, top to bottom: who the input is for, where the data comes from,
 * what the operator attached, and exactly what will be processed and where each
 * piece came from. The selection lives in `useInput` (above the router), is
 * saved per operator on the server, and is what PROCESS DATA freezes into a
 * snapshot for the output stage. Nothing on this screen runs the agent.
 */
export default function Input({ input, isOwner, realtime, incidents, onStartRealtime, onStopRealtime, onProcess, onOpenOutput }) {
  const { catalog, view, commit, busy, uploading, processing, error, patch, upload, removeUpload, refreshView } = input

  useEffect(() => {
    const timer = setInterval(refreshView, POLL_MS)
    return () => clearInterval(timer)
  }, [refreshView])

  // Starting or stopping the feed changes readiness straight away.
  useEffect(() => {
    if (catalog) refreshView()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [realtime?.running])

  if (error && !view) {
    return (
      <Notice tone="block" title="Input settings are unavailable">
        {error.message}
      </Notice>
    )
  }
  if (!view || !catalog) return <Spinner label="Loading input settings" />

  const doPatch = (p) => patch(p) // a refused change shows in the banner below

  // PROCESS DATA hands over to the Output screen (see App.handleProcess). If
  // processing fails, the Output screen says so and Back to input returns here.
  const doProcess = () => onProcess()

  return (
    <div className="space-y-5 pb-2">
      <div>
        <h2 className="text-[20px] font-semibold text-slate-50">What data is entering the self-healing system?</h2>
        <p className="mt-1 max-w-[72ch] text-[13px] leading-relaxed text-slate-400">
          Choose the client and device, decide whether the data is live or a stored synthetic dataset, and add anything
          you want the agent to have. Your choices are saved as you go.
        </p>
      </div>

      {error && (
        <Notice tone="block" title="That change did not go through">
          {error.message}
        </Notice>
      )}

      <ContextBanner view={view} catalog={catalog} />
      <ClientSection catalog={catalog} view={view} patch={doPatch} incidents={incidents} busy={busy} />
      <DataSourceSection
        catalog={catalog}
        view={view}
        realtime={realtime}
        patch={doPatch}
        busy={busy}
        isOwner={isOwner}
        onStartRealtime={onStartRealtime}
        onStopRealtime={onStopRealtime}
      />
      <UploadSection view={view} resolved={view.resolved} uploading={uploading} onUpload={upload} onRemove={removeUpload} />
      <ProvenanceTable view={view} />
      <ProcessBar
        view={view}
        commit={commit}
        processing={processing}
        message={null}
        onProcess={doProcess}
        onOpenOutput={onOpenOutput}
      />
    </div>
  )
}
