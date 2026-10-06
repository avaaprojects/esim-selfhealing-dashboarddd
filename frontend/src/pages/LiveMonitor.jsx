import { Radio, Waves } from 'lucide-react'
import { Notice, Panel } from '../components/Primitives'
import { AnomalyChart, FeatureChart, dynamicWindow } from '../components/TelemetryChart'
import MetricCard from '../components/MetricCard'
import { fmt } from '../data/constants'

const COLORS = {
  aka_fail_rate: '#22D3EE',
  rsrp_dbm: '#F472B6',
  drop_rate: '#34D399',
  latency_ms: '#FBBF24',
  ota_fail_rate: '#8B5CF6',
}

/**
 * The five real telemetry channels plus the detector's own decision variable.
 *
 * Source is `monitor.readings` once a scenario has run; before that it is a
 * short healthy window produced by a throwaway detector, labelled as such.
 */
export default function LiveMonitor({ telemetry, error }) {
  const samples = telemetry?.samples ?? []
  const features = telemetry?.features ?? []
  const latest = samples.at(-1)
  const crossings = samples.filter((s) => s.triggered).length
  const windowWidth = Math.min(dynamicWindow(samples), samples.length)

  if (error) {
    return (
      <Notice tone="block" title="Telemetry is unavailable">
        The backend is not responding. Start it with{' '}
        <code className="num text-slate-300">python -m uvicorn backend.app:app --port 8000</code>.
      </Notice>
    )
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Anomaly score g_t"
          value={fmt.num(latest?.score, 2)}
          hint={`chi-square threshold ${fmt.num(latest?.threshold, 2)}`}
          meter={{ value: latest?.score ?? 0, max: Math.max(latest?.threshold ?? 1, latest?.score ?? 1), limit: latest?.threshold }}
          tone={(latest?.score ?? 0) > (latest?.threshold ?? Infinity) ? 'block' : 'pass'}
        />
        <MetricCard
          label="Drift statistic"
          value={fmt.num(latest?.drift_score, 2)}
          hint="gap between the fast and slow baselines — catches ramps the EWMA absorbs"
          tone={(latest?.drift_score ?? 0) > 12 ? 'block' : 'pass'}
        />
        <MetricCard
          label="Detector latency"
          value={fmt.ms(latest?.latency_ms)}
          hint="budget is 10 ms per sample"
        />
        <MetricCard
          label="Threshold crossings"
          value={crossings}
          hint={`${windowWidth} of ${samples.length} samples in view — the window narrows as the signal moves`}
          tone={crossings ? 'block' : 'pass'}
        />
      </div>

      <Panel
        title="Change-point detection"
        icon={Radio}
        meta={telemetry?.source === 'monitor.readings' ? 'live detector state' : 'healthy baseline window'}
      >
        <p className="mb-3 max-w-[70ch] text-[12.5px] leading-relaxed text-slate-400">
          The dashed line is the decision boundary itself: MONITOR opens an incident when g_t
          crosses it and the exceedance persists. Slow baseline drift is absorbed by the EWMA
          rather than paged on.
        </p>
        <AnomalyChart samples={samples} height={220} windowed />
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        {features.map((f) => {
          const v = latest?.features?.[f.key]
          const mu = latest?.baseline?.[f.key]
          const sd = latest?.band?.[f.key]
          // "within normal" is the same envelope the chart shades, which is
          // the detector's own baseline and band - not a second opinion.
          const inBand = typeof v === 'number' && typeof mu === 'number' && typeof sd === 'number'
            ? Math.abs(v - mu) <= 3 * sd
            : null
          return (
            <Panel key={f.key} title={f.label} icon={Waves} meta={f.unit} dense>
              <div className="mb-1 flex items-baseline gap-2">
                <span className="num text-xl font-semibold text-slate-100">
                  {fmt.num(v, f.precision)}
                </span>
                <span className="num text-[11px] text-slate-500">
                  z = {fmt.num(latest?.z_scores?.[f.key], 2)}
                </span>
                {typeof mu === 'number' && typeof sd === 'number' && (
                  <span className="num ml-auto text-[10.5px] text-slate-500">
                    nominal {fmt.num(mu - 3 * sd, f.precision)} to {fmt.num(mu + 3 * sd, f.precision)}
                    <span className={inBand ? 'ml-2 text-emerald-400' : 'ml-2 text-rose-400'}>
                      {inBand ? 'in range' : 'out of range'}
                    </span>
                  </span>
                )}
              </div>
              <FeatureChart
                samples={samples}
                featureKey={f.key}
                color={COLORS[f.key]}
                unit={f.unit}
                band
                windowed
              />
            </Panel>
          )
        })}
      </div>
    </div>
  )
}
